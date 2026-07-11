"""Cooldown calculator + adaptive early-stop.

Pure functions, no I/O, so they are easy to test and reason about. The queue
calls these to decide when a lane may train again and with how many iterations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def cooldown_minutes(seed_only: bool, *, seed: int = 30, full: int = 480,
                     lo: int = 30, hi: int = 720) -> int:
    """Cooldown for the next run, clamped to [lo, hi]."""
    base = seed if seed_only else full
    return max(lo, min(hi, base))


def next_run_epoch(now_epoch: float, a_job_is_running: bool, cooldown_min: int,
                   queue_position: int = 0) -> float:
    """When the next run should start (unix seconds).

    If a job is running, wait one cooldown. Each further queued job is staggered
    by an additional cooldown so they never overlap.
    """
    if a_job_is_running:
        first = now_epoch + cooldown_min * 60
    else:
        first = now_epoch
    return first + queue_position * cooldown_min * 60


@dataclass
class EarlyStop:
    iters: int
    reason: str | None


def adaptive_iters(
    history: list[dict],
    configured_iters: int,
    *,
    min_iters: int = 100,
    overfit_ratio: float = 1.4,   # final must be >40% worse than the best
    overfit_abs: float = 0.1,     # ...and at least 0.1 worse in absolute terms
    early_frac: float = 0.7,      # ...with the best reached before 70% of iters
    buffer: float = 1.2,          # train 20% past the typical peak
    window: int = 4,              # look at the last N runs
) -> EarlyStop:
    """Cap iterations when a lane keeps overfitting.

    `history` items look like:
        {"selected_val_loss": float, "final_val_loss": float,
         "selected_iter": int, "iters": int}
    Returns the (possibly reduced) iteration budget and a human reason, or the
    configured value with reason=None when nothing needs capping.
    """
    if configured_iters <= min_iters:
        return EarlyStop(configured_iters, None)

    peaks: list[int] = []
    for run in history[-window:]:
        sel = run.get("selected_val_loss")
        fin = run.get("final_val_loss")
        sel_it = run.get("selected_iter")
        its = run.get("iters")
        if None in (sel, fin, sel_it, its) or not its:
            continue
        overfit = (fin - sel) > overfit_abs and fin > sel * overfit_ratio
        early = sel_it < its * early_frac
        if overfit and early:
            peaks.append(int(sel_it))

    if not peaks:
        return EarlyStop(configured_iters, None)

    peaks.sort()
    median_peak = peaks[len(peaks) // 2]
    capped = max(min_iters, math.ceil(median_peak * buffer / 50) * 50)
    if capped >= configured_iters:
        return EarlyStop(configured_iters, None)
    return EarlyStop(
        capped,
        f"Auto early-stop: lane overfit recently (peak ~{median_peak}); "
        f"capping {configured_iters} -> {capped} iters.",
    )
