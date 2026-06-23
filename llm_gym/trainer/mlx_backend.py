"""Apple Silicon LoRA trainer using mlx-lm.

Wraps `python -m mlx_lm lora`. Generates an rsLoRA config (all linear layers),
runs the subprocess, parses validation loss from the log, and selects the best
checkpoint rather than the final one (which is often worse after overfitting).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from .base import LogFn, TrainResult, resolve_base

# Total physical RAM (for turning a profile's mem_fraction into a byte budget).
try:
    _TOTAL_RAM = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
except (ValueError, OSError):
    _TOTAL_RAM = 0

_VAL_RE = re.compile(r"Iter\s+(\d+):.*?Val loss\s+([0-9.]+)", re.IGNORECASE)


class MLXBackend:
    name = "mlx"

    def available(self) -> bool:
        try:
            import mlx_lm  # noqa: F401
            return True
        except Exception:
            return False

    def train(self, *, base_model: str, data_dir: Path, out_dir: Path,
              rank: int, scale: float, dropout: float, learning_rate: float,
              iters: int, lora_keys: list[str], log: LogFn,
              save_every: int = 50, resume: bool = False,
              profile: dict | None = None) -> TrainResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        repo = resolve_base(base_model, "mlx")
        prof = profile or {}
        num_layers = int(prof.get("num_layers", 16))
        batch_size = int(prof.get("batch_size", 1))
        grad_accum = int(prof.get("grad_accum", 1))
        val_batches = int(prof.get("val_batches", 25))

        config = {
            "model": repo,
            "train": True,
            "data": str(data_dir),
            "adapter_path": str(out_dir),
            "fine_tune_type": "lora",
            "num_layers": num_layers,        # top-N layers = the big memory lever
            "iters": iters,
            "learning_rate": learning_rate,
            "batch_size": batch_size,
            "grad_accumulation_steps": grad_accum,  # effective batch = batch * accum
            "grad_checkpoint": True,
            "save_every": save_every,
            "steps_per_eval": save_every,
            "val_batches": val_batches,
            "max_seq_length": 2048,
            # Match the shared-queue path (and both old trainers): AdamW with a
            # cosine-decay-to-LR/10 schedule and a short warmup. Without this
            # mlx_lm trains at a flat LR with plain Adam — measurably worse.
            "optimizer": "adamw",
            "lr_schedule": {
                "name": "cosine_decay",
                "warmup": min(100, max(10, iters // 20)),
                "warmup_init": 1e-7,
                "arguments": [learning_rate, iters, learning_rate / 10],
            },
            "lora_parameters": {
                "keys": lora_keys, "rank": rank,
                "scale": scale, "dropout": dropout,
            },
        }
        cfg_path = data_dir / "lora_config.yaml"
        cfg_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        log(f"[mlx] model={repo} rank={rank} scale={scale} iters={iters} "
            f"layers={num_layers} batch={batch_size} profile={prof.get('name', '-')}")

        # Throttle so the machine stays usable: cap worker threads, lower priority,
        # and hand MLX a memory ceiling (share of RAM) it shouldn't exceed.
        env = dict(os.environ)
        threads = str(prof.get("threads", 4))
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                    "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[var] = threads
        frac = float(prof.get("mem_fraction", 0))
        if frac and _TOTAL_RAM:
            env["LLMGYM_MLX_MEM_BYTES"] = str(int(_TOTAL_RAM * frac))
        nice_n = int(prof.get("nice", 10))

        def _lower_priority() -> None:
            try:
                os.nice(nice_n)
            except OSError:
                pass

        # Route through our wrapper (which applies the MLX memory cap) when one is
        # set; otherwise call mlx_lm directly.
        entry = (["-m", "llm_gym.trainer._mlx_lora"] if env.get("LLMGYM_MLX_MEM_BYTES")
                 else ["-m", "mlx_lm", "lora"])
        cmd = [sys.executable, *entry, "-c", str(cfg_path), "--mask-prompt"]
        prior = out_dir / "adapters.safetensors"
        if resume and prior.exists():
            cmd += ["--resume-adapter-file", str(prior)]
            log(f"[mlx] resuming from {prior.name}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                env=env, preexec_fn=_lower_priority)
        val_points: list[tuple[int, float]] = []
        # Early stopping: kill the run once validation loss stops improving, so a
        # small dataset doesn't burn the whole iter budget overfitting (train loss
        # → 0). Best-checkpoint selection below still returns the lowest-val-loss
        # adapter, so stopping early never ships a worse model.
        patience = int(prof.get("early_stop_patience", 4))
        min_delta = float(prof.get("early_stop_min_delta", 0.005))
        min_iters_es = max(save_every * 2, iters // 10)   # let it warm up first
        best_val, since_improve, stopped_early = float("inf"), 0, False
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            log(line)
            m = _VAL_RE.search(line)
            if m:
                it, vl = int(m.group(1)), float(m.group(2))
                val_points.append((it, vl))
                if patience > 0 and it >= min_iters_es:
                    if vl < best_val - min_delta:
                        best_val, since_improve = vl, 0
                    else:
                        since_improve += 1
                        if since_improve >= patience:
                            log(f"[mlx] early-stop @ iter {it}: no val-loss "
                                f"improvement for {patience} evals (best {best_val:.4f})")
                            stopped_early = True
                            proc.terminate()
                            break
        if stopped_early:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            proc.wait()

        # A clean early-stop terminates the process (non-zero rc) on purpose.
        if proc.returncode not in (0, None) and not stopped_early:
            return TrainResult(False, self.name, iters, None, None, None,
                               str(out_dir), f"mlx_lm exited {proc.returncode}")

        # Select the best (lowest val loss) checkpoint, not the final one.
        selected_iter = None
        selected = None
        final = val_points[-1][1] if val_points else None
        if val_points:
            selected_iter, selected = min(val_points, key=lambda p: p[1])
            ckpt = out_dir / f"{selected_iter:07d}_adapters.safetensors"
            if ckpt.exists():
                shutil.copyfile(ckpt, out_dir / "adapters.safetensors")

        ok = (out_dir / "adapters.safetensors").exists()
        return TrainResult(
            ok=ok, backend=self.name, iters=iters,
            selected_val_loss=selected, final_val_loss=final,
            selected_iter=selected_iter, adapter_path=str(out_dir),
            message="" if ok else "No adapter file produced.",
        )
