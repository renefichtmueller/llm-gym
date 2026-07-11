"""Gold-standard input.

A gold pair is the highest-trust training signal. It is only gold when BOTH
hold:

    1. The solution was actually executed and succeeded (executed_ok = True).
    2. The result was independently verified (verified = True).

Anything that fails either check is NOT gold. Failures are still useful: keep
them as negative signal in errors.jsonl. This mirrors how a real gym earns
trustworthy data instead of scraping plausible-looking text.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pool import Pool


@dataclass
class GoldEntry:
    problem_prompt: str          # what the model is asked
    solution_text: str           # the verified-correct answer/action
    executed_ok: bool            # condition 1
    verified: bool               # condition 2
    source: str = "manual"
    model: str = ""              # which model produced the candidate, if any
    quality_score: int = 96
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def is_gold(self) -> bool:
        return bool(self.executed_ok and self.verified)


def to_example(entry: GoldEntry) -> dict:
    messages = []
    if entry.system_prompt:
        messages.append({"role": "system", "content": entry.system_prompt})
    messages.append({"role": "user", "content": entry.problem_prompt})
    messages.append({"role": "assistant", "content": entry.solution_text})
    return {
        "messages": messages,
        "_meta": {
            "source": entry.source, "verified": True, "executed_ok": True,
            "quality_score": entry.quality_score, "model": entry.model,
            "tags": entry.tags,
        },
    }


def add_gold(pool: Pool, entry: GoldEntry) -> dict:
    """Write a gold pair to the pool's gold.jsonl. Rejects non-gold input."""
    if not entry.is_gold:
        return {
            "ok": False,
            "reason": "Not gold: requires executed_ok AND verified. "
                      "Record this as a failure/negative example instead.",
        }
    result = pool.append([to_example(entry)], split="gold")
    return {"ok": True, **result}
