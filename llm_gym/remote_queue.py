"""Client for an external GPU training queue (the central serializer).

Instead of training locally, the gym can hand each job to one shared queue that
runs exactly one GPU job at a time — so several trainers (this gym, others) line
up in one circuit and never run two trainings at once (no OOM).

The wire contract mirrors what the reference `llm-gym-queue` expects (and what a
sibling trainer already uses), so this gym slots in as just another producer:

    POST /jobs   {type:"adapter_only", source, lane, priority,
                  params:{config_path, mask_prompt}, callback_url}  -> {job_id}
    GET  /jobs/{id}                  -> {status, result:{exit_code, val_loss, ...}}
    GET  /jobs/{id}/log              -> text (live tail)
    GET  /health                     -> 200 (no auth)

It deliberately exposes the same small surface the local TrainingQueue does
(enqueue / job / job_log), so the pipeline can drive either one unchanged.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import httpx

from .adapters import AdapterStore
from .config import Settings
from .pool import Pool
from .queue import _optimal_cooldown, _verdict
from .trainer.base import BASE_REPOS, resolve_base

# Layers fine-tuned + per-step batch: the memory levers that keep a run from
# eating all of unified memory. Kept conservative so the box stays usable.
_NUM_LAYERS = 16
_BATCH_SIZE = 1
_MAX_SEQ = 2048
_DEFAULT_LORA_KEYS = [
    "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj",
    "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj",
]


def _mlx_repo(base_model: str) -> str:
    """The mlx-community repo the queue's executor loads for this base."""
    if base_model in BASE_REPOS:
        return resolve_base(base_model, "mlx")
    # Already a full repo id (e.g. mlx-community/...): pass through.
    return base_model


