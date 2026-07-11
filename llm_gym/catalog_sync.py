"""Scheduled catalog freshness sync (#5) — keep the adapters current on new products.

Pulls your live product catalog (this example targets a Magento-style public
suggest API — point CATALOG_SUGGEST_URL at your own shop), diffs it against the
snapshot, and keeps both the grounding snapshot and the training pools current —
robustly, because the suggest API is *fuzzy* (each sweep surfaces a slightly
different subset of the ~330-product universe, ~268 at a time). A naive diff would
wrongly flag dozens of real products as "removed" every run and feed sweep noise
into training. So this is deliberately accumulating and conservative:

  • MERGE new SKUs into the snapshot (never clobbers existing rich entries — the
    snapshot's specs/description from the deeper pull are preserved; only missing
    fields are filled),
  • a SKU is flagged stale only after `stale_days` of *consecutive* misses — never
    on a single fuzzy miss; reappearing un-stales it,
  • a *new* SKU is ingested into training only after it's been confirmed on a 2nd
    sweep (provisional first sighting → confirmed → controlled trickle into the
    sales lanes' COLLECTED split, never the gold flood we rolled back),
  • each sweep also re-queries the known product families (SKU stems) so real
    products are never *consistently* missed → the staleness clock is trustworthy.

Sync bookkeeping (last_seen / seen_count / provisional) lives in a SIDECAR file so
the canonical catalog stays clean for the grounding layer. Net effect: grounding /
retrieval always reflects new products, the pool learns them automatically once
they're confirmed real, and nobody's work gets overwritten.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import catalog as _catalog
from . import catalog_gen as _gen
from .pool import Pool

# Point this at your own shop's suggest/search endpoint. No-ops (empty results)
# when unset, so this module is safe to leave unconfigured.
CATALOG_SUGGEST_URL = os.environ.get("LLMGYM_CATALOG_SUGGEST_URL", "")
CATALOG_FILE = _catalog.CATALOG_FILE
STATE_FILE = CATALOG_FILE.parent / ".catalog_sync_state.json"
STALE_DAYS = 14          # consecutive sweeps-worth of misses before flagging stale
CONFIRM_SIGHTINGS = 2    # sightings before a new SKU is trusted enough to train on

_KM = re.compile(r"(\d+(?:\.\d+)?)\s*km", re.I)
_NM = re.compile(r"(\d{3,4}(?:-\d{3,4})?)\s*nm", re.I)
_CONN = re.compile(r"\b(LC-Duplex|LC|MPO|MTP|SC|RJ45|CS)\b", re.I)
_FIBER = re.compile(r"\b(Singlemode|Multimode|SMF|MMF|OM[345]|copper)\b", re.I)

# Generic form-factor × variant grid to discover products via the suggest API.
_FF = ["SFP", "SFP+", "SFP28", "SFP56", "SFP-DD", "QSFP+", "QSFP28", "QSFP56",
       "QSFP-DD", "OSFP"]
_VAR = ["", "SR", "LR", "ER", "ZR", "SR4", "LR4", "ER4", "FR4", "DR", "BiDi",
        "CWDM", "DWDM", "DAC", "AOC", "breakout", "copper"]


def _parse_title(t: str) -> dict:
    out = {}
    if (m := _KM.search(t)):
        out["reach_km"] = float(m.group(1))
    if (m := _NM.search(t)):
        out["wavelength_nm"] = m.group(1)
    if (m := _CONN.search(t)):
        out["connector"] = m.group(1)
    if (m := _FIBER.search(t)):
        out["fiber"] = m.group(1)
    return out


def _stem(sku: str) -> str:
    """Product-family code = 2nd dotted segment (S.1312.20.DI:Sx → 1312,
    Q.161HG.10:Sx → 161HG). Re-querying stems re-confirms known families."""
    parts = sku.split(".")
    return parts[1] if len(parts) > 1 and parts[1] else sku


def _fetch_suggest(q: str) -> list:
    """One keyless suggest-API call → list of raw items (curl, never raises)."""
    import subprocess
    if not CATALOG_SUGGEST_URL:
        return []
    url = CATALOG_SUGGEST_URL + q.replace(" ", "%20")
    try:
        raw = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "15",
             "-H", "User-Agent: llm-gym-catalog-sync/1.0", url],
            capture_output=True, text=True).stdout
        data = json.loads(raw or "[]")
    except Exception:
        data = []
    return data if isinstance(data, list) else []


def _pull_live(rate: float = 1.1, known_skus=None, log=print) -> dict:
    """Current {sku: basic_entry} from the live Magento suggest API. SKU taken from
    item.sku to match the snapshot format (e.g. S.1312.20.DI:Sx). Three layers,
    keyless and rate-limited: the generic FF×variant grid (discover new), one query
    per known family stem, then a targeted second pass that directly re-queries any
    known product still missing — so real products are never *consistently* missed
    and the staleness clock stays honest."""
    def _sweep(queries, live):
        for q in queries:
            for item in _fetch_suggest(q):
                if item.get("type") != "product":
                    continue
                sku = (item.get("sku") or "").strip()
                if not sku or sku in live:
                    continue
                title = re.sub(r"<[^>]*>", "", item.get("title") or "").strip()
                live[sku] = {"sku": sku, "title": title, "url": item.get("url") or "",
                             **_parse_title(title)}
            time.sleep(rate)

    live: dict = {}
    queries = [f"{ff} {v}".strip() for ff in _FF for v in _VAR]
    if known_skus:
        queries += sorted({_stem(s) for s in known_skus if s} - set(queries))
    _sweep(queries, live)
    n_queries = len(queries)
    if known_skus:
        missing = sorted({s.split(":")[0] for s in known_skus if s and s not in live})
        _sweep(missing, live)
        n_queries += len(missing)
    log(f"catalog-sync: pulled {len(live)} live SKUs from {n_queries} queries")
    return live


def _diff(snap: dict, state: dict, live: dict, now: float, stale_days: int = STALE_DAYS):
    """Pure accumulating diff (no I/O). Mutates copies and returns the updated
    snapshot, sidecar state, and the new/confirmed/stale SKU lists. Testable."""
    snap = {k: dict(v) for k, v in snap.items()}
    state = {k: dict(v) for k, v in state.items()}
    # Grandfather pre-existing entries: start their staleness clock now so we never
    # punish products that were captured before this loop ever ran.
    for sku in snap:
        state.setdefault(sku, {"last_seen": now, "seen": 1, "provisional": False})

    added, confirmed = [], []
    for sku, entry in live.items():
        if sku in snap:
            merged = dict(snap[sku])
            for k, v in entry.items():            # fill only missing/empty fields
                if v not in (None, "") and not merged.get(k):
                    merged[k] = v
            merged.pop("_stale", None)            # reappeared → no longer stale
            merged.pop("_stale_at", None)
            snap[sku] = merged
            st = state.get(sku) or {"seen": 0, "provisional": False}
            st["last_seen"] = now
            st["seen"] = int(st.get("seen", 0)) + 1
            if st.get("provisional") and st["seen"] >= CONFIRM_SIGHTINGS:
                st["provisional"] = False
                confirmed.append(sku)
            state[sku] = st
        else:
            snap[sku] = {**entry, "_meta": {"source": "catalog-sync", "captured_at": now}}
            state[sku] = {"last_seen": now, "seen": 1, "provisional": True}
            added.append(sku)

    cutoff = now - stale_days * 86400
    newly_stale = []
    for sku in snap:
        if state.get(sku, {}).get("last_seen", now) < cutoff and not snap[sku].get("_stale"):
            snap[sku]["_stale"] = True
            snap[sku]["_stale_at"] = now
            newly_stale.append(sku)

    return snap, state, added, confirmed, newly_stale


def _basic_pairs(entry: dict) -> list:
    """Grounded Q&A for a freshly-discovered product, built from its suggest-API
    title (which is itself a complete spec line, e.g. "STM-4 SFP CWDM | ~80 km /
    28 dB, 1270 nm - 1610 nm, DDM, LC-Duplex"). Deliberately NOT the rich-spec
    templates in catalog_gen — those render empty placeholders for not-yet-enriched
    entries, which must never reach training."""
    sku = (entry.get("sku") or "").strip()
    title = (entry.get("title") or "").strip()
    if not sku or not title:
        return []
    reach = entry.get("reach_km")
    conn = entry.get("connector")
    pairs = [
        (f"Was ist der {sku}?", f"Der {sku} ist folgendes Produkt: {title}."),
        (f"What is the {sku}?", f"The {sku} is the following product: {title}."),
    ]
    if reach:
        tail_de = f", Anschluss {conn}." if conn else "."
        tail_en = f", {conn} connector." if conn else "."
        pairs.append((f"Wofür wird der {sku} eingesetzt?",
                      f"Der {sku} ({title}) wird für Verbindungen bis ~{int(reach)} km "
                      f"eingesetzt{tail_de}"))
        pairs.append((f"What is the {sku} used for?",
                      f"The {sku} ({title}) is used for links up to ~{int(reach)} km"
                      f"{tail_en}"))
    return pairs


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def sync(settings, *, ingest: bool = True, lanes=_gen.CATALOG_LANES, write: bool = True,
         log=print, stale_days: int = STALE_DAYS, _live=None) -> dict:
    """Pull live, accumulate the diff, persist, and trickle confirmed-new products
    into the sales pools. `_live` injects a pull (for testing without the network)."""
    snap_list = _load_json(CATALOG_FILE, [])
    snap = {(r.get("sku") or "").strip(): r for r in snap_list if (r.get("sku") or "").strip()}
    state = _load_json(STATE_FILE, {})

    now = time.time()
    live = _live if _live is not None else _pull_live(known_skus=list(snap), log=log)
    if not live:
        return {"ok": False, "reason": "live pull returned nothing — shop unreachable?"}

    snap, state, added, confirmed, newly_stale = _diff(snap, state, live, now, stale_days)
    structural = bool(added or confirmed or newly_stale)

    if write:
        if structural:
            CATALOG_FILE.with_suffix(f".json.bak-{int(now)}").write_text(
                json.dumps(snap_list, ensure_ascii=False), encoding="utf-8")
            CATALOG_FILE.write_text(
                json.dumps(list(snap.values()), ensure_ascii=False, indent=1),
                encoding="utf-8")
            _catalog._catalog.cache_clear()       # grounding sees the refreshed snapshot
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")   # always persist tracking

    ingested = {}
    if ingest and confirmed and write:
        ex = []
        for sku in confirmed:
            try:
                for user, answer in _basic_pairs(snap[sku]):
                    ex.append({"messages": [{"role": "user", "content": user},
                                            {"role": "assistant", "content": answer}],
                               "_meta": {"source": "catalog-sync", "verified": True,
                                         "origin": "catalog", "sku": sku, "captured_at": now}})
            except Exception:
                continue
        if ex:
            for lane in lanes:
                res = Pool(settings.pool_path, lane).append(ex, split="collected")
                ingested[lane] = res.get("added", 0)
            log(f"catalog-sync: ingested {len(ex)} grounded pairs for {len(confirmed)} "
                f"confirmed-new SKUs into collected of {len(lanes)} lane(s)")

    log(f"catalog-sync: {len(added)} new(provisional), {len(confirmed)} confirmed→ingest, "
        f"{len(newly_stale)} newly-stale, {len(snap)} total, {len(live)} this sweep")
    return {"ok": True,
            "added_provisional": added[:50], "added_count": len(added),
            "confirmed_new": confirmed[:50], "confirmed_count": len(confirmed),
            "newly_stale": newly_stale[:50], "stale_count": len(newly_stale),
            "snapshot_total": len(snap), "live_this_sweep": len(live), "ingested": ingested}
