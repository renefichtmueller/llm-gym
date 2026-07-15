"""Trainer backend interface + base-model resolution + simulate fallback."""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

# Ollama tags are short. The training engines need real model repos. This map
# translates the gym's base-model id into the repo each backend can load.
# Extend it from Settings if you add models.
BASE_REPOS: dict[str, dict[str, str]] = {
    "qwen2.5:3b": {
        "mlx": "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "hf": "Qwen/Qwen2.5-3B-Instruct",
    },
    "qwen2.5:7b": {
        "mlx": "mlx-community/Qwen2.5-7B-Instruct-4bit",
        "hf": "Qwen/Qwen2.5-7B-Instruct",
    },
    "qwen2.5:14b": {
        "mlx": "mlx-community/Qwen2.5-14B-Instruct-4bit",
        "hf": "Qwen/Qwen2.5-14B-Instruct",
    },
}


def resolve_base(base_model: str, kind: str) -> str:
    """Map an Ollama-style tag to a backend repo. Falls back to the tag itself
    (so custom/local models still work if the engine understands them)."""
    return BASE_REPOS.get(base_model, {}).get(kind, base_model)


@dataclass
class TrainResult:
    ok: bool
    backend: str
    iters: int
    selected_val_loss: float | None
    final_val_loss: float | None
    selected_iter: int | None
    adapter_path: str
    message: str = ""


LogFn = Callable[[str], None]


class TrainerBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def train(self, *, base_model: str, data_dir: Path, out_dir: Path,
              rank: int, scale: float, dropout: float, learning_rate: float,
              iters: int, lora_keys: list[str], log: LogFn,
              save_every: int = 50, resume: bool = False,
              profile: dict | None = None,
              training_mode: str = "lora") -> TrainResult: ...


def _count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


class SimulateBackend:
    """Runs the whole flow without a GPU: no real training, plausible metrics,
    a placeholder artifact. Lets newcomers see queue -> train -> assign end to
    end before installing a heavy backend."""

    name = "simulate"

    def available(self) -> bool:
        return True

    def train(self, *, base_model: str, data_dir: Path, out_dir: Path,
              rank: int, scale: float, dropout: float, learning_rate: float,
              iters: int, lora_keys: list[str], log: LogFn,
              save_every: int = 50, resume: bool = False,
              profile: dict | None = None,
              training_mode: str = "lora") -> TrainResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        n = _count(data_dir / "train.jsonl")
        log(f"[simulate] base={base_model} examples={n} rank={rank} iters={iters} mode={training_mode}")
        rng = random.Random(hash((base_model, n, iters)) & 0xFFFFFFFF)
        selected = round(0.12 + rng.random() * 0.25, 4)
        final = round(selected + rng.random() * 0.05, 4)
        selected_iter = max(50, int(iters * (0.5 + rng.random() * 0.4)))
        for step in range(0, iters + 1, max(50, iters // 8)):
            log(f"[simulate] iter {step}/{iters}  val_loss~{round(selected + rng.random()*0.05, 4)}")
            time.sleep(0.5)   # pace it so the live view shows progress (teaching backend)
        (out_dir / "adapters.safetensors").write_bytes(b"SIMULATED_ADAPTER")
        (out_dir / "adapter_config.json").write_text(json.dumps({
            "base_model": base_model, "rank": rank, "scale": scale,
            "simulated": True}, indent=2), encoding="utf-8")
        log("[simulate] done — install mlx-lm or torch+peft for real training.")
        return TrainResult(
            ok=True, backend=self.name, iters=iters,
            selected_val_loss=selected, final_val_loss=final,
            selected_iter=selected_iter, adapter_path=str(out_dir),
            message="Simulated run (no real weights were trained).",
        )
