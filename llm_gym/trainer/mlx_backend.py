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
import threading
import time
from pathlib import Path

import yaml

from .base import LogFn, TrainResult, resolve_base

# Total physical RAM (for turning a profile's mem_fraction into a byte budget).
try:
    _TOTAL_RAM = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
except (ValueError, OSError):
    _TOTAL_RAM = 0

_VAL_RE = re.compile(r"Iter\s+(\d+):.*?Val loss\s+([0-9.]+)", re.IGNORECASE)


def _valid_safetensors(p: Path) -> bool:
    """True if the file's 8-byte little-endian header length fits inside the file.
    Catches the truncated-write corruption (e.g. the 17-byte adapters left by a
    crash mid-copy) before it is fed to --resume or shipped."""
    try:
        sz = p.stat().st_size
        if sz < 100:
            return False
        with p.open("rb") as f:
            n = int.from_bytes(f.read(8), "little")
        return 0 < n <= 50_000_000 and 8 + n <= sz
    except OSError:
        return False


def _finite_safetensors(p: Path) -> bool:
    """True if every tensor is finite (no NaN/Inf). A NaN-poisoned adapter is
    format-valid yet useless: resuming from it produces NaN loss from iter 1, and
    shipping it serves garbage. Scans values, not just the header. (Root cause of
    a recurring class of failure where all tensors were NaN.)"""
    try:
        import numpy as np
        from safetensors import safe_open
    except Exception:
        return True   # scanner unavailable → don't block; the format check still applies
    try:
        with safe_open(str(p), framework="numpy") as f:
            for k in f.keys():
                if not np.isfinite(f.get_tensor(k)).all():
                    return False
        return True
    except Exception:
        return False   # unreadable as safetensors → treat as unhealthy


