"""Champion record: which trained version is live, how it scored, and its history.

A freshly trained adapter is a *candidate*. It only becomes the *champion* (the
served version) when promoted. We keep the champion's verify score so a later
candidate that scores worse can be caught as a regression and never auto-shipped,
and we keep a version history so you can see — and roll back to — earlier ones.

The record lives next to the adapter's artifacts (champion.json), so it travels
with the adapter on rename and survives restarts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# A candidate may score this many points below the champion and still count as
# "not a regression" (judge noise). Below that it's treated as a real drop.
REGRESSION_TOLERANCE = 3.0


def _path(artifact_dir: Path) -> Path:
    return artifact_dir / "champion.json"


def load(artifact_dir: Path) -> dict | None:
    p = _path(artifact_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _score(verify: dict) -> dict:
    return {"pass_rate": verify.get("pass_rate", 0) or 0,
            "avg_score": verify.get("avg_score", 0) or 0}


def is_regression(candidate_verify: dict, champion: dict | None) -> bool:
    """True if the candidate scores clearly below the current champion."""
    if not champion:
        return False
    # A champion record carrying no score (legacy/corrupt) can't be compared —
    # treat that as "cannot confirm safe" so the caller gates instead of shipping.
    if "pass_rate" not in champion and "avg_score" not in champion:
        return True
    # Apples-to-apples only: if the champion was scored under a different eval mode
    # (trained adapter vs base vs override), its stored scalars aren't comparable —
    # don't fire a scalar regression (the head-to-head on the frozen set guards that).
    cand_mode = candidate_verify.get("eval_mode", "")
    champ_mode = champion.get("eval_mode", "")
    if cand_mode and champ_mode and cand_mode != champ_mode:
        return False
    cand = _score(candidate_verify)
    champ = champion.get("pass_rate", 0) or 0
    champ_avg = champion.get("avg_score", 0) or 0
    return (cand["pass_rate"] < champ - REGRESSION_TOLERANCE
            or cand["avg_score"] < champ_avg - REGRESSION_TOLERANCE)


def promote(artifact_dir: Path, verify: dict) -> dict | None:
    """Make this candidate the champion. Pushes the previous champion onto the
    history stack so it can be rolled back to. No-op (returns None) if the adapter
    has no artifacts dir yet."""
    if not artifact_dir.exists():
        return None
    prev = load(artifact_dir)
    history = (prev.get("history") if prev else []) or []
    if prev:
        history = history + [{k: prev.get(k) for k in
                              ("version", "pass_rate", "avg_score", "promoted_at")}]
    rec = {
        "version": (prev.get("version", 0) + 1) if prev else 1,
        "pass_rate": _score(verify)["pass_rate"],
        "avg_score": _score(verify)["avg_score"],
        # Remember HOW it was scored so a later candidate is only compared like-for-
        # like (see is_regression).
        "eval_mode": verify.get("eval_mode", ""),
        "eval_model": verify.get("eval_model", ""),
        "promoted_at": time.time(),
        "history": history[-20:],
    }
    _path(artifact_dir).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def mark_trained(artifact_dir: Path, gold_count: int) -> None:
    """Record how much gold the pool had when training started, so continuous
    mode knows when enough *new* gold has arrived to be worth retraining."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "last_train.json").write_text(
        json.dumps({"gold_count": int(gold_count), "ts": time.time()}),
        encoding="utf-8")


def gold_at_last_train(artifact_dir: Path) -> int:
    p = artifact_dir / "last_train.json"
    if not p.exists():
        return 0
    try:
        return int(json.loads(p.read_text(encoding="utf-8")).get("gold_count", 0))
    except Exception:  # noqa: BLE001
        return 0


def continuous_eligible(*, enabled: bool, in_pipeline: bool, cooldown_until: float,
                        now: float, gold_now: int, gold_at_train: int,
                        min_new: int) -> bool:
    """Should continuous mode (re)start this adapter's pipeline now?"""
    if not enabled or in_pipeline:
        return False
    if cooldown_until and now < cooldown_until:
        return False
    return (gold_now - gold_at_train) >= min_new


def rollback(artifact_dir: Path) -> dict | None:
    """Make the most recent previous version the champion again. The version being
    demoted is pushed back onto history (not discarded), so the rollback is itself
    reversible and the demoted scores aren't lost. Returns the restored record, or
    None if there is nothing to roll back to."""
    cur = load(artifact_dir)
    if not cur or not cur.get("history"):
        return None
    history = list(cur["history"])
    prev = history.pop()
    demoted = {k: cur.get(k) for k in
               ("version", "pass_rate", "avg_score", "promoted_at")}
    rec = {**prev, "history": (history + [demoted])[-20:],
           "rolled_back_at": time.time(), "rolled_back_from": cur.get("version")}
    _path(artifact_dir).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec
