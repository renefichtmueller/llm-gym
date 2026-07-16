"""The single training queue.

One worker, one GPU job at a time — by design. Jobs are serialized through a
SQLite table and a single background thread, so two adapters can never train at
once (no overlap, no out-of-memory). The cooldown calculator decides when a lane
becomes eligible again; the adaptive early-stop trims iterations for lanes that
overfit.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import cooldown as cd
from .adapters import AdapterStore
from .config import Settings
from .pool import Pool
from .trainer import select_backend

# Below this, a full fine-tune run (whose checkpoints are full-model sized, not a
# small LoRA delta) refuses to start rather than risk filling the disk mid-run.
_MIN_FREE_GB_FULL_FINETUNE = 20.0

# Hard ceilings on iters for DPO — a 'running' job can't be cancelled via the
# API, and _epoch_capped_iters() alone is a no-op when max_epochs=0. Well above
# what a real run needs; just bounds the worst case. Full mode gets a much
# tighter ceiling since it's the single worker's slowest possible job.
_MAX_ITERS_FULL_DPO = 2000
_MAX_ITERS_LORA_DPO = 20_000


def _in_quiet_hours(now_epoch: float, start_h: int, end_h: int) -> bool:
    """True if the local hour at now_epoch is inside the no-training window. Wraps
    midnight. start == end disables (always False)."""
    if start_h == end_h:
        return False
    h = time.localtime(now_epoch).tm_hour
    return start_h <= h < end_h if start_h < end_h else (h >= start_h or h < end_h)


def _epoch_capped_iters(iters: int, n_train: int, batch: int,
                        max_epochs: int, min_iters: int) -> int:
    """Bound iterations so a small pool can't be trained for hundreds of epochs —
    the core overtraining risk. epochs = iters * batch / n_train. Never caps below
    min_iters. A no-op when the data is large enough that iters stays under the cap."""
    if not (n_train and max_epochs):
        return iters
    cap = max(min_iters, int(max_epochs * n_train / max(1, batch)))
    return min(iters, cap)


@contextlib.contextmanager
def _gpu_lease(lease_path: str, owner: str, info: str):
    """Hold the shared OS-level GPU lease while training, if a `gpu_queue` module
    is importable from lease_path (the trainer host ships ~/gpu_queue.py). This
    serialises the gym's GPU work with any other GPU trainers on the host so a
    second job can't trigger an out-of-memory. A no-op when lease_path is empty or
    the module isn't present (e.g. a laptop where the gym is the only GPU user)."""
    gq = None
    if lease_path:
        p = os.path.expanduser(lease_path)
        if p not in sys.path:
            sys.path.insert(0, p)
        try:
            import gpu_queue as _gq
            gq = _gq
        except Exception:  # noqa: BLE001 — absence just means no shared lease here
            gq = None
    if gq is None:
        yield
        return
    with gq.lease(owner, info):
        yield


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL,            -- pending | running | done | failed | cancelled
    seed_only INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    run_at REAL NOT NULL,
    started REAL,
    finished REAL,
    log_path TEXT,
    result TEXT,                     -- JSON TrainResult + verdict
    priority INTEGER NOT NULL DEFAULT 0,  -- higher jumps the line (interactive)
    method TEXT NOT NULL DEFAULT 'sft'    -- sft (chat examples) | dpo (preference pairs, RLHF)
);
"""


class SecondInstanceError(RuntimeError):
    """Raised when a second gym process tries to start while one is already
    alive — see TrainingQueue's single-instance guard for why this matters."""


class TrainingQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = settings.db_file
        self.runs_dir = settings.runs_path
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        # Single-instance guard — MUST be the very first thing, before touching
        # the jobs table at all. Found live: a second gym process (launchd vs. a
        # manually-started one, or a launchd retry racing a bind failure) reached
        # this constructor, ran the "mark stale running jobs failed" sweep below,
        # and killed a training job that was actually alive and progressing under
        # the FIRST instance — whose thread then kept running as an unsupervised
        # "ghost" (nothing re-checks job status once inside _run()). This must
        # never happen again: refuse to become a second queue at all. A held,
        # non-blocking flock on a dedicated lock file makes that a hard OS-level
        # guarantee, independent of whatever the HTTP port bind does — reused
        # by anything else than sequential test runs of the same process.
        self._instance_lock_path = self.db_path.parent / ".gym_instance.lock"
        self._instance_lock_fd = open(self._instance_lock_path, "w")
        try:
            fcntl.flock(self._instance_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            self._instance_lock_fd.close()
            raise SecondInstanceError(
                "another llm_gym process already holds the instance lock "
                f"({self._instance_lock_path}) — refusing to start a second "
                "queue, which would corrupt the running one's job bookkeeping "
                "and leave an unsupervised ghost training process") from exc
        self._instance_lock_fd.write(str(os.getpid()))
        self._instance_lock_fd.flush()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL + a busy timeout: the queue and pipeline hold separate connections
        # to this same file, so let writers retry instead of failing instantly
        # with "database is locked".
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)
        # Migrate older DBs that predate the priority/method columns.
        for ddl in ("ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE jobs ADD COLUMN method TEXT NOT NULL DEFAULT 'sft'"):
            try:
                self._db.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
        # A process can't own a 'running' job at startup — any such row died with a
        # previous process. Mark them failed so the single queue never deadlocks.
        # Safe now: the instance lock above guarantees we are the ONLY queue that
        # could ever have written 'running', so this can no longer clobber a
        # still-alive sibling's in-flight job.
        self._db.execute(
            "UPDATE jobs SET status='failed', finished=?, result=? WHERE status='running'",
            (time.time(), json.dumps({"ok": False, "message": "interrupted by restart"})))
        self._db.commit()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    # --- public API ---
    def enqueue(self, adapter: str, seed_only: bool = False,
                priority: int = 0, method: str = "sft") -> dict:
        store = AdapterStore(self.settings.adapters_path)
        spec = store.load(adapter)
        if spec is None:
            return {"ok": False, "error": f"No adapter spec named '{adapter}'."}
        if method not in ("sft", "dpo"):
            return {"ok": False, "error": f"Unknown training method '{method}'."}
        now = time.time()
        # DPO runs are short, interactive corrections on top of an already-trained
        # adapter -- the lane's SFT seed/full cooldown doesn't apply to them.
        run_at = now if method == "dpo" else self._eligible_at(adapter, now, seed_only)
        # Hard cooldown stored on the adapter: never train before it expires.
        if method == "sft" and spec.cooldown_until and now < spec.cooldown_until:
            run_at = max(run_at, spec.cooldown_until)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO jobs(adapter,status,seed_only,created,run_at,priority,method) "
                "VALUES (?,?,?,?,?,?,?)",
                (adapter, "pending", int(seed_only), now, run_at, int(priority), method))
            self._db.commit()
            job_id = cur.lastrowid
        wait = max(0, int((run_at - now) / 60))
        return {"ok": True, "job_id": job_id, "run_at": run_at,
                "starts_in_min": wait, "method": method}

    def status(self) -> dict:
        with self._lock:
            rows = [dict(r) for r in self._db.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT 50")]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        running = next((r for r in rows if r["status"] == "running"), None)
        return {"counts": counts, "running": running, "recent": rows[:20]}

    def job(self, job_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def job_for_adapter(self, adapter: str) -> dict | None:
        """The most recent job for this adapter, however it was enqueued (pipeline,
        direct API call, a smoke test) — lets pipeline reconciliation notice a
        training that succeeded outside the pipeline's own flow."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM jobs WHERE adapter=? ORDER BY id DESC LIMIT 1",
                (adapter,)).fetchone()
        return dict(row) if row else None

    def job_log(self, job_id: int) -> str:
        row = self.job(job_id)
        if not row or not row.get("log_path"):
            return ""
        p = Path(row["log_path"])
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def cancel(self, job_id: int) -> dict:
        with self._lock:
            row = self._db.execute("SELECT status FROM jobs WHERE id=?",
                                   (job_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "No such job."}
            if row["status"] != "pending":
                return {"ok": False, "error": f"Job is {row['status']}, cannot cancel."}
            self._db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
            self._db.commit()
        return {"ok": True}

    def has_active_job(self, adapter: str) -> bool:
        """True if a job for this adapter is pending OR running. Used to refuse a
        rename while the worker might claim/train it under the old name."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM jobs WHERE adapter=? AND status IN ('pending','running') "
                "LIMIT 1", (adapter,)).fetchone()
        return row is not None

    def rename_adapter(self, old: str, new: str) -> int:
        """Carry an adapter's job history to a new name so cooldown/history
        lookups (keyed by adapter name) keep working after a rename. Returns the
        number of job rows moved."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE jobs SET adapter=? WHERE adapter=?", (new, old))
            self._db.commit()
            return cur.rowcount

    def forget(self, adapter: str) -> int:
        """Drop an adapter's job history (used when it is deleted) so a future
        adapter reusing the name doesn't inherit stale cooldown/history."""
        with self._lock:
            cur = self._db.execute("DELETE FROM jobs WHERE adapter=?", (adapter,))
            self._db.commit()
            return cur.rowcount

    def stop(self) -> None:
        self._stop.set()

    # --- internals ---
    def _eligible_at(self, adapter: str, now: float, seed_only: bool) -> float:
        cool = cd.cooldown_minutes(
            seed_only,
            seed=self.settings.cooldown.seed_minutes,
            full=self.settings.cooldown.full_minutes,
            lo=self.settings.cooldown.min_minutes,
            hi=self.settings.cooldown.max_minutes)
        with self._lock:
            last = self._db.execute(
                "SELECT MAX(finished) AS f FROM jobs "
                "WHERE adapter=? AND status='done'", (adapter,)).fetchone()
            last_any = self._db.execute(
                "SELECT MAX(finished) AS f FROM jobs WHERE status='done'").fetchone()
            pending = self._db.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status='pending'").fetchone()["c"]
            busy = self._db.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status='running'").fetchone()["c"]
        earliest = now
        if last and last["f"]:
            earliest = max(earliest, last["f"] + cool * 60)
        # Machine-level breather between ANY two trainings (not just this lane's).
        pause = max(0, self.settings.cooldown.min_pause_minutes)
        if pause and last_any and last_any["f"]:
            earliest = max(earliest, last_any["f"] + pause * 60)
        # Stagger behind anything already waiting/running.
        return cd.next_run_epoch(earliest, busy > 0, cool, queue_position=pending)

    def _history(self, adapter: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT result FROM jobs WHERE adapter=? AND status='done' "
                "ORDER BY id ASC", (adapter,)).fetchall()
        out = []
        for r in rows:
            try:
                res = json.loads(r["result"])
                out.append({
                    "selected_val_loss": res.get("selected_val_loss"),
                    "final_val_loss": res.get("final_val_loss"),
                    "selected_iter": res.get("selected_iter"),
                    "iters": res.get("iters"),
                })
            except Exception:
                continue
        return out

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self._claim_next()
            if job is None:
                time.sleep(2.0)
                continue
            try:
                self._run(job)
            except Exception as exc:  # noqa: BLE001
                self._finish(job["id"], "failed", {"ok": False, "message": str(exc)})

    def _claim_next(self) -> dict | None:
        now = time.time()
        # Quiet hours: don't START new training while the machine should stay free.
        if _in_quiet_hours(now, self.settings.cooldown.no_train_start_hour,
                           self.settings.cooldown.no_train_end_hour):
            return None
        with self._lock:
            busy = self._db.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status='running'").fetchone()["c"]
            if busy:  # strict: never run two at once
                return None
            row = self._db.execute(
                "SELECT * FROM jobs WHERE status='pending' AND run_at<=? "
                "ORDER BY priority DESC, run_at ASC, id ASC LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            self._db.execute(
                "UPDATE jobs SET status='running', started=? WHERE id=?",
                (now, row["id"]))
            self._db.commit()
            return dict(row)

    def _run(self, job: dict) -> None:
        if job.get("method") == "dpo":
            self._run_dpo(job)
            return
        store = AdapterStore(self.settings.adapters_path)
        spec = store.load(job["adapter"])
        run_dir = self.runs_dir / str(job["id"])
        data_dir = run_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "train.log"
        with self._lock:
            self._db.execute("UPDATE jobs SET log_path=? WHERE id=?",
                             (str(log_path), job["id"]))
            self._db.commit()

        def log(msg: str) -> None:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(msg + "\n")

        # Full fine-tune checkpoints are full-model sized (not a small LoRA delta),
        # and each save_every interval writes another one — fail fast on low disk
        # instead of discovering it mid-run via an ENOSPC-corrupted checkpoint.
        if spec.training_mode == "full":
            try:
                free_gb = shutil.disk_usage(store.dir).free / 1e9
            except OSError:
                free_gb = None
            if free_gb is not None and free_gb < _MIN_FREE_GB_FULL_FINETUNE:
                msg = (f"only {free_gb:.0f} GB free where adapters are stored — full "
                       f"fine-tune checkpoints are full-model sized, need >= "
                       f"{_MIN_FREE_GB_FULL_FINETUNE:.0f} GB free before starting. "
                       "Free space or point the adapters directory at a larger volume.")
                log(msg)
                self._finish(job["id"], "failed", {"ok": False, "message": msg})
                return

        # Prepare data: merged (gold+train + collected at/above the adapter's
        # min_tier, deduped, leakage-free) -> train.jsonl, pool valid -> valid.jsonl.
        pool = Pool(self.settings.pool_path, spec.pool or spec.name)
        train_rows, valid_rows, sinfo = pool.split_for_training(
            min_tier=spec.min_tier, val_ratio=self.settings.training.val_split)
        # Hard guard — a lane/adapter mismatch must NEVER reach the weights. If the
        # lane's own gold doesn't match this adapter's objective domain (wrong source
        # routed here, a bad lane->space map), refuse to train. Fail-closed. Checked
        # on the lane's gold, BEFORE the generic injection seed is mixed in.
        # (1) EXACT provenance guard — every row must come from a source this lane is
        # allowed to pull from; catches dept-vs-dept mismatch (RMA gold in the CS lane).
        _prov_ok, _prov_why = _provenance_ok(spec.pool or spec.name, train_rows)
        if not _prov_ok:
            msg = (f"lane/adapter mismatch: {_prov_why} — refusing to train "
                   f"'{spec.name}' on foreign-source data.")
            log(msg)
            self._finish(job["id"], "failed",
                         {"ok": False, "message": msg,
                          "verdict": "Mismatch — foreign-source data, refused to train."})
            return
        # (2) Coarse domain guard — catches gross mismatch for lanes without a declared
        # source allowlist (e.g. the DACH->roof collector bug, sales data in a support lane).
        _match = _lane_domain_match(spec, train_rows)
        if _match < _LANE_MATCH_MIN:
            msg = (f"lane/adapter mismatch: only {_match*100:.0f}% of the data matches the "
                   f"'{spec.name}' objective domain (need >= {_LANE_MATCH_MIN*100:.0f}%) — "
                   "refusing to train; wrong source routed to this lane?")
            log(msg)
            self._finish(job["id"], "failed",
                         {"ok": False, "message": msg,
                          "verdict": "Mismatch — wrong-domain data, refused to train."})
            return
        # Data gates run on the TASK data BEFORE the injection seed is mixed in —
        # the varied seed answers used to push parrot-y or too-small task pools
        # over both gates, so lanes trained on seed + near-nothing.
        min_ex = self.settings.training.min_examples
        if len(train_rows) < min_ex:
            msg = (f"only {len(train_rows)} task training examples (need >= {min_ex}) — "
                   "collect more or add gold examples before training.")
            log(msg)
            self._finish(job["id"], "failed", {"ok": False, "message": msg})
            return
        ok_d, why_d = Pool.distinctness_ok(
            train_rows, self.settings.training.min_answer_distinct_ratio)
        if not ok_d:
            msg = (f"task answers too repetitive ({why_d}) — the pool would train a "
                   "parrot; add answer variety first.")
            log(msg)
            self._finish(job["id"], "failed", {"ok": False, "message": msg})
            return
        # Defense in depth: blend the shared injection-defense seed into the training
        # set so the adapter itself learns to resist prompt injection (a held-out
        # battery verifies it later).
        from . import security
        train_rows, _sec_mix = security.mix_into(train_rows, self.settings)
        if _sec_mix.get("added"):
            log(f"injection-defense: +{_sec_mix['added']} seed examples "
                f"({_sec_mix['classes']} classes) mixed into training")
        log(f"merged {len(train_rows)} train / {len(valid_rows)} valid "
            f"({sinfo['valid_source']} split, tier >= {spec.min_tier})")
        Pool.write_jsonl(data_dir / "train.jsonl", train_rows)
        Pool.write_jsonl(data_dir / "valid.jsonl", valid_rows)

        # Adaptive early-stop from this lane's own history.
        es = cd.adaptive_iters(
            self._history(spec.name), spec.iters,
            min_iters=self.settings.training.min_iters)
        if es.reason:
            log(es.reason)

        backend = select_backend(self.settings.backend)
        prof = {**self.settings.profile(), "name": self.settings.train_profile}
        rank, scale = self.settings.effective_lora(spec)
        if rank != spec.lora_rank:
            log(f"per-lane rank: {spec.name} -> rank {rank} (scale {scale:.3f})")
        # Anti-overtraining: bound epochs over the actual training set.
        iters = _epoch_capped_iters(es.iters, len(train_rows), prof.get("batch_size", 1),
                                    self.settings.training.max_epochs,
                                    self.settings.training.min_iters)
        if iters < es.iters:
            log(f"epoch cap: iters {es.iters} -> {iters} "
                f"(<= {self.settings.training.max_epochs} passes over {len(train_rows)} ex)")
        log(f"backend={backend.name} profile={prof['name']} iters={iters} "
            f"grad_accum={prof.get('grad_accum', 1)} val_batches={prof.get('val_batches')}")
        # Free unified/VRAM before training: evict any Ollama-resident models so the
        # trainer and idle judge models can't co-load and OOM-panic the host (the
        # crash that took everything down). Best-effort.
        try:
            from .ollama_client import OllamaClient
            freed = OllamaClient(self.settings.ollama_host).unload_all()
            if freed:
                log(f"freed GPU: unloaded {len(freed)} Ollama model(s) before training")
        except Exception:
            pass
        # Hold the shared GPU lease across the actual training so the gym never runs
        # concurrently with the other trainers on the host (no OOM). No-op off-host.
        # Real capability signal, computed WHILE STILL HOLDING THE ONE GPU LEASE.
        # Val-loss alone is not comparable across data types and can reward a
        # memorised prose lane; the acceptance battery grades what the adapter
        # actually does. Two hard constraints:
        #  * ONE continuous lease train->verify — never release+reacquire, or a
        #    foreign host trainer grabs the GPU in the gap and co-loads -> OOM.
        #  * Evict Ollama around the eval so the mlx eval subprocess and the local
        #    judge model can't co-load and OOM the host (the top landmine).
        # FORCE the LOCAL judge — acceptance answers carry internal content and must
        # never reach a public/cloud judge (data-sovereignty guarantee).
        pass_rate = avg_score = base_avg = None
        acceptance_error = None
        with _gpu_lease(self.settings.gpu_lease_path, f"llm-gym:{spec.name}",
                        f"gym training {spec.name}") as _:
            log("holding GPU lease" if self.settings.gpu_lease_path else "no GPU lease (disabled)")
            result = backend.train(
                base_model=spec.base_model, data_dir=data_dir,
                out_dir=store.artifact_dir(spec.name),
                rank=rank, scale=scale,
                dropout=spec.lora_dropout, learning_rate=spec.learning_rate,
                iters=iters, lora_keys=self.settings.training.lora_keys, log=log,
                # resume=False: warm-starting every retrain from the previous run's
                # lowest-val (= most-memorized) weights compounded memorization across
                # cooldown cycles — measured: support lanes fell 40-60 avg points BELOW
                # their own un-adapted base. Every run trains fresh from the base.
                save_every=self.settings.save_every, resume=False, profile=prof,
                training_mode=spec.training_mode)
            if result.ok and getattr(spec, "acceptance_prompts", None):
                try:
                    from . import judge
                    from .ollama_client import OllamaClient
                    sv = self.settings.model_copy(deep=True)
                    sv.judge.mode = "local"
                    OllamaClient(sv.ollama_host).unload_all()
                    answers = judge.run_acceptance(
                        spec, store.artifact_dir(spec.name), backend.name, sv)
                    jr = judge.judge(spec, answers, sv)
                    if jr.get("eval_mode") in ("unavailable", "degraded"):
                        # Placeholder answers / a sick judge would grade ~0 and wrongly
                        # overwrite a good adapter's verdict — leave pass_rate None
                        # (val fallback) and surface why.
                        acceptance_error = f"eval {jr.get('eval_mode')}: " \
                                           f"judge_health={jr.get('judge_health')}"
                        log(f"acceptance skipped: {acceptance_error}")
                    else:
                        judge.save_verify(store.artifact_dir(spec.name), jr)
                        pass_rate, avg_score = jr.get("pass_rate"), jr.get("avg_score")
                        log(f"acceptance: {pass_rate}% pass, avg {avg_score}")
                        base_avg = self._base_baseline(spec, store, sv, log)
                    OllamaClient(sv.ollama_host).unload_all()
                except Exception as exc:  # noqa: BLE001
                    acceptance_error = repr(exc)
                    log(f"acceptance verify skipped: {exc!r}")

        # Grade by acceptance pass-rate when available; otherwise fall back to the
        # val-loss bands (pass_rate=None inside _verdict).
        verdict = _verdict(result.selected_val_loss, result.final_val_loss,
                           result.selected_iter, result.iters, result.ok,
                           pass_rate=pass_rate, avg_score=avg_score,
                           prose=_is_prose(train_rows))
        # Delta vs the un-adapted base — the only fair grade. Batteries differ in
        # difficulty, so absolute scores mislead; and a training that scores BELOW
        # its own base actively damaged the model (measured: support lanes at
        # base 77-83 dropped to 23-34 after training on weak synth gold).
        if avg_score is not None and base_avg is not None:
            delta = round(avg_score - base_avg, 1)
            if delta <= -5:
                verdict = (f"Regression — adapter scores BELOW its own base model "
                           f"({avg_score:.0f} vs base {base_avg:.0f}, delta {delta:+.0f}); "
                           f"training hurt the model — do not use this adapter.")
            else:
                verdict += f" (delta vs base {delta:+.0f}, base {base_avg:.0f})"
        # Compute + persist the recommended cooldown on the adapter; the queue
        # will refuse to retrain this adapter before it expires.
        cool = _optimal_cooldown(self.settings.cooldown, verdict, result.ok)
        if result.ok:
            spec.optimal_cooldown_min = cool
            spec.cooldown_until = time.time() + cool * 60
            store.save(spec)
        payload = {
            "ok": result.ok, "backend": result.backend, "iters": result.iters,
            "selected_val_loss": result.selected_val_loss,
            "final_val_loss": result.final_val_loss,
            "selected_iter": result.selected_iter,
            "adapter_path": result.adapter_path, "message": result.message,
            "base_model": spec.base_model, "verdict": verdict,
            "pass_rate": pass_rate, "avg_score": avg_score,
            "base_avg": base_avg,
            "delta_vs_base": (round(avg_score - base_avg, 1)
                              if avg_score is not None and base_avg is not None else None),
            "acceptance_error": acceptance_error,
            "optimal_cooldown_min": cool,
            "cooldown_until": spec.cooldown_until if result.ok else 0,
        }
        self._finish(job["id"], "done" if result.ok else "failed", payload)

    def _run_dpo(self, job: dict) -> None:
        """RLHF via Direct Preference Optimization: trains directly on human
        preference pairs (llm_gym/feedback.py) instead of the SFT chat pool.
        Separate from _run()'s SFT path -- the data shape, gates, and
        hyperparameters are different enough that branching early and keeping
        the two paths independent is far safer than threading `if method ==`
        checks through the SFT path's carefully-tuned guard chain."""
        from . import anonymize, feedback
        from .trainer import dpo_backend

        store = AdapterStore(self.settings.adapters_path)
        spec = store.load(job["adapter"])
        run_dir = self.runs_dir / str(job["id"])
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "train.log"
        with self._lock:
            self._db.execute("UPDATE jobs SET log_path=? WHERE id=?",
                             (str(log_path), job["id"]))
            self._db.commit()

        def log(msg: str) -> None:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(msg + "\n")

        if spec is None:
            self._finish(job["id"], "failed", {"ok": False, "message": "adapter spec not found"})
            return
        if not self.settings.rlhf.enabled:
            msg = "RLHF (DPO) is disabled — enable it on the Settings tab first."
            log(msg)
            self._finish(job["id"], "failed", {"ok": False, "message": msg})
            return
        if not dpo_backend.available():
            msg = ("DPO training needs torch+transformers+peft+trl (pip install "
                   "'llm-gym[peft]', trl>=0.9) -- not installed on this host.")
            log(msg)
            self._finish(job["id"], "failed", {"ok": False, "message": msg})
            return

        if spec.training_mode == "full":
            try:
                free_gb = shutil.disk_usage(store.dir).free / 1e9
            except OSError:
                free_gb = None
            if free_gb is not None and free_gb < _MIN_FREE_GB_FULL_FINETUNE:
                msg = (f"only {free_gb:.0f} GB free where adapters are stored — full "
                       f"fine-tune checkpoints are full-model sized, need >= "
                       f"{_MIN_FREE_GB_FULL_FINETUNE:.0f} GB free before starting. "
                       "Free space or point the adapters directory at a larger volume.")
                log(msg)
                self._finish(job["id"], "failed", {"ok": False, "message": msg})
                return

        rlhf = self.settings.rlhf
        pool = Pool(self.settings.pool_path, spec.pool or spec.name)
        if rlhf.auto_pairs_from_verify:
            a = self.settings.anonymize
            anon = ((lambda t: anonymize.anonymize(t, terms=a.terms, redact_names=a.redact_names))
                    if a.enabled else (lambda t: t))
            derived = feedback.derive_from_verification(pool, anonymize=anon)
            if derived["added"]:
                log(f"auto-derived {derived['added']} pair(s) from gold vs. verify failures")
        pairs = feedback.read_pairs(pool)
        if len(pairs) < rlhf.min_pairs:
            msg = (f"only {len(pairs)} preference pair(s) (need >= {rlhf.min_pairs}) -- "
                   "submit more human feedback before training with RLHF.")
            log(msg)
            self._finish(job["id"], "failed", {"ok": False, "message": msg})
            return
        # Snapshot exactly what this job trained on (SFT's _run() does the same for
        # train.jsonl/valid.jsonl) — a later-edited/removed pair shouldn't erase the
        # ability to audit what a finished run actually consumed.
        data_dir = run_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        Pool.write_jsonl(data_dir / "pairs.jsonl", pairs)
        log(f"training on {len(pairs)} preference pair(s)")

        # Anti-overtraining, same principle as SFT: bound epochs over the actual
        # pair count. dpo_backend.py hardcodes an effective batch of 4 (batch_size=1,
        # grad_accum=4) — without this, rlhf.iters has no ceiling tied to how much
        # data actually exists, and a huge value would occupy the single-worker
        # queue for the entire run with no way to cancel a 'running' job.
        iters = _epoch_capped_iters(rlhf.iters, len(pairs), 4,
                                    self.settings.training.max_epochs,
                                    self.settings.training.min_iters)
        if iters < rlhf.iters:
            log(f"epoch cap: iters {rlhf.iters} -> {iters} "
                f"(<= {self.settings.training.max_epochs} passes over {len(pairs)} pairs)")
        # The epoch cap above is a no-op when max_epochs=0 (an existing, repo-wide
        # escape valve the SFT path also has) — so also apply an unconditional
        # ceiling here, same reasoning as _MAX_ITERS_FULL_DPO but more generous
        # since LoRA DPO is far cheaper per step than full-weight DPO.
        iters = min(iters, _MAX_ITERS_FULL_DPO if spec.training_mode == "full"
                    else _MAX_ITERS_LORA_DPO)
        if spec.training_mode == "full":
            rank, scale = spec.lora_rank, spec.lora_scale
        else:
            rank, scale = self.settings.effective_lora(spec)
        try:
            from .ollama_client import OllamaClient
            freed = OllamaClient(self.settings.ollama_host).unload_all()
            if freed:
                log(f"freed GPU: unloaded {len(freed)} Ollama model(s) before training")
        except Exception:
            pass

        with _gpu_lease(self.settings.gpu_lease_path, f"llm-gym-dpo:{spec.name}",
                        f"gym RLHF/DPO training {spec.name}"):
            log("holding GPU lease" if self.settings.gpu_lease_path else "no GPU lease (disabled)")
            result = dpo_backend.train_dpo(
                base_model=spec.base_model, pairs=pairs,
                out_dir=store.artifact_dir(spec.name),
                rank=rank, scale=scale, dropout=spec.lora_dropout,
                learning_rate=rlhf.learning_rate, iters=iters,
                lora_keys=self.settings.training.lora_keys, beta=rlhf.beta, log=log,
                training_mode=spec.training_mode)

        verdict = ("RLHF/DPO training complete." if result.ok
                   else f"RLHF/DPO training failed: {result.message}")
        payload = {
            "ok": result.ok, "backend": result.backend, "method": "dpo",
            "iters": result.iters, "selected_val_loss": result.selected_val_loss,
            "final_val_loss": result.final_val_loss, "adapter_path": result.adapter_path,
            "message": result.message, "base_model": spec.base_model,
            "verdict": verdict, "pairs_used": len(pairs),
        }
        self._finish(job["id"], "done" if result.ok else "failed", payload)

    def _base_baseline(self, spec, store: AdapterStore, sv, log) -> float | None:
        """avg_score of the UN-adapted base model on this lane's battery, cached
        per (base_model, battery) in the adapter dir. Computed once (~2 min);
        invalidated automatically when the base or the battery changes."""
        from . import judge
        cache = store.artifact_dir(spec.name) / "base_verify.json"
        key = judge.base_battery_key(spec)
        try:
            if cache.exists():
                d = json.loads(cache.read_text(encoding="utf-8"))
                if d.get("key") == key and d.get("avg_score") is not None:
                    return d["avg_score"]
        except (OSError, ValueError):
            pass
        answers = judge.run_acceptance_base(spec, sv)
        if not answers:
            log("base baseline skipped: no acceptance_prompts on spec")
            return None
        jr = judge.judge(spec, answers, sv)
        if jr.get("eval_mode") == "degraded":
            n_valid = jr.get("n_valid"); health = jr.get("judge_health")
            log(f"base baseline skipped: degraded (n_valid={n_valid}/{len(answers)}, "
                f"judge_health={health}) — never caching a sick/gen-error baseline")
            return None   # never cache a sick judge's baseline
        out = {"key": key, "avg_score": jr.get("avg_score"),
               "pass_rate": jr.get("pass_rate"), "ts": time.time()}
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out), encoding="utf-8")
        except OSError:
            pass
        log(f"base baseline computed: avg {out['avg_score']} (cached for reuse)")
        return out["avg_score"]

    def _finish(self, job_id: int, status: str, payload: dict) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE jobs SET status=?, finished=?, result=? WHERE id=?",
                (status, time.time(), json.dumps(payload), job_id))
            self._db.commit()

    @staticmethod
    def _copy(src: Path, dst: Path) -> None:
        dst.write_text(src.read_text(encoding="utf-8") if src.exists() else "",
                       encoding="utf-8")


def _optimal_cooldown(cd, verdict: str, ok: bool) -> int:
    """Recommend how long to wait before retraining this adapter.

    Weak/Regression -> LONG: retraining on the same data cannot improve the
    outcome — the old 0.5x-base "iterate quickly" rule created the
    memorize-retrain spiral that damaged the support lanes. New/changed data
    re-enqueues explicitly through the ingest path and bypasses the cooldown.
    Overfit -> longer (gather more/better data first). Good -> standard.
    """
    base = cd.full_minutes
    if not ok:
        mins = cd.min_minutes
    elif verdict.startswith(("Weak", "Regression")):
        mins = cd.max_minutes
    elif verdict.startswith("Overfit"):
        mins = int(base * 1.5)
    else:  # Sehr gut / Gut / Good / Solid
        mins = base
    return max(cd.min_minutes, min(cd.max_minutes, mins))


def _is_prose(rows: list) -> bool:
    """True if a lane's answers are free prose, not formulaic/structured output
    (routing JSON, short labels). Absolute val-loss is NOT comparable across these:
    formulaic lanes reach ~0.1, free prose sits at ~1.5-2.5 even when good — so the
    verdict bands must differ, or every prose lane gets mislabelled 'weak'."""
    sample = [r for r in rows[:40] if r.get("messages")]
    if not sample:
        return True
    structured = 0
    for r in sample:
        a = next((m.get("content", "") for m in r["messages"] if m.get("role") == "assistant"), "").strip()
        if a[:1] in "{[" or (len(a) < 60 and (":" in a or a.isupper())):
            structured += 1
    return structured < 0.6 * len(sample)


