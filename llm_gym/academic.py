"""Search open academic sources for candidate training material.

These APIs index university and research output and are free to query without an
account (CORE is optional and key-gated). Everything returned here is a
*candidate*: it must be curated and, for gold data, verified before it earns a
place in the pool. See docs/WALKTHROUGH.md for the curation workflow.

Sources:
  - OpenAlex          openalex.org        (250M+ works, no key)
  - arXiv             arxiv.org           (preprints, no key)
  - Semantic Scholar  semanticscholar.org (graph of papers, keyless tier)
  - CORE              core.ac.uk          (open-access aggregator, optional key)
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

import httpx


@dataclass
class Paper:
    title: str
    abstract: str
    year: int | None
    authors: list[str]
    url: str
    source: str


def _reconstruct_openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def search_openalex(query: str, limit: int = 10) -> list[Paper]:
    url = "https://api.openalex.org/works"
    params = {"search": query, "per-page": limit,
              "mailto": "llm-gym@example.org"}  # polite pool, not a credential
    with httpx.Client(timeout=20) as c:
        data = c.get(url, params=params).json()
    out: list[Paper] = []
    for w in data.get("results", []):
        out.append(Paper(
            title=w.get("title") or "",
            abstract=_reconstruct_openalex_abstract(w.get("abstract_inverted_index")),
            year=w.get("publication_year"),
            authors=[a["author"]["display_name"]
                     for a in (w.get("authorships") or [])[:5]],
            url=w.get("doi") or w.get("id") or "",
            source="openalex",
        ))
    return out


def search_arxiv(query: str, limit: int = 10) -> list[Paper]:
    url = "http://export.arxiv.org/api/query"
    params = {"search_query": f"all:{query}", "max_results": limit}
    with httpx.Client(timeout=20) as c:
        xml = c.get(url, params=params).text
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    out: list[Paper] = []
    for e in root.findall("a:entry", ns):
        published = (e.findtext("a:published", default="", namespaces=ns) or "")[:4]
        out.append(Paper(
            title=(e.findtext("a:title", default="", namespaces=ns) or "").strip(),
            abstract=(e.findtext("a:summary", default="", namespaces=ns) or "").strip(),
            year=int(published) if published.isdigit() else None,
            authors=[a.findtext("a:name", default="", namespaces=ns)
                     for a in e.findall("a:author", ns)][:5],
            url=e.findtext("a:id", default="", namespaces=ns) or "",
            source="arxiv",
        ))
    return out


def search_semantic_scholar(query: str, limit: int = 10) -> list[Paper]:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": query, "limit": limit,
              "fields": "title,abstract,year,authors,url"}
    with httpx.Client(timeout=20) as c:
        data = c.get(url, params=params).json()
    out: list[Paper] = []
    for p in data.get("data", []) or []:
        out.append(Paper(
            title=p.get("title") or "",
            abstract=p.get("abstract") or "",
            year=p.get("year"),
            authors=[a.get("name", "") for a in (p.get("authors") or [])[:5]],
            url=p.get("url") or "",
            source="semantic_scholar",
        ))
    return out


_SOURCES = {
    "openalex": search_openalex,
    "arxiv": search_arxiv,
    "semantic_scholar": search_semantic_scholar,
}


def search(query: str, sources: list[str] | None = None, limit: int = 10) -> list[dict]:
    sources = sources or list(_SOURCES)
    results: list[dict] = []
    for src in sources:
        fn = _SOURCES.get(src)
        if not fn:
            continue
        try:
            results.extend(asdict(p) for p in fn(query, limit))
        except Exception as exc:  # noqa: BLE001 — one dead source must not kill the rest
            results.append({"source": src, "error": str(exc), "title": "",
                            "abstract": "", "year": None, "authors": [], "url": ""})
    return results


def to_candidate_pair(paper: dict, question: str | None = None) -> dict:
    """Turn a paper into a CANDIDATE training pair. Unverified by design — edit
    the answer and verify it before promoting to gold."""
    q = question or f"Summarise the key contribution of: {paper.get('title', '')}"
    return {
        "messages": [
            {"role": "user", "content": q},
            {"role": "assistant", "content": paper.get("abstract", "")},
        ],
        "_meta": {
            "source": f"academic:{paper.get('source', '')}",
            "verified": False,
            "quality_score": 50,
            "citation": paper.get("url", ""),
        },
    }
