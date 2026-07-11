"""Distill the highest-quality collected pairs into the gold split.

`judge.score_pair_quality()` (the gym's "search for perfect pairs" grader) existed
but was never called, so collected data never earned its way into gold and training
ran on tier-labelled noise. This module walks a pool's collected.jsonl, has the
judge rate each (question, answer) pair, and promotes only the *perfect* ones
(passed AND score >= 85) into the gold split, tagged source='distill'. Verdicts are
cached by message hash so the (GPU-bound) judge cost is paid once.

GPU note: the judge runs on Ollama — run distill at idle, not during training.
"""
from __future__ import annotations

import json
from typing import Callable

from . import catalog as _catalog
from . import judge as _judge
from .pool import Pool, _hash_messages


def distill(spec, settings, *, limit: int = 0, dry_run: bool = False,
            log: Callable[[str], None] = print) -> dict:
    """Promote judge-'perfect' collected pairs of one adapter into gold.

    limit caps how many *new* pairs are judged this run (0 = all). dry_run judges +
    counts but writes nothing. Returns {collected, judged, would_promote, promoted}.
    """
    pool = Pool(settings.pool_path, getattr(spec, "pool", None) or spec.name)
    collected = [e for e in pool._read(pool.collected_file)
                 if isinstance(e.get("messages"), list)]
    cache_file = pool.dir / ".distill_cache.json"
    cache: dict = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    promoted, judged = [], 0
    for ex in collected:
        msgs = ex.get("messages") or []
        user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
        answer = next((m.get("content", "") for m in reversed(msgs)
                       if m.get("role") == "assistant"), "")
        if not user.strip() or not answer.strip():
            continue
        h = _hash_messages(msgs)
        verdict = cache.get(h)
        # Never trust a cached ERROR verdict — a transient ollama hiccup used to be
        # cached forever as score 0, permanently blocking a good pair from gold.
        if verdict is not None and "judge" in str(verdict.get("reason", ""))[:20] \
                and "error" in str(verdict.get("reason", "")):
            verdict = None
        if verdict is None:
            verdict = _judge.score_pair_quality(spec, user, answer, settings)
            reason = str(verdict.get("reason", ""))
            if not ("judge" in reason[:20] and "error" in reason):
                cache[h] = verdict   # cache only real judgments
            judged += 1
        if verdict.get("perfect"):
            # Catalog gate: a judge can rate a fluent answer "perfect" even when it
            # cites an invented SKU. Block such pairs from gold (no-ops when the
            # catalog snapshot is absent or the answer cites no SKUs).
            grounding = _catalog.check_grounding(answer)
            if not grounding.get("ok", True):
                continue
            promoted.append({
                "messages": msgs,
                "_meta": {**(ex.get("_meta") or {}), "source": "distill",
                          "verified": True, "origin": "self",
                          "quality_score": verdict.get("score"),
                          "grounded_skus": grounding.get("cited", [])},
            })
        if limit and judged >= limit:
            break

    added = 0
    if not dry_run:
        try:
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
        if promoted:
            res = pool.append(promoted, split="gold")
            added = res.get("added", 0)
            log(f"distill: promoted {added} pair(s) to gold "
                f"(judged {judged}, {res.get('skipped', 0)} dupes/invalid)")
    return {"collected": len(collected), "judged": judged,
            "would_promote": len(promoted), "promoted": added}
