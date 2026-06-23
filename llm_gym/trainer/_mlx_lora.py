"""Thin wrapper that caps MLX's memory before running `mlx_lm lora`.

Run exactly like `python -m mlx_lm lora <args>`, but as
`python -m llm_gym.trainer._mlx_lora <args>`. If LLMGYM_MLX_MEM_BYTES is set it
hands MLX a hard memory ceiling first (so a training run can't eat all of unified
memory and freeze the machine), then delegates to mlx_lm unchanged.

The cap can only be set in the process that actually runs MLX, which is why this
lives here rather than in the parent gym process.
"""
from __future__ import annotations

import os
import runpy
import sys


def _apply_cap() -> None:
    limit = int(os.environ.get("LLMGYM_MLX_MEM_BYTES", "0") or "0")
    if limit <= 0:
        return
    try:
        import mlx.core as mx
        if hasattr(mx, "set_memory_limit"):
            mx.set_memory_limit(limit)
        elif hasattr(mx, "metal") and hasattr(mx.metal, "set_memory_limit"):
            mx.metal.set_memory_limit(limit)
    except Exception:  # noqa: BLE001 — never block training on the cap
        pass


def main() -> None:
    _apply_cap()
    # Delegate to `python -m mlx_lm lora <args>` exactly.
    sys.argv = ["mlx_lm", "lora", *sys.argv[1:]]
    runpy.run_module("mlx_lm", run_name="__main__")


if __name__ == "__main__":
    main()
