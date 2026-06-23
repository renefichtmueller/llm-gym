"""Adapter-driven data collector.

You point it at an adapter. It derives search queries from the adapter's
objective and capabilities, gathers candidates from open sources (and, if you
configure one, a web-search provider), fetches and extracts the text, scrubs
PII/secrets, scores each candidate into a tier (platin/gold/silver/bronze) and
writes the keepers into the pool's collected.jsonl.

"Search the whole web" is bounded on purpose: it respects robots.txt, rate-limits
itself, and stops at a page/time budget. You are responsible for the licence and
terms of anything you train on — the collector records the source so you can check.
"""
from __future__ import annotations

import json
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from . import academic, scoring, sources

# --- PII / secret scrubbing ---------------------------------------------------
_SCRUBBERS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\b(?:sk|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{12,}\b"), "[token]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
                re.DOTALL), "[key]"),
]


def scrub(text: str) -> str:
    for rx, repl in _SCRUBBERS:
        text = rx.sub(repl, text)
    return text


# --- minimal HTML -> text (stdlib only, no extra install) ---------------------
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.parts.append(t)

    def text(self) -> str:
        return re.sub(r"\s+\n", "\n", " ".join(self.parts))


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return ""
    return p.text()


# --- query derivation ---------------------------------------------------------
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "given", "into", "your",
    "should", "able", "must", "each", "über", "eine", "einen", "einem", "der",
    "die", "das", "und", "für", "mit", "den", "dem", "des", "was", "wie", "soll",
    "muss", "kann", "nach", "vor", "statt", "oder", "auch", "wenn", "bei", "zum",
    "zur", "als", "auf", "aus", "ist", "sind", "werden", "wird", "einer", "jede",
    "jeder", "jedes", "diese", "dieser", "dieses", "ihre", "sich", "von", "dann",
}


def _keywords(text: str, limit: int = 6) -> str:
    """Reduce a prose field to a short keyword query — search APIs want terms,
    not whole sentences."""
    words, seen = [], set()
    for w in re.findall(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß-]{3,}", text or ""):
        lw = w.lower()
        if lw in _STOPWORDS or lw in seen:
            continue
        seen.add(lw)
        words.append(w)
        if len(words) >= limit:
            break
    return " ".join(words)


def derive_queries(spec: dict, topic: str = "") -> list[str]:
    queries: list[str] = []
    # An explicit topic drives an intensive search on that subject across every
    # source. Breadth comes from the source net, not from many query variants.
    if topic.strip():
        queries.append(topic.strip())
        for cap in (spec.get("capabilities") or [])[:3]:
            kw = _keywords(cap, 3)
            if kw:
                queries.append(f"{topic.strip()} {kw}")
        seen, out = set(), []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
        return out
    topic = _keywords(f"{spec.get('name', '')} {spec.get('description', '')}", 6)
    if topic:
        queries.append(topic)
    obj_kw = _keywords(spec.get("objective", ""), 6)
    if obj_kw:
        queries.append(obj_kw)
    for cap in (spec.get("capabilities") or [])[:6]:
        kw = _keywords(cap, 5)
        if kw:
            queries.append(kw)
    if not queries and spec.get("name"):
        queries.append(spec["name"].replace("-", " "))
    # de-dup, keep order
    seen, out = set(), []
    for q in queries:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


# --- optional extra web search ------------------------------------------------
def _web_provider(query: str, limit: int, cfg) -> list[dict]:
    """Optional web search. Keyless via self-hosted SearXNG, or keyed Brave/Tavily.
    Keys are read from the environment at call time, never stored."""
    import os
    prov = (cfg.web_search or "").lower()
    try:
        if prov == "searxng" and cfg.searx_url:
            with httpx.Client(timeout=20) as c:
                data = c.get(f"{cfg.searx_url.rstrip('/')}/search",
                             params={"q": query, "format": "json"}).json()
            return [{"title": r.get("title", ""), "abstract": r.get("content", ""),
                     "url": r.get("url", ""), "source": "web", "year": None,
                     "authors": []} for r in data.get("results", [])[:limit]]
        if prov == "brave":
            key = os.environ.get("LLMGYM_WEB_SEARCH_KEY", "")
            with httpx.Client(timeout=20) as c:
                data = c.get("https://api.search.brave.com/res/v1/web/search",
                             params={"q": query, "count": limit},
                             headers={"X-Subscription-Token": key}).json()
            return [{"title": r.get("title", ""), "abstract": r.get("description", ""),
                     "url": r.get("url", ""), "source": "web", "year": None,
                     "authors": []} for r in data.get("web", {}).get("results", [])[:limit]]
        if prov == "tavily":
            key = os.environ.get("LLMGYM_WEB_SEARCH_KEY", "")
            with httpx.Client(timeout=25) as c:
                data = c.post("https://api.tavily.com/search", json={
                    "api_key": key, "query": query, "max_results": limit,
                    "include_raw_content": True}).json()
            return [{"title": r.get("title", ""), "abstract": r.get("content", ""),
                     "url": r.get("url", ""), "source": "web", "year": None,
                     "authors": []} for r in data.get("results", [])[:limit]]
    except Exception:
        return []
    return []


