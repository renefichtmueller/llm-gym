"""Quality tiers for training data.

Every collected example gets a 0-100 score from explainable signals, then a tier:

    Platin  >= 85   top quality — keep
    Gold    70-84   strong — keep
    Silver  50-69   usable — keep if you need volume
    Bronze   < 50   not worthwhile — discard by default

The score is the sum of weighted signals (relevance, source authority, citation,
completeness, verified, uniqueness) minus junk penalties. Pure functions, no I/O,
so the rubric is testable and the UI can show *why* something got its tier.
"""
from __future__ import annotations

import re

TIERS = ["platin", "gold", "silver", "bronze"]
TIER_THRESHOLDS = {"platin": 85, "gold": 70, "silver": 50}  # below silver -> bronze
TIER_RANK = {"platin": 4, "gold": 3, "silver": 2, "bronze": 1}

# How much we trust each source out of the box. Lookup falls back to the prefix
# before ":" (so any "academic:*" gets the academic weight).
SOURCE_AUTHORITY = {
    "manual": 24, "gold": 25,
    "academic": 22,                       # prefix fallback for academic:*
    "academic:openalex": 25, "academic:crossref": 24, "academic:europepmc": 24,
    "academic:arxiv": 22, "academic:semantic_scholar": 22, "academic:doaj": 21,
    "confluence": 24, "jira": 22,         # internal company docs/tickets — authoritative in context
    "wikipedia": 16, "news": 15, "tech": 11, "web": 9,
}

_WORD = re.compile(r"[a-zA-Zäöüß0-9]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "given", "into",
         "der", "die", "das", "und", "ein", "eine", "für", "mit", "den", "was"}

# Markers of a non-answer (refusal / meta / error) — terrible training targets.
_NON_ANSWER = (
    "i cannot", "i can't", "i am unable", "i'm unable", "i'm sorry", "i am sorry",
    "as an ai", "i won't", "cannot help with", "unable to help",
    "does not exist", "doesn't exist", "no actual user request",
    "prompt injection attempt", "this message appears to be",
    "i do not have access", "i don't have access",
)


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOP}


def tier_for(score: float) -> str:
    if score >= TIER_THRESHOLDS["platin"]:
        return "platin"
    if score >= TIER_THRESHOLDS["gold"]:
        return "gold"
    if score >= TIER_THRESHOLDS["silver"]:
        return "silver"
    return "bronze"


def meets_tier(tier: str, minimum: str) -> bool:
    return TIER_RANK.get(tier, 0) >= TIER_RANK.get(minimum, 0)


def score_example(example: dict, objective_tokens: set[str], *, unique: bool = True) -> dict:
    """Score one example against an adapter's objective tokens."""
    msgs = example.get("messages", [])
    meta = example.get("_meta", {}) or {}
    user = " ".join(m.get("content", "") for m in msgs if m.get("role") == "user")
    answer = " ".join(m.get("content", "") for m in msgs if m.get("role") == "assistant")

    signals: dict[str, float] = {}

    # Relevance: overlap of example tokens with the adapter's objective (0-30).
    ex_tokens = _tokens(user + " " + answer)
    overlap = len(ex_tokens & objective_tokens)
    signals["relevance"] = min(30, overlap * 3)

    # Coherence: does the ANSWER actually address the QUESTION? A perfect training
    # pair is a real Q with a real, on-topic A — not a generic blob (0-12).
    q_tok, a_tok = _tokens(user), _tokens(answer)
    coh = (len(q_tok & a_tok) / len(q_tok)) if q_tok else 0.0
    signals["coherence"] = round(min(12.0, coh * 24), 1)

    # Source authority (0-25).
    src = str(meta.get("source", "web"))
    signals["authority"] = float(SOURCE_AUTHORITY.get(
        src, SOURCE_AUTHORITY.get(src.split(":")[0], 9)))

    # Citation present (0-12).
    signals["citation"] = 12.0 if meta.get("citation") else 0.0

    # Completeness: a sane answer length (0-15).
    n = len(answer.strip())
    if 120 <= n <= 4000:
        signals["completeness"] = 15.0
    elif 60 <= n < 120 or 4000 < n <= 8000:
        signals["completeness"] = 8.0
    else:
        signals["completeness"] = 2.0

    # Verified gold pairs get a strong boost (0 or 18).
    signals["verified"] = 18.0 if meta.get("verified") else 0.0

    # Uniqueness within the pool (0 or 5).
    signals["unique"] = 5.0 if unique else 0.0

    # Junk penalties.
    penalty = 0.0
    if n < 40:
        penalty += 25
    symbols = sum(1 for c in answer if not c.isalnum() and not c.isspace())
    if n and symbols / n > 0.4:
        penalty += 15
    # Not actually a pair (missing side) — never a training example.
    if not user.strip() or not answer.strip():
        penalty += 60
    # Refusals / meta / error replies are NON-answers — they'd teach the model to
    # dodge, not to do the job. Hard penalty so they can never reach a usable tier.
    low = answer.lower()
    if any(p in low for p in _NON_ANSWER):
        penalty += 45
    signals["penalty"] = -penalty

    score = max(0.0, min(100.0, sum(signals.values())))
    tier = tier_for(score)
    # A "perfect" training pair: top tier, the answer addresses the question, it's a
    # complete on-objective answer, and nothing was penalised.
    perfect = bool(score >= TIER_THRESHOLDS["platin"] and signals["coherence"] >= 6.0
                   and signals["completeness"] >= 15.0 and signals["relevance"] >= 12.0
                   and penalty == 0.0)
    return {"score": round(score, 1), "tier": tier, "perfect": perfect,
            "signals": signals}


def objective_tokens(spec_like: dict) -> set[str]:
    """Build the token set that defines what the adapter is about."""
    parts = [spec_like.get("name", ""), spec_like.get("description", ""),
             spec_like.get("objective", "")]
    parts += list(spec_like.get("capabilities", []) or [])
    parts += list(spec_like.get("acceptance_prompts", []) or [])
    return _tokens(" ".join(parts))
