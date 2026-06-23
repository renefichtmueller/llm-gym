"""The single training queue.

One worker, one GPU job at a time — by design. Jobs are serialized through a
SQLite table and a single background thread, so two adapters can never train at
once (no overlap, no out-of-memory). The cooldown calculator decides when a lane
becomes eligible again; the adaptive early-stop trims iterations for lanes that
overfit.
"""
from __future__ import annotations

import contextlib
import json
import os
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
    priority INTEGER NOT NULL DEFAULT 0   -- higher jumps the line (interactive)
);
"""


class TrainingQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = settings.db_file
        self.runs_dir = settings.runs_path
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL + a busy timeout: the queue and pipeline hold separate connections
        # to this same file, so let writers retry instead of failing instantly
        # with "database is locked".
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)
        # Migrate older DBs that predate the priority column.
        try:
            self._db.execute("ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        # A process can't own a 'running' job at startup — any such row died with a
        # previous process. Mark them failed so the single queue never deadlocks.
        self._db.execute(
            "UPDATE jobs SET status='failed', finished=?, result=? WHERE status='running'",
            (time.time(), json.dumps({"ok": False, "message": "interrupted by restart"})))
        self._db.commit()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    # --- public API ---
    def enqueue(self, adapter: str, seed_only: bool = False,
                priority: int = 0) -> dict:
        store = AdapterStore(self.settings.adapters_path)
        spec = store.load(adapter)
        if spec is None:
            return {"ok": False, "error": f"No adapter spec named '{adapter}'."}
        now = time.time()
        run_at = self._eligible_at(adapter, now, seed_only)
        # Hard cooldown stored on the adapter: never train before it expires.
        if spec.cooldown_until and now < spec.cooldown_until:
            run_at = max(run_at, spec.cooldown_until)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO jobs(adapter,status,seed_only,created,run_at,priority) "
                "VALUES (?,?,?,?,?,?)",
                (adapter, "pending", int(seed_only), now, run_at, int(priority)))
            self._db.commit()
            job_id = cur.lastrowid
        wait = max(0, int((run_at - now) / 60))
        return {"ok": True, "job_id": job_id, "run_at": run_at,
                "starts_in_min": wait}

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

        # Prepare data: merged (gold+train + collected at/above the adapter's
        # min_tier, deduped, leakage-free) -> train.jsonl, pool valid -> valid.jsonl.
        pool = Pool(self.settings.pool_path, spec.pool or spec.name)
        train_rows, valid_rows, sinfo = pool.split_for_training(
            min_tier=spec.min_tier, val_ratio=self.settings.training.val_split)
        # Data gate: too few examples trains noise. Fail clearly before the GPU run.
        min_ex = self.settings.training.min_examples
        if len(train_rows) < min_ex:
            msg = (f"only {len(train_rows)} training examples (need >= {min_ex}) — "
                   "collect more or add gold examples before training.")
            log(msg)
            self._finish(job["id"], "failed", {"ok": False, "message": msg})
            return
        ok_d, why_d = Pool.distinctness_ok(
            train_rows, self.settings.training.min_answer_distinct_ratio)
        if not ok_d:
            msg = (f"answers too repetitive ({why_d}) — the pool would train a "
                   "parrot; add answer variety first.")
            log(msg)
            self._finish(job["id"], "failed", {"ok": False, "message": msg})
            return
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
        # Hold the shared GPU lease across the actual training so the gym never runs
        # concurrently with the other trainers on the host (no OOM). No-op off-host.
        with _gpu_lease(self.settings.gpu_lease_path, f"llm-gym:{spec.name}",
                        f"gym training {spec.name}") as _:
            log("holding GPU lease" if self.settings.gpu_lease_path else "no GPU lease (disabled)")
            result = backend.train(
                base_model=spec.base_model, data_dir=data_dir,
                out_dir=store.artifact_dir(spec.name),
                rank=rank, scale=scale,
                dropout=spec.lora_dropout, learning_rate=spec.learning_rate,
                iters=iters, lora_keys=self.settings.training.lora_keys, log=log,
                save_every=self.settings.save_every, resume=True, profile=prof)

        verdict = _verdict(result.selected_val_loss, result.final_val_loss,
                           result.selected_iter, result.iters, result.ok)
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
            "optimal_cooldown_min": cool,
            "cooldown_until": spec.cooldown_until if result.ok else 0,
        }
        self._finish(job["id"], "done" if result.ok else "failed", payload)

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

    Overfit  -> longer (gather more/better data first).
    Good     -> standard (it's stable, no rush).
    Weak     -> shorter (iterate quickly with more data).
    """
    base = cd.full_minutes
    if not ok:
        mins = cd.min_minutes
    elif verdict.startswith("Overfit"):
        mins = int(base * 1.5)
    elif verdict.startswith("Good"):
        mins = base
    elif verdict.startswith(("Weak", "Solid")):
        mins = int(base * 0.5)
    else:
        mins = base
    return max(cd.min_minutes, min(cd.max_minutes, mins))


def _verdict(selected: float | None, final: float | None,
             selected_iter: int | None, iters: int | None, ok: bool) -> str:
    """Plain-language assessment of a finished run."""
    if not ok:
        return "Broken — no usable adapter."
    if selected is None:
        return "Done — no validation metric (add a valid split to grade it)."
    if final is not None and selected_iter and iters:
        overfit = (final - selected) > 0.1 and final > selected * 1.4
        early = selected_iter < iters * 0.7
        if overfit and early:
            return "Overfit / unstable — best was early, final got worse."
    if selected <= 0.15:
        return "Good — solid, low validation loss."
    if selected <= 0.4:
        return "Solid — usable, room to improve with more/better data."
    return "Weak — high validation loss; improve the pool before relying on it."