# --- robots + fetch -----------------------------------------------------------
_robots_cache: dict[str, robotparser.RobotFileParser] = {}


def _allowed(url: str, ua: str) -> bool:
    try:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        rp = _robots_cache.get(root)
        if rp is None:
            rp = robotparser.RobotFileParser()
            rp.set_url(root + "/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None  # unreadable robots -> be permissive but cache miss
            _robots_cache[root] = rp or robotparser.RobotFileParser()
        return rp.can_fetch(ua, url) if rp else True
    except Exception:
        return True


def _fetch_text(url: str, ua: str, timeout: float = 15) -> str:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": ua}) as c:
            r = c.get(url)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", "text/html"):
                return ""
            return html_to_text(r.text)[:6000]
    except Exception:
        return ""


# --- the collection run -------------------------------------------------------
def _existing_hashes(pool_dir: Path) -> set[str]:
    import hashlib
    hashes: set[str] = set()
    for name in ("train.jsonl", "gold.jsonl", "collected.jsonl"):
        f = pool_dir / name
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msgs = json.loads(line).get("messages", [])
                norm = json.dumps([(m.get("role"), (m.get("content") or "").strip())
                                   for m in msgs], ensure_ascii=False, sort_keys=True)
                hashes.add(hashlib.sha256(norm.encode()).hexdigest())
            except Exception:
                pass
    return hashes


def collect(spec: dict, pool_dir: Path, cfg, log, topic: str = "",
            plan: dict | None = None, confluence: dict | None = None,
            jira: dict | None = None, anon=None) -> dict:
    pool_dir.mkdir(parents=True, exist_ok=True)
    # Anonymise collected source content (PII + configured company/external terms).
    if anon is not None and getattr(anon, "enabled", True):
        from . import anonymize as _az
        _terms = list(getattr(anon, "terms", ()) or [])
        _rn = bool(getattr(anon, "redact_names", False))
        def _anon(t: str) -> str:
            return _az.anonymize(t, terms=_terms, redact_names=_rn)
    else:
        _anon = scrub   # PII-only fallback (back-compat when no settings passed)
    ua = cfg.user_agent
    default_cats = cfg.source_categories or ["academic", "news", "reference", "tech"]
    obj = scoring.objective_tokens(spec)
    if plan and plan.get("queries"):
        # The advisor decided what + where to search.
        queries = list(plan["queries"])
        cats = [c for c in plan.get("source_categories", []) if c] or default_cats
        obj = obj | scoring.objective_tokens(
            {"objective": " ".join(queries) + " " + plan.get("domain", "")})
        log(f"advisor plan: domain={plan.get('domain', '?')!r} · sources={cats}")
    else:
        if topic.strip():  # score relevance against the requested topic
            obj = obj | scoring.objective_tokens({"objective": topic})
        queries = derive_queries(spec, topic)
        cats = default_cats
    log(f"derived {len(queries)} queries"
        + (f" for topic {topic!r}" if topic.strip() else ""))

    # 1) gather candidates from the wide source net (universities + news + ...)
    candidates: list[dict] = []
    per = cfg.results_per_source
    conf_token = (confluence or {}).get("token") or ""
    conf_on = bool((confluence or {}).get("enabled") and conf_token)
    jira_token = (jira or {}).get("token") or ""
    jira_on = bool((jira or {}).get("enabled") and jira_token)
    extra = "".join([" + confluence" if conf_on else "", " + jira" if jira_on else ""])
    log(f"sources: {', '.join(cats)}{extra} · {per}/source · {len(queries)} queries")
    start = time.time()                       # one time budget for the whole run
    for i, q in enumerate(queries, 1):
        if (time.time() - start) > cfg.max_seconds:
            log(f"time budget reached during source search — stopping at "
                f"query {i}/{len(queries)}")
            break
        log(f"search [{i}/{len(queries)}]: {q!r}")
        candidates += sources.search_all(q, cats, per)
        candidates += _web_provider(q, per, cfg)
        if conf_on:
            candidates += sources.search_confluence(confluence, conf_token, q, per)
        if jira_on:
            candidates += sources.search_jira(jira, jira_token, q, per)
    private = [n for n, on in (("Confluence", conf_on), ("Jira", jira_on)) if on]
    log(f"{len(candidates)} raw candidates"
        + (f" (incl. {', '.join(private)})" if private else ""))

    # 2) fetch web pages (academic/wiki already carry text); enforce robots + budget
    # (reuses the single `start` above so phase 1 + 2 share one time budget)
    seen_urls: set[str] = set()
    pages = 0
    enriched: list[dict] = []
    for cand in candidates:
        if pages >= cfg.max_pages or (time.time() - start) > cfg.max_seconds:
            log("budget reached — stopping fetch")
            break
        url = cand.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # Fetch the page for any candidate that has a url but no text yet
        # (news agencies via GDELT, web results, etc.) — robots + budget enforced.
        if url and not (cand.get("abstract") or "").strip():
            if not _allowed(url, ua):
                continue
            cand["abstract"] = _fetch_text(url, ua)
            pages += 1
            time.sleep(cfg.rate_limit_seconds)
        if (cand.get("abstract") or "").strip():
            enriched.append(cand)

    # 3) build pairs, scrub, score -> tier, keep non-dupes above bronze
    existing = _existing_hashes(pool_dir)
    import hashlib
    kept: list[dict] = []
    tier_counts = {t: 0 for t in scoring.TIERS}
    perfect_kept = 0
    discarded = 0
    for cand in enriched:
        cand["abstract"] = _anon(cand["abstract"])[:4000]
        cand["title"] = _anon(cand.get("title", ""))
        pair = academic.to_candidate_pair(cand)
        norm = json.dumps([(m.get("role"), (m.get("content") or "").strip())
                           for m in pair["messages"]], ensure_ascii=False, sort_keys=True)
        h = hashlib.sha256(norm.encode()).hexdigest()
        unique = h not in existing
        scored = scoring.score_example(pair, obj, unique=unique)
        tier_counts[scored["tier"]] += 1
        if scored["tier"] == "bronze" or not unique:
            discarded += 1
            continue
        existing.add(h)
        if scored["perfect"]:
            perfect_kept += 1
        pair["_meta"].update({"tier": scored["tier"], "score": scored["score"],
                              "perfect": scored["perfect"],
                              "signals": scored["signals"], "license": "unknown"})
        kept.append(pair)

    out = pool_dir / "collected.jsonl"
    with out.open("a", encoding="utf-8") as fh:
        for p in kept:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    summary = {"candidates": len(candidates), "fetched_pages": pages,
               "kept": len(kept), "discarded": discarded, "tiers": tier_counts,
               "perfect": perfect_kept}
    log(f"done: kept {len(kept)} (platin {tier_counts['platin']}, "
        f"gold {tier_counts['gold']}, silver {tier_counts['silver']}), "
        f"discarded {discarded}")
    return summary


# --- background runner (one collection at a time) -----------------------------
class CollectorService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}

    def status(self, adapter: str) -> dict:
        return self._runs.get(adapter, {"state": "idle"})

    def busy(self) -> bool:
        return any(r.get("state") == "running" for r in self._runs.values())

    def is_running(self, adapter: str) -> bool:
        return self._runs.get(adapter, {}).get("state") == "running"

    def active(self) -> list[dict]:
        """Running standalone collections (the 'collect only' button), with their
        latest log line — for the live view. (Pipeline collects show up under the
        pipeline's own state instead.)"""
        out = []
        for adapter, r in list(self._runs.items()):
            if r.get("state") != "running":
                continue
            last = ""
            lp = r.get("log")
            if lp:
                try:
                    lines = Path(lp).read_text(encoding="utf-8").splitlines()
                    last = lines[-1] if lines else ""
                except Exception:  # noqa: BLE001
                    pass
            out.append({"adapter": adapter, "started": r.get("started"), "last": last})
        return out

    def rename_adapter(self, old: str, new: str) -> None:
        """Move a (transient) collection-run entry to the new adapter name."""
        with self._lock:
            if old in self._runs:
                self._runs[new] = self._runs.pop(old)

    def start(self, spec: dict, pool_dir: Path, cfg, log_path: Path,
              topic: str = "", plan: dict | None = None,
              confluence: dict | None = None, jira: dict | None = None) -> dict:
        adapter = spec["name"]
        if self.busy():
            return {"ok": False, "error": "A collection is already running."}
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")  # fresh log per run
        self._runs[adapter] = {"state": "running", "started": time.time(),
                               "summary": None, "log": str(log_path)}

        def log(msg: str) -> None:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(msg + "\n")

        def worker() -> None:
            try:
                summary = collect(spec, pool_dir, cfg, log, topic, plan, confluence, jira)
                self._runs[adapter] = {"state": "done", "summary": summary,
                                       "log": str(log_path)}
            except Exception as exc:  # noqa: BLE001
                log(f"ERROR: {exc}")
                self._runs[adapter] = {"state": "failed", "error": str(exc),
                                       "log": str(log_path)}

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}


SERVICE = CollectorService()