_LANE_MATCH_MIN = 0.30


def _domain_tokens(text: str) -> set:
    from . import scoring
    return {w for w in scoring._tokens(text or "") if len(w) > 3}


def _lane_domain_match(spec, rows: list, sample: int = 120) -> float:
    """Fraction of training rows whose content shares >=2 domain tokens with the
    adapter's objective. Guards against lane/adapter mismatch: gold for the wrong
    adapter (RMA data in the CS lane, a wrong source->lane map, the DACH->roof
    collector bug) scores near 0 and MUST block training. Returns 1.0 (don't block)
    only when the objective is too sparse to judge reliably."""
    obj = _domain_tokens(spec.objective + " " + " ".join(getattr(spec, "capabilities", []) or [])
                         + " " + " ".join(getattr(spec, "acceptance_prompts", []) or []))
    if len(obj) < 8:
        return 1.0
    rows = [r for r in rows[:sample] if r.get("messages")]
    if not rows:
        return 1.0
    hits = sum(1 for r in rows
               if len(_domain_tokens(" ".join(m.get("content", "") for m in r["messages"])) & obj) >= 2)
    return hits / len(rows)


# Globally-allowed provenance (clean, lane-agnostic, no internal-domain bleed): the
# injection-defense seed, grounded catalog product facts, and public academic/research
# corpora (OpenAlex/Crossref/EuropePMC) + the gym's own clean method pools may appear
# in any lane. (Catches dept-vs-dept Confluence/Jira bleed, not these clean sources.)
_GLOBAL_OK_SOURCES = {"catalog", "security", "inoculation", "seed",
                      "academic", "experience", "reasoning", "diagnosis"}
