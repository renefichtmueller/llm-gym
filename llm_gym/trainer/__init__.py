"""Pick a training backend.

`auto` prefers MLX on Apple Silicon, then PEFT (NVIDIA/CPU), then a simulate
fallback so the flow always works. Force one with the LLMGYM_BACKEND setting.
"""
from __future__ import annotations

from .base import SimulateBackend, TrainerBackend, TrainResult, resolve_base
from .mlx_backend import MLXBackend
from .peft_backend import PeftBackend

__all__ = ["select_backend", "TrainerBackend", "TrainResult", "resolve_base"]


def select_backend(preference: str = "auto") -> TrainerBackend:
    if preference == "simulate":
        return SimulateBackend()
    mlx, peft = MLXBackend(), PeftBackend()
    if preference == "mlx":
        return mlx if mlx.available() else SimulateBackend()
    if preference == "peft":
        return peft if peft.available() else SimulateBackend()
    # auto
    if mlx.available():
        return mlx
    if peft.available():
        return peft
    return SimulateBackend()
