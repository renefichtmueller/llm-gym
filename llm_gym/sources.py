"""A broad, keyless source net for the collector.

Each connector returns the same record shape and is wrapped so one dead source
never kills a run. Grouped into categories so the collector can cast a wide net
automatically — universities/research and news agencies included:

  academic   OpenAlex, Crossref, arXiv, Semantic Scholar, Europe PMC, DOAJ
  news       GDELT (global news agencies), Wikinews
  reference  Wikipedia
  tech       Hacker News

None of these need an API key. A record:
  {title, abstract, url, source, year, authors}
Records with an empty abstract but a url are fetched + extracted by the collector.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict

import httpx

from . import academic


# academic.* return Paper dataclasses; the collector wants plain dicts.
def search_openalex(query: str, limit: int = 8) -> list[dict]:
    return [asdict(p) for p in academic.search_openalex(query, limit)]


def search_arxiv(query: str, limit: int = 8) -> list[dict]:
    return [asdict(p) for p in academic.search_arxiv(query, limit)]


def search_semantic_scholar(query: str, limit: int = 8) -> list[dict]:
    return [asdict(p) for p in academic.search_semantic_scholar(query, limit)]

_UA = "llm-gym-collector/0.1 (research; +https://github.com/)"
_TIMEOUT = 20


def _rec(title="", abstract="", url="", source="", year=None, authors=None) -> dict:
    return {"title": title or "", "abstract": abstract or "", "url": url or "",
            "source": source, "year": year, "authors": authors or []}


# --- universities / research ---------------------------------------------------
def search_crossref(query: str, limit: int = 8) -> list[dict]:
    import re
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        data = c.get("https://api.crossref.org/works", params={
            "query": query, "rows": limit,
            "select": "title,abstract,author,issued,URL"}).json()
    out = []
    for it in data.get("message", {}).get("items", []):
        title = (it.get("title") or [""])[0]
        abstract = re.sub(r"<[^>]+>", "", it.get("abstract", "") or "")  # strip JATS
        year = (it.get("issued", {}).get("date-parts", [[None]])[0] or [None])[0]
        authors = [f"{a.get('given','')} {a.get('family','')}".strip()
                   for a in (it.get("author") or [])[:5]]
        out.append(_rec(title, abstract, it.get("URL", ""), "academic:crossref",
                        year, authors))
    return out


def search_europepmc(query: str, limit: int = 8) -> list[dict]:
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        data = c.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                     params={"query": query, "format": "json", "pageSize": limit,
                             "resultType": "core"}).json()
    out = []
    for r in data.get("resultList", {}).get("result", []):
        doi = r.get("doi")
        url = f"https://doi.org/{doi}" if doi else r.get("fullTextUrlList", {}) and ""
        out.append(_rec(r.get("title", ""), r.get("abstractText", ""),
                        url or "", "academic:europepmc",
                        int(r["pubYear"]) if r.get("pubYear", "").isdigit() else None,
                        (r.get("authorString", "") or "").split(", ")[:5]))
    return out


def search_doaj(query: str, limit: int = 8) -> list[dict]:
    from urllib.parse import quote
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        data = c.get(f"https://doaj.org/api/search/articles/{quote(query)}",
                     params={"pageSize": limit}).json()
    out = []
    for r in data.get("results", []):
        b = r.get("bibjson", {})
        links = b.get("link", []) or []
        url = links[0].get("url", "") if links else ""
        out.append(_rec(b.get("title", ""), b.get("abstract", ""), url,
                        "academic:doaj",
                        int(b["year"]) if str(b.get("year", "")).isdigit() else None,
                        [a.get("name", "") for a in (b.get("author") or [])[:5]]))
    return out


def search_plos(query: str, limit: int = 8) -> list[dict]:
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        data = c.get("https://api.plos.org/search", params={
            "q": query, "rows": limit, "wt": "json",
            "fl": "title,abstract,author_display,publication_date,id"}).json()
    out = []
    for d in data.get("response", {}).get("docs", []):
        ab = d.get("abstract")
        out.append(_rec(d.get("title", ""),
                        (ab[0] if isinstance(ab, list) else ab) or "",
                        f"https://doi.org/{d.get('id','')}", "academic:plos",
                        (d.get("publication_date", "") or "")[:4] and int(d["publication_date"][:4]),
                        (d.get("author_display") or [])[:5]))
    return out


# --- news agencies -------------------------------------------------------------
def search_google_news(query: str, limit: int = 10) -> list[dict]:
    """Google News RSS aggregates the wire agencies (Reuters, AP, AFP, dpa, ...)
    and thousands of outlets. Keyless. Title + link; the collector fetches text."""
    from urllib.parse import quote
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA},
                      follow_redirects=True) as c:
        xml = c.get(url).text
    out = []
    for item in ET.fromstring(xml).iter("item"):
        src = item.find("source")
        out.append(_rec(item.findtext("title", default=""), "",
                        item.findtext("link", default=""), "news", None,
                        [src.text] if src is not None and src.text else []))
        if len(out) >= limit:
            break
    return out


def search_gdelt(query: str, limit: int = 10) -> list[dict]:
    """GDELT indexes worldwide news (agencies, papers). Title + URL; the
    collector fetches the article text."""
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        data = c.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": query, "mode": "ArtList", "maxrecords": limit,
            "format": "json", "sort": "DateDesc"}).json()
    return [_rec(a.get("title", ""), "", a.get("url", ""), "news",
                 None, [a.get("domain", "")]) for a in data.get("articles", [])]


def _media_wiki(host: str, query: str, limit: int, source: str) -> list[dict]:
    base = f"https://{host}/w/api.php"
    out = []
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        hits = c.get(base, params={"action": "query", "list": "search",
                     "srsearch": query, "srlimit": limit, "format": "json"}).json()
        for h in hits.get("query", {}).get("search", []):
            t = h["title"]
            ex = c.get(base, params={"action": "query", "prop": "extracts",
                       "exintro": 1, "explaintext": 1, "titles": t,
                       "format": "json", "redirects": 1}).json()
            for p in ex.get("query", {}).get("pages", {}).values():
                if p.get("extract"):
                    out.append(_rec(t, p["extract"],
                                    f"https://{host}/wiki/{t.replace(' ', '_')}",
                                    source))
    return out


def search_wikipedia(query: str, limit: int = 6, lang: str = "en") -> list[dict]:
    return _media_wiki(f"{lang}.wikipedia.org", query, limit, "wikipedia")


def search_wikinews(query: str, limit: int = 6, lang: str = "en") -> list[dict]:
    return _media_wiki(f"{lang}.wikinews.org", query, limit, "news")


# --- tech ----------------------------------------------------------------------
def search_hackernews(query: str, limit: int = 8) -> list[dict]:
    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        data = c.get("https://hn.algolia.com/api/v1/search", params={
            "query": query, "tags": "story", "hitsPerPage": limit}).json()
    return [_rec(h.get("title", ""), h.get("story_text", "") or "",
                 h.get("url", "") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                 "tech", None, [h.get("author", "")]) for h in data.get("hits", [])]


# --- private company source (per-adapter creds, not in the keyless CATEGORIES) ---
def search_confluence(conf: dict, token: str, query: str, limit: int = 12) -> list[dict]:
    """Targeted Confluence search via CQL. Auth matches Atlassian conventions:
    bearer (Personal Access Token, Server/DC) or basic (email + API token, Cloud).
    Returns page records with text extracted from the rendered body."""
    base = (conf.get("base_url") or "").rstrip("/")
    if not base or not token:
        return []
    cql = f'text ~ "{query}" and type = page'
    if conf.get("space"):
        cql += f' and space = "{conf["space"]}"'
    headers = {"Accept": "application/json"}
    auth = None
    if conf.get("auth_type") == "basic":
        auth = (conf.get("email", ""), token)
    else:
        headers["Authorization"] = "Bearer " + token
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=headers, auth=auth) as c:
            data = c.get(f"{base}/content/search",
                         params={"cql": cql, "limit": limit, "expand": "body.view"}).json()
    except Exception:
        return []
    site = (data.get("_links", {}) or {}).get("base", "")
    out = []
    for r in data.get("results", []):
        html = (((r.get("body") or {}).get("view") or {}).get("value")) or ""
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
        webui = ((r.get("_links") or {}).get("webui")) or ""
        out.append(_rec(r.get("title", ""), text, (site + webui) if webui else base,
                        "confluence"))
    return out


def _adf_text(node) -> str:
    """Flatten Atlassian Document Format (Cloud) to plain text; pass strings through."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        t = node.get("text", "")
        return (t + " " + " ".join(_adf_text(c) for c in node.get("content", []))).strip()
    if isinstance(node, list):
        return " ".join(_adf_text(c) for c in node)
    return ""