_LANE_SOURCES_PATH = os.environ.get("LLMGYM_LANE_SOURCES_PATH", "data/lane_sources.json")


def _source_key(row: dict) -> str:
    """Normalised provenance key of a training row from its _meta: a Confluence
    space ('confluence:VS'), a Jira/ITSM project ('jira:SRC'), or a global marker."""
    m = row.get("_meta", {}) or {}
    o = str(m.get("origin") or m.get("source") or "").strip()
    if o.startswith("confluence:"):
        return "confluence:" + o.split(":", 1)[1].split()[0]
    if o.startswith("jira:"):
        return "jira:" + o.split(":", 1)[1].split("-")[0]
    if o.startswith("itsm:"):
        return "itsm:" + o.split(":", 1)[1].split("-")[0]
    low = o.lower()
    for g in _GLOBAL_OK_SOURCES:
        if g in low:
            return g
    return o or ""


def _provenance_ok(lane: str, rows: list, tol: float = 0.10):
    """EXACT lane/adapter mismatch guard: every row carrying a clear provenance must
    come from a source this lane is allowed to pull from (its declared spaces/projects
    + the global-OK set). Catches dept-vs-dept mismatch that token-overlap can't (RMA
    gold's 'confluence:RMA' is not in CS's allowlist {'confluence:VS'}). Rows with no
    recorded provenance (legacy gold) are skipped — the token-overlap guard covers
    those. Returns (ok, reason)."""
    try:
        import json as _json
        allow = (_json.load(open(_LANE_SOURCES_PATH)) or {}).get(lane)
    except Exception:
        allow = None
    if not allow:
        return True, "no declared sources"
    allowed = set(allow) | _GLOBAL_OK_SOURCES
    from collections import Counter
    foreign, checked = Counter(), 0
    for r in rows:
        k = _source_key(r)
        if not k:
            continue
        checked += 1
        if k not in _GLOBAL_OK_SOURCES and not any(k == a or k.startswith(a) or a.startswith(k) for a in allowed):
            foreign[k] += 1
    if not checked:
        return True, "no provenance to check"
    frac = sum(foreign.values()) / checked
    return frac <= tol, (f"{sum(foreign.values())}/{checked} rows from foreign sources "
                         f"{dict(foreign.most_common(3))}" if foreign else "")