def _healthy_safetensors(p: Path) -> bool:
    """Format-valid AND finite — safe to resume from or ship."""
    return _valid_safetensors(p) and _finite_safetensors(p)


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy src->dst crash-safely: write a temp in the same dir, fsync, os.replace
    (atomic on POSIX) so a crash never leaves a half-written adapter at dst."""
    tmp = dst.with_name(dst.name + ".tmp")
    with open(src, "rb") as r, open(tmp, "wb") as w:
        shutil.copyfileobj(r, w)
        w.flush()
        os.fsync(w.fileno())
    os.replace(tmp, dst)


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
              profile: dict | None = None,
              training_mode: str = "lora") -> TrainResult:
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
            # mlx_lm.lora's own --fine-tune-type flag: lora (default) | dora | full.
            # "full" trains every weight instead of a low-rank delta -- NOT exercised
            # against real Apple Silicon hardware by this codebase (no such host was
            # available to test against); mlx_lm is documented to keep writing
            # checkpoints under adapter_path with the same naming either way, but
            # treat a "full" run here as best-effort until confirmed on real hardware.
            "fine_tune_type": training_mode,
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
        }
        if training_mode == "lora":
            config["lora_parameters"] = {
                "keys": lora_keys, "rank": rank,
                "scale": scale, "dropout": dropout,
            }
        else:
            log("[mlx] full fine-tune: every weight is trainable — needs far more "
                "memory than LoRA. This path is best-effort (not verified against "
                "real Apple Silicon hardware); watch closely on the first run.")
        cfg_path = data_dir / "lora_config.yaml"
        cfg_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        log(f"[mlx] mode={training_mode} model={repo} iters={iters} "
            f"layers={num_layers} batch={batch_size} profile={prof.get('name', '-')}"
            + (f" rank={rank} scale={scale}" if training_mode == "lora" else ""))

        # Throttle so the machine stays usable: cap worker threads, lower priority,
        # and hand MLX a memory ceiling (share of RAM) it shouldn't exceed.
        env = dict(os.environ)
        threads = str(prof.get("threads", 4))
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                    "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[var] = threads
        # Always hand MLX a memory ceiling so even an un-profiled run can't grab all
        # of unified memory and OOM-panic the host. Default to 40% of RAM.
        frac = float(prof.get("mem_fraction", 0)) or 0.40
        if _TOTAL_RAM:
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
            sz = prior.stat().st_size
            if _healthy_safetensors(prior):
                cmd += ["--resume-adapter-file", str(prior)]
                log(f"[mlx] resuming from {prior.name}")
            else:
                log(f"[mlx] prior adapter unhealthy (corrupt or NaN) → training "
                    f"from scratch, quarantining {prior.name}")
                try:
                    prior.rename(out_dir / f"adapters.safetensors.corrupt-{int(time.time())}")
                except OSError:
                    pass
                log(f"[mlx] prior adapter corrupt ({sz}b) — quarantined, training fresh")
        # A fresh run must not inherit numbered checkpoints from an earlier run:
        # selection scans {iter:07d}_adapters.safetensors on disk, and a stale file
        # from an older run at a matching iter would ship silently (wrong weights).
        for old in out_dir.glob("*_adapters.safetensors"):
            try:
                old.unlink()
            except OSError:
                pass
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                env=env, preexec_fn=_lower_priority)
        # Watchdog: kill the subprocess if it emits no output for stall_minutes
        # (hung on model/data load or GPU contention) or exceeds max_minutes total,
        # so a dead run fails cleanly instead of wedging the queue (the 111-min hang).
        stall_s = int(prof.get("stall_minutes", 25)) * 60
        max_s = int(prof.get("max_minutes", 720)) * 60
        _last = [time.time()]
        _start = time.time()
        killed_by_wd = [False]

        def _watchdog() -> None:
            while proc.poll() is None:
                time.sleep(15)
                now = time.time()
                if now - _last[0] > stall_s or now - _start > max_s:
                    killed_by_wd[0] = True
                    why = "stalled" if now - _last[0] > stall_s else "max runtime"
                    log(f"[mlx] WATCHDOG ({why}): killing after "
                        f"{int((now - _last[0]) / 60)}min no output / "
                        f"{int((now - _start) / 60)}min total")
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    return

        threading.Thread(target=_watchdog, daemon=True).start()
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
            _last[0] = time.time()   # heartbeat for the watchdog
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

        if killed_by_wd[0]:
            return TrainResult(False, self.name, iters, None, None, None,
                               str(out_dir),
                               "watchdog: training stalled / exceeded max runtime")
        # A clean early-stop terminates the process (non-zero rc) on purpose.
        if proc.returncode not in (0, None) and not stopped_early:
            return TrainResult(False, self.name, iters, None, None, None,
                               str(out_dir), f"mlx_lm exited {proc.returncode}")

        # Select the best (lowest val loss) checkpoint, not the final one.
        # Only evals whose checkpoint file exists AND is healthy are candidates:
        # the val log also contains evals with no saved file (e.g. the iter-1
        # baseline), which previously could "win" silently — the final weights
        # shipped while verdict/history graded a checkpoint that never existed.
        selected_iter = None
        selected = None
        final = val_points[-1][1] if val_points else None
        if val_points:
            shippable = [(it, vl) for it, vl in val_points
                         if _healthy_safetensors(out_dir / f"{it:07d}_adapters.safetensors")]
            if shippable:
                selected_iter, selected = min(shippable, key=lambda p: p[1])
                _atomic_copy(out_dir / f"{selected_iter:07d}_adapters.safetensors",
                             out_dir / "adapters.safetensors")
            else:
                # Nothing shippable among the evals — the final weights (written by
                # mlx itself) ship; report the final eval so verdict/history stay
                # truthful about what actually shipped.
                log("[mlx] no saved checkpoint matches a val eval — shipping final "
                    "weights, selected_* = final eval")
                selected_iter, selected = val_points[-1]

        final_adapter = out_dir / "adapters.safetensors"
        ok = final_adapter.exists() and _healthy_safetensors(final_adapter)
        return TrainResult(
            ok=ok, backend=self.name, iters=iters,
            selected_val_loss=selected, final_val_loss=final,
            selected_iter=selected_iter, adapter_path=str(out_dir),
            message="" if ok else "No valid adapter file produced.",
        )