def build_lora_config(spec, data_dir: Path, adapter_path: Path,
                      profile: dict | None = None,
                      rank: int | None = None, scale: float | None = None) -> dict:
    """The mlx_lm `-c` config, in the shape the queue's adapter_only path runs.
    All hyper-parameters live in here (the queue runs `mlx_lm lora -c <file>`).
    rank/scale default to the spec's values; pass them to apply a per-lane rank."""
    iters = int(spec.iters)
    lr = float(spec.learning_rate)
    prof = profile or {}
    rank = int(rank if rank is not None else spec.lora_rank)
    scale = float(scale if scale is not None else spec.lora_scale)
    return {
        "model": _mlx_repo(spec.base_model),
        "train": True,
        "data": str(data_dir),
        "adapter_path": str(adapter_path),
        "fine_tune_type": "lora",
        "num_layers": int(prof.get("num_layers", _NUM_LAYERS)),
        "iters": iters,
        "batch_size": int(prof.get("batch_size", _BATCH_SIZE)),
        "grad_accumulation_steps": int(prof.get("grad_accum", 1)),
        "max_seq_length": _MAX_SEQ,
        "learning_rate": lr,
        "optimizer": "adamw",
        "grad_checkpoint": True,
        "save_every": max(10, iters // 10),
        "steps_per_eval": max(50, iters // 6),
        "val_batches": -1,
        "lora_parameters": {
            "keys": _DEFAULT_LORA_KEYS,
            "rank": rank,
            "scale": scale,
            "dropout": float(spec.lora_dropout),
        },
        "lr_schedule": {
            "name": "cosine_decay",
            "warmup": min(100, max(10, iters // 20)),
            "warmup_init": 1e-7,
            "arguments": [lr, iters, lr / 10],
        },
    }


class RemoteQueue:
    """Talks to the central training queue. Same enqueue/job/job_log surface as
    the local TrainingQueue, so the pipeline can use either interchangeably."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        rq = settings.remote_queue
        self.url = rq.url.rstrip("/")
        self.token = rq.token()            # read from env at call time, never stored
        self.source = rq.source
        self.priority = rq.priority
        self.timeout = rq.timeout

    # --- helpers ---
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["X-Gym-Token"] = self.token
        return h

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as c:
                return c.get(f"{self.url}/health").status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def _prepare(self, spec) -> Path:
        """Build merged data + the mlx_lm config for this adapter; return the
        config path the queue's executor will run."""
        pool = Pool(self.settings.pool_path, spec.pool or spec.name)
        train_rows, valid_rows, _ = pool.split_for_training(
            min_tier=spec.min_tier, val_ratio=self.settings.training.val_split)
        min_ex = self.settings.training.min_examples
        if len(train_rows) < min_ex:
            raise ValueError(f"only {len(train_rows)} training examples "
                             f"(need >= {min_ex}) — collect more before training.")
        ok_d, why_d = Pool.distinctness_ok(
            train_rows, self.settings.training.min_answer_distinct_ratio)
        if not ok_d:
            raise ValueError(f"answers too repetitive ({why_d}) — add answer "
                             "variety before training.")
        run_dir = self.settings.runs_path / f"remote-{spec.name}"
        (run_dir).mkdir(parents=True, exist_ok=True)
        # mlx_lm expects train.jsonl + valid.jsonl in the data dir.
        Pool.write_jsonl(run_dir / "train.jsonl", train_rows)
        Pool.write_jsonl(run_dir / "valid.jsonl", valid_rows)
        store = AdapterStore(self.settings.adapters_path)
        rank, scale = self.settings.effective_lora(spec)
        cfg = build_lora_config(spec, run_dir, store.artifact_dir(spec.name),
                                profile=self.settings.profile(), rank=rank, scale=scale)
        cfg_path = run_dir / "lora_config.yaml"   # JSON is valid YAML; mlx_lm reads it
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return cfg_path

    # --- TrainingQueue-compatible surface ---
    def enqueue(self, adapter: str, seed_only: bool = False) -> dict:
        store = AdapterStore(self.settings.adapters_path)
        spec = store.load(adapter)
        if spec is None:
            return {"ok": False, "error": f"No adapter spec named '{adapter}'."}
        try:
            cfg_path = self._prepare(spec)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"prepare failed: {exc}"}
        body = {
            "type": "adapter_only", "source": self.source, "lane": adapter,
            "priority": self.priority,
            "params": {"config_path": str(cfg_path), "mask_prompt": True},
            "callback_url": "",
        }
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(f"{self.url}/jobs", headers=self._headers(), json=body)
            if r.status_code not in (200, 201):
                return {"ok": False, "error": f"queue {r.status_code}: {r.text[:200]}"}
            jid = r.json().get("job_id")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"queue unreachable: {exc}"}
        return {"ok": True, "job_id": jid, "run_at": time.time(), "starts_in_min": 0,
                "remote": True}

    def job(self, job_id) -> dict | None:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(f"{self.url}/jobs/{job_id}", headers=self._headers())
            if r.status_code != 200:
                return None
            j = r.json()
        except Exception:  # noqa: BLE001
            return None
        status = j.get("status", "pending")
        result = j.get("result") or {}
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:  # noqa: BLE001
                result = {}
        # Adapt the queue's result shape to what the pipeline reads from a local
        # job (ok / selected_val_loss / verdict ...).
        adapted = None
        if status in ("done", "failed", "cancelled"):
            ok = status == "done" and int(result.get("exit_code", 1) or 1) == 0
            sel = result.get("val_loss")
            fin = result.get("final_val_loss", sel)
            it = result.get("iterations") or result.get("iters")
            sel_it = result.get("best_iter")
            verdict = _verdict(sel, fin, sel_it, it, ok)
            adapted = json.dumps({
                "ok": ok, "backend": "mlx-queue", "iters": it,
                "selected_val_loss": sel, "final_val_loss": fin,
                "selected_iter": sel_it, "verdict": verdict,
                "adapter_path": result.get("adapter_path", ""),
                "base_model": result.get("base_model", ""),
                "optimal_cooldown_min": _optimal_cooldown(
                    self.settings.cooldown, verdict, ok),
            })
        return {"id": job_id, "adapter": j.get("lane", ""), "status": status,
                "result": adapted, "log_path": ""}

    def job_log(self, job_id) -> str:
        # The queue serves the log at GET /jobs/{id}/log (chunked text/plain), not
        # as a field on the job body. Hitting the job body returned nothing.
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(f"{self.url}/jobs/{job_id}/log", headers=self._headers())
            return r.text if r.status_code == 200 else ""
        except Exception:  # noqa: BLE001
            return ""

    def has_active_job(self, adapter: str) -> bool:
        return False   # the remote queue tracks this itself; gym doesn't gate it

    def status(self) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(f"{self.url}/status", headers=self._headers())
            return r.json() if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001
            return {}
