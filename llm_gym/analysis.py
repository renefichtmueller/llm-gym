"""Analyse a pool before training and forecast the likely outcome.

Produces a structured report the UI visualises: tier distribution, duplicate
ratio, length stats, citation/verified share, source mix, how well the data
covers the adapter's stated capabilities, a pool grade, and a heuristic forecast
of the training result. The forecast is an estimate, not a promise — it is based
on how much usable, on-topic, non-duplicate data you have at the chosen tier.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path

from . import scoring


def _hash(messages: list[dict]) -> str:
    norm = json.dumps([(m.get("role"), (m.get("content") or "").strip())
                       for m in messages], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _grade(score: float) -> str:
    return ("A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55
            else "D" if score >= 40 else "F")


def analyse(pool_dir: Path, spec: dict, min_tier: str = "gold") -> dict:
    gold = _read(pool_dir / "gold.jsonl")
    train = _read(pool_dir / "train.jsonl")
    collected = _read(pool_dir / "collected.jsonl")
    valid = _read(pool_dir / "valid.jsonl")

    obj = scoring.objective_tokens(spec)
    everything = gold + train + collected
    seen: set[str] = set()
    duplicates = 0
    tier_counts = {t: 0 for t in scoring.TIERS}
    lengths: list[int] = []
    with_citation = 0
    verified = 0
    sources: dict[str, int] = {}
    relevance_scores: list[float] = []
    usable = 0  # unique examples at/above min_tier (gold/manual always count)

    for ex in everything:
        msgs = ex.get("messages")
        if not (isinstance(msgs, list) and len(msgs) >= 2):
            continue
        h = _hash(msgs)
        is_dup = h in seen
        if is_dup:
            duplicates += 1
        seen.add(h)
        meta = ex.get("_meta", {}) or {}
        # Pre-scored (collector) examples keep their tier; gold/manual are top.
        if meta.get("tier"):
            scored = {"tier": meta["tier"], "score": meta.get("score", 0)}
        elif meta.get("verified"):
            scored = {"tier": "platin", "score": 90}
        else:
            scored = scoring.score_example(ex, obj, unique=not is_dup)
        tier_counts[scored["tier"]] = tier_counts.get(scored["tier"], 0) + 1
        relevance_scores.append(scored["score"])
        answer = " ".join(m.get("content", "") for m in msgs if m.get("role") == "assistant")
        lengths.append(len(answer))
        if meta.get("citation"):
            with_citation += 1
        if meta.get("verified"):
            verified += 1
        src = str(meta.get("source", "manual"))
        sources[src] = sources.get(src, 0) + 1
        if not is_dup and scoring.meets_tier(scored["tier"], min_tier):
            usable += 1

    total = len(everything)
    unique = len(seen)
    caps = spec.get("capabilities", []) or []
    coverage = []
    for cap in caps:
        cap_tokens = scoring.objective_tokens({"objective": cap})
        hits = sum(1 for ex in everything
                   if cap_tokens & scoring.objective_tokens(
                       {"objective": " ".join(m.get("content", "") for m in ex.get("messages", []))}))
        coverage.append({"capability": cap, "examples": hits})

    avg_rel = round(statistics.mean(relevance_scores), 1) if relevance_scores else 0.0
    dup_ratio = round(duplicates / total, 2) if total else 0.0
    pool_score = max(0.0, avg_rel - dup_ratio * 20)

    forecast = _forecast(usable, tier_counts, dup_ratio, len(valid), min_tier)

    return {
        "totals": {"all": total, "unique": unique, "duplicates": duplicates,
                   "duplicate_ratio": dup_ratio, "gold": len(gold),
                   "train": len(train), "collected": len(collected),
                   "valid": len(valid), "usable_at_tier": usable},
        "tiers": tier_counts,
        "length": {"min": min(lengths) if lengths else 0,
                   "median": int(statistics.median(lengths)) if lengths else 0,
                   "max": max(lengths) if lengths else 0},
        "citation_pct": round(100 * with_citation / total) if total else 0,
        "verified_pct": round(100 * verified / total) if total else 0,
        "sources": sources,
        "coverage": coverage,
        "avg_relevance": avg_rel,
        "pool_grade": _grade(pool_score),
        "min_tier": min_tier,
        "forecast": forecast,
    }


def _forecast(usable: int, tiers: dict, dup_ratio: float, valid: int,
              min_tier: str) -> dict:
    """Heuristic outcome estimate. Honest about being an estimate."""
    reasons: list[str] = []
    high = tiers.get("platin", 0) + tiers.get("gold", 0)
    if usable < 20:
        verdict, lo, hi, conf = "Likely weak", 0.5, 1.0, "low"
        reasons.append(f"Only {usable} usable examples at '{min_tier}'+ — too few to learn a task.")
    elif usable < 80:
        verdict, lo, hi, conf = "Solid possible", 0.25, 0.5, "medium"
        reasons.append(f"{usable} usable examples — enough for a usable adapter.")
    else:
        verdict, lo, hi, conf = "Good likely", 0.12, 0.3, "medium"
        reasons.append(f"{usable} usable examples — a good amount for LoRA.")
    if high >= max(1, usable) * 0.5 and usable >= 20:
        reasons.append("Half or more are Platin/Gold — quality is on your side.")
        if conf == "medium":
            conf = "high"
    if dup_ratio > 0.3:
        reasons.append(f"High duplicate ratio ({int(dup_ratio*100)}%) — dedup will shrink the set.")
    if valid == 0:
        reasons.append("No validation set yet — add a few held-out examples to grade honestly.")
    return {"expected_verdict": verdict, "val_loss_low": lo, "val_loss_high": hi,
            "confidence": conf, "reasons": reasons}
