"""Human feedback -> DPO preference pairs.

A preference pair says: for this prompt, the `chosen` response is better than
the `rejected` one. Stored per-pool (mirrors gold.jsonl's per-pool layout) at
POOL_ROOT/<name>/feedback.jsonl, one JSON object per line:

    {"prompt": "...", "chosen": "...", "rejected": "...",
     "_meta": {"source": "human" | "gold_vs_error", "rater": "...", "ts": ...}}

Two ways pairs get here:
  1. A human rates two candidate responses directly (submit_pair) -- this IS
     the RLHF signal: person says "this answer is better than that one".
  2. Auto-derived from data the gym already collects during verification: a
     gold-standard answer for a prompt (chosen) paired against a same-prompt
     verify-failure answer (rejected) -- see derive_from_verification(). This
     bootstraps a first batch of pairs with zero new human effort.

train_dpo_adapter (trainer/dpo_backend.py) trains directly on this file.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .pool import Pool


def pairs_file(pool: Pool) -> Path:
    return pool.dir / "feedback.jsonl"


def _normalize(text: str) -> str:
    """Casefold + collapse internal whitespace so a duplicate submission with
    different capitalization/spacing still hashes the same — a bare .strip()
    only catches leading/trailing whitespace, not the trivial variations that
    would otherwise let a flood of near-identical pairs past the dedup check."""
    return " ".join((text or "").split()).casefold()


def _hash_pair(prompt: str, chosen: str, rejected: str) -> str:
    norm = json.dumps([_normalize(prompt), _normalize(chosen), _normalize(rejected)],
                       ensure_ascii=False)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_pairs(pool: Pool) -> list[dict]:
    return _read(pairs_file(pool))


def stats(pool: Pool) -> dict:
    pairs = read_pairs(pool)
    by_source: dict[str, int] = {}
    for p in pairs:
        src = (p.get("_meta") or {}).get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
    return {"total": len(pairs), "by_source": by_source}


def submit_pair(pool: Pool, prompt: str, chosen: str, rejected: str, *,
                 source: str = "human", rater: str = "") -> dict:
    """Append one human preference pair. Rejects empty/identical fields and
    exact duplicates (by content hash) -- mirrors Pool.append()'s dedup."""
    prompt, chosen, rejected = prompt.strip(), chosen.strip(), rejected.strip()
    if not (prompt and chosen and rejected):
        return {"ok": False, "reason": "prompt, chosen, and rejected must all be non-empty"}
    if chosen == rejected:
        return {"ok": False, "reason": "chosen and rejected must differ"}
    path = pairs_file(pool)
    existing = {_hash_pair(p.get("prompt", ""), p.get("chosen", ""), p.get("rejected", ""))
                for p in _read(path)}
    h = _hash_pair(prompt, chosen, rejected)
    if h in existing:
        return {"ok": False, "reason": "duplicate pair already recorded"}
    record = {"prompt": prompt, "chosen": chosen, "rejected": rejected,
              "_meta": {"source": source, "rater": rater, "ts": time.time()}}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "added": 1}


def _last_turn(messages: list[dict]) -> tuple[str, str] | None:
    """(last user content, last assistant content) from a chat-format example,
    or None if either is missing -- mirrors Pool._answers()'s trailing-turn pick."""
    user = assistant = None
    for m in messages or []:
        if m.get("role") == "user":
            user = (m.get("content") or "").strip()
        elif m.get("role") == "assistant":
            assistant = (m.get("content") or "").strip()
    if not (user and assistant):
        return None
    return user, assistant


def derive_from_verification(pool: Pool, anonymize=lambda t: t) -> dict:
    """Build preference pairs from data the verify stage already wrote:
    gold.jsonl (trusted-correct answers) paired as `chosen` against
    errors.jsonl (verify-failure answers, same prompt) as `rejected`.
    Appends only new, deduped pairs. Returns {"ok", "added", "candidates"}.

    errors.jsonl is written directly from live judge output (pipeline.py's
    verify stage) and is NOT anonymized at write time, unlike gold.jsonl or
    a human-submitted pair -- pass `anonymize` (e.g. anonymize.anonymize)
    so this path gets the same PII/secret scrubbing guarantee as every other
    way text enters the pool, instead of training DPO directly on raw judge
    output.
    """
    gold_by_prompt: dict[str, str] = {}
    for ex in _read(pool.gold_file):
        turn = _last_turn(ex.get("messages", []))
        if turn:
            gold_by_prompt.setdefault(turn[0], turn[1])

    errors_path = pool.dir / "errors.jsonl"
    added = 0
    candidates = 0
    for ex in _read(errors_path):
        turn = _last_turn(ex.get("messages", []))
        if not turn:
            continue
        prompt, bad_answer = turn
        good_answer = gold_by_prompt.get(prompt)
        if not good_answer or good_answer == bad_answer:
            continue
        candidates += 1
        result = submit_pair(pool, anonymize(prompt), anonymize(good_answer), anonymize(bad_answer),
                              source="gold_vs_error", rater="auto")
        if result["ok"]:
            added += 1
    return {"ok": True, "added": added, "candidates": candidates}