def search_jira(conf: dict, token: str, query: str, limit: int = 12) -> list[dict]:
    """Targeted Jira search via JQL (REST v2). Covers normal projects and Jira
    Service Management (ITSM) issues. Auth: bearer (PAT, Server/DC) or basic (Cloud)."""
    base = (conf.get("base_url") or "").rstrip("/")
    if not base or not token:
        return []
    safe = query.replace('"', " ").strip()
    jql = f'text ~ "{safe}"'
    if conf.get("project"):
        jql = f'project = "{conf["project"]}" AND ' + jql
    jql += " ORDER BY updated DESC"
    headers = {"Accept": "application/json"}
    auth = None
    if conf.get("auth_type") == "basic":
        auth = (conf.get("email", ""), token)
    else:
        headers["Authorization"] = "Bearer " + token
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=headers, auth=auth) as c:
            data = c.get(f"{base}/search", params={
                "jql": jql, "maxResults": limit, "expand": "changelog",
                "fields": ("summary,description,status,resolution,issuetype,priority,"
                           "created,resolutiondate,components,labels")}).json()
    except Exception:
        return []
    site = base.split("/rest/")[0]
    out = []
    for it in data.get("issues", []):
        f = it.get("fields", {}) or {}
        name = lambda x: (x or {}).get("name", "") if isinstance(x, dict) else ""
        parts = [f"Typ: {name(f.get('issuetype'))} · Status: {name(f.get('status'))} · "
                 f"Priorität: {name(f.get('priority')) or '—'} · "
                 f"Resolution: {name(f.get('resolution')) or '—'}"]
        desc = re.sub(r"\s+", " ", _adf_text(f.get("description")) or "").strip()
        if desc:
            parts.append("Beschreibung: " + desc[:1500])
        # status transitions from the changelog (workflow / dwell mining)
        timeline = []
        for h in (it.get("changelog", {}) or {}).get("histories", []):
            when = (h.get("created") or "")[:10]
            for item in h.get("items", []):
                if item.get("field") == "status":
                    timeline.append(f"{when}: {item.get('fromString')} → {item.get('toString')}")
        if timeline:
            parts.append("Status-Verlauf: " + " | ".join(timeline[:20]))
        created, resolved = (f.get("created") or "")[:10], (f.get("resolutiondate") or "")[:10]
        if created or resolved:
            parts.append(f"Erstellt: {created or '—'} · Gelöst: {resolved or '—'}")
        out.append(_rec(f.get("summary", "") or it.get("key", ""), "\n".join(parts),
                        f"{site}/browse/{it.get('key', '')}", "jira"))
    return out


CATEGORIES: dict[str, list] = {
    "academic": [search_openalex, search_crossref, search_arxiv,
                 search_semantic_scholar, search_europepmc, search_doaj, search_plos],
    "news": [search_google_news, search_gdelt, search_wikinews],
    "reference": [search_wikipedia],
    "tech": [search_hackernews],
}


def search_all(query: str, categories: list[str], per_source: int = 8) -> list[dict]:
    """Query every connector in the chosen categories. Resilient: a failing
    source is skipped, not fatal."""
    out: list[dict] = []
    for cat in categories:
        for fn in CATEGORIES.get(cat, []):
            try:
                out.extend(fn(query, per_source))
            except Exception:  # noqa: BLE001 — one dead source must not stop the net
                continue
    return out