def _verdict(selected: float | None, final: float | None,
             selected_iter: int | None, iters: int | None, ok: bool,
             *, pass_rate: float | None = None, avg_score: float | None = None,
             prose: bool = False) -> str:
    """Plain-language assessment of a finished run. Grades by acceptance PASS-RATE
    when a verify ran (comparable across data types); otherwise falls back to
    val-loss with data-type-aware bands (free prose tolerates a far higher loss)."""
    if not ok:
        return "Broken — no usable adapter."
    if pass_rate is not None:
        # avg_score is the primary grade — pass_rate flips 12-14 points per single
        # item on a 7-8-prompt battery, so it only serves as a secondary floor.
        avg = avg_score or 0
        if avg >= 75 and pass_rate >= 60:
            return f"Sehr gut — avg {avg:.0f}/100, {pass_rate:.0f}% of checks pass."
        if avg >= 60:
            return f"Gut — avg {avg:.0f}/100 on the acceptance battery."
        if avg >= 45:
            return f"Solid — avg {avg:.0f}/100; more/aligned gold lifts it."
        return (f"Weak — avg {avg:.0f}/100 on the acceptance battery; "
                "align the gold to the acceptance domain or add gold.")
    if selected is None:
        return "Done — no validation metric (add a valid split to grade it)."
    if final is not None and selected_iter and iters:
        overfit = (final - selected) > 0.1 and final > selected * 1.4
        early = selected_iter < iters * 0.7
        if overfit and early:
            return "Overfit / unstable — best was early, pool too small for the run length."
    good, solid = (1.0, 2.2) if prose else (0.15, 0.4)
    if selected <= good:
        return "Good — solid, low validation loss."
    if selected <= solid:
        return "Solid — usable, room to improve with more/better data."
    return ("Weak — high validation loss for free-prose data; add/align gold."
            if prose else "Weak — high validation loss; improve the pool before relying on it.")
