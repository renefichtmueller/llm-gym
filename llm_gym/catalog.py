"""Shop-catalog grounding for the sales/support lanes.

Those lanes must never invent SKUs or specs. This loads your real product catalog
(data/catalog/catalog.json — however you populate it, e.g. a scheduled pull from
your shop's own search/suggest API) and checks an answer's SKU claims against it: a
SKU-shaped token that is NOT in the catalog is a hallucination. Used as a grounding
penalty in scoring and a blocking gate in distill for catalog-grounded lanes.

The catalog carries no price field, so grounding covers SKU existence + specs;
prices are handled by teaching abstention ("check the current price in the shop").
Gracefully no-ops (ok=True, empty) when the catalog file is absent, so it can never
hard-block a lane just because the snapshot is missing.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import ROOT

CATALOG_FILE = ROOT / "data" / "catalog" / "catalog.json"

# Example calibration target: dotted, alnum SKU codes (S.1312.20.DI:Sx, QP.Czz.z,
# C2.1HG.QCT:Sx, OQ1.A854HG.R.z, ...) — a 1-3 char letter-led prefix, then >=2
# dot-separated alnum groups, optional :suffix. Adjust SKU_RE to match your own
# catalog's SKU format.
SKU_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9]{0,2}\.[A-Za-z0-9]{1,10}(?:\.[A-Za-z0-9]{1,10}){1,5}"
    r"(?::[A-Za-z0-9]{1,5})?")

# Tokens that match the shape but are abbreviations/versions, not SKUs. No real SKU
# starts with these, so dropping them removes the only residual false positives.
_DENY_PREFIX = ("v.", "ver.", "rev.", "vol.", "no.", "fig.", "eq.", "ca.", "approx.")


def _qualifies(tok: str) -> bool:
    if tok.lower().startswith(_DENY_PREFIX):
        return False
    # Real SKUs carry a digit, a :suffix, or are reasonably long; short dotted
    # abbreviations (U.S.A, e.g.) do not.
    return any(c.isdigit() for c in tok) or ":" in tok or len(tok) >= 7


@lru_cache(maxsize=1)
def _catalog() -> dict:
    """{sku -> entry}. Empty when the catalog file is missing (grounding no-ops)."""
    try:
        rows = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {(r.get("sku") or "").strip(): r for r in rows if (r.get("sku") or "").strip()}


def available() -> bool:
    return bool(_catalog())


def known(sku: str) -> bool:
    return sku.strip() in _catalog()


def facts(sku: str) -> dict | None:
    """The real catalog entry (title, reach_km, connector, fiber, specs, ...)."""
    return _catalog().get(sku.strip())


def extract_skus(text: str) -> list[str]:
    """SKU-shaped tokens in the text (real or invented), de-duplicated, order kept."""
    return list(dict.fromkeys(t for t in SKU_RE.findall(text or "") if _qualifies(t)))


def check_grounding(text: str) -> dict:
    """Split an answer's SKU tokens into cited-real vs invented (not in the catalog).
    ok=False only when the catalog is loaded AND >=1 invented SKU appears; ok=True
    (no-op) when the catalog is unavailable."""
    cat = _catalog()
    if not cat:
        return {"ok": True, "cited": [], "invented": [], "reason": "no catalog"}
    cited, invented = [], []
    for tok in extract_skus(text):
        (cited if tok in cat else invented).append(tok)
    return {"ok": not invented, "cited": cited, "invented": invented,
            "reason": (f"{len(invented)} invented SKU(s): {', '.join(invented[:3])}"
                       if invented else f"{len(cited)} grounded SKU(s)")}
