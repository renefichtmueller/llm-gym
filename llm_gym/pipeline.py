"""The auto-pipeline: define -> collect -> analyse -> [gate] -> train ->
check -> verify -> cooldown -> [gate] -> promote.

One adapter runs through it at a time. Two human gates: which data tier to train
on, and whether to promote the result. Everything else is automatic. State lives
in SQLite so a restart doesn't lose your place; a background thread advances it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from . import advisor, analysis, champion, collector, crypto, judge
from .adapters import AdapterStore
from .config import Settings
from .pool import Pool
from .queue import TrainingQueue
from .trainer import select_backend

STAGES = ["collect", "analyse", "train", "check", "verify", "gate_promote", "done"]


def _band(verdict: dict) -> str:
    """Grade a verify result: sehr_gut / gut / schlecht.

    'gut' needs a quality floor on avg_score too, not just the pass count — a run
    that technically passes half the prompts but answers them poorly (high pass /
    low avg) must not qualify as 'gut' and auto-promote under full_auto."""
    pr = verdict.get("pass_rate", 0) or 0
    avg = verdict.get("avg_score", 0) or 0
    if pr >= 80 and avg >= 75:
        return "sehr_gut"
    if pr >= 50 and avg >= 40:
        return "gut"
    return "schlecht"


def _gate_action(band: str, policy: str) -> str:
    """What to do at the end of a run, given the verdict band and the policy:
      'promote'   — adopt the candidate now (no human click)
      'recollect' — discard, keep the previous adapter, collect more (no click)
      'gate'      — wait for the human's one-click green/red decision

    policy:
      full_auto      — everything automatic
      auto_verygood  — only 'sehr gut' promotes itself; the rest is gated
      confirm        — nothing automatic; every finished run is gated
    """
    if policy == "full_auto":
        return "recollect" if band == "schlecht" else "promote"
    if policy == "auto_verygood" and band == "sehr_gut":
        return "promote"
    return "gate"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipelines (
    adapter TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,        -- queued | running | waiting | done | failed
    data TEXT NOT NULL DEFAULT '{}',
    updated REAL NOT NULL,
    created REAL NOT NULL DEFAULT 0   -- enqueue time; FIFO order of the line
);
"""


class Pipeline:
    def __init__(self, settings: Settings, queue: TrainingQueue) -> None:
        self.settings = settings
        self.queue = queue
        self._lock = threading.Lock()
        self._db = sqlite3.connect(settings.db_file, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # Share the queue's DB file; WAL + busy timeout avoids "database is locked"
        # when both connections write concurrently (e.g. during a rename).
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)
        # Migrate older DBs that predate the `created` column.
        try:
            self._db.execute("ALTER TABLE pipelines ADD COLUMN created REAL NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        self._db.execute("UPDATE pipelines SET created=updated WHERE created=0 OR created IS NULL")
        # Only ONE pipeline is actively worked at a time. Any 'running' (or
        # 'awaiting_train') row at startup died with a previous process — put it
        # back in the queue (its stage is preserved, so it resumes where it left
        # off; the train stage re-checks its job) so exactly one is promoted again.
        self._db.execute(
            "UPDATE pipelines SET status='queued' WHERE status IN ('running','awaiting_train')")
        # The tier gate is gone (auto-tier now). Anything parked at the old
        # gate_tier resumes straight into training in its turn.
        self._db.execute(
            "UPDATE pipelines SET stage='train', status='queued' "
            "WHERE stage='gate_tier'")
        self._db.commit()
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._continuous_loop, daemon=True).start()

    # --- public API ---
    def start(self, adapter: str, *, skip_collect: bool = False) -> dict:
        store = AdapterStore(self.settings.adapters_path)
        if store.load(adapter) is None:
            return {"ok": False, "error": f"No adapter spec '{adapter}'."}
        stage = "analyse" if skip_collect else "collect"
        now = time.time()
        with self._lock:
            # Don't disturb an adapter that's already in flight: restarting it would
            # reset created=now and shove it to the back of the line (or knock a
            # 'running' one back to 'queued'), breaking FIFO. Only (re)enter from an
            # idle/done/failed state.
            existing = self._db.execute(
                "SELECT status FROM pipelines WHERE adapter=?", (adapter,)).fetchone()
            if existing and existing["status"] in (
                    "running", "awaiting_train", "queued", "waiting"):
                return {"ok": False, "status": existing["status"],
                        "error": "This adapter is already in the pipeline."}
            # Join the line as 'queued'. The worker promotes exactly one to
            # 'running' at a time, in FIFO order (oldest 'created' first).
            self._db.execute(
                "INSERT INTO pipelines(adapter,stage,status,data,updated,created) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(adapter) DO UPDATE SET "
                "stage=excluded.stage, status='queued', data='{}', "
                "updated=excluded.updated, created=excluded.created",
                (adapter, stage, "queued", "{}", now, now))
            self._db.commit()
            pos = self._db.execute(
                "SELECT COUNT(*) AS c FROM pipelines WHERE status IN ('queued','running')").fetchone()["c"]
        return {"ok": True, "stage": stage, "queue_position": pos}

    def status(self, adapter: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM pipelines WHERE adapter=?",
                                   (adapter,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["data"] = json.loads(d["data"] or "{}")
        d["stages"] = STAGES
        return d

    def active(self) -> list[dict]:
        """Every pipeline in flight, ordered for the live view: the one running
        first, then the queued line (FIFO), then any paused at a gate. Queued
        rows carry their 1-based position in line."""
        with self._lock:
            rows = self._db.execute(
                "SELECT adapter, stage, status, data, updated, created FROM pipelines "
                "WHERE status IN ('running','awaiting_train','queued','waiting') "
                "ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'awaiting_train' THEN 1 "
                "WHEN 'queued' THEN 2 ELSE 3 END, created ASC").fetchall()
        out, qpos = [], 0
        for r in rows:
            data = json.loads(r["data"] or "{}")
            log = data.get("log") or []
            item = {"adapter": r["adapter"], "stage": r["stage"],
                    "status": r["status"], "updated": r["updated"],
                    "last": log[-1] if log else ""}
            if r["status"] == "queued":
                qpos += 1
                item["position"] = qpos
            if r["status"] == "waiting":      # a gate is open — feed the buttons
                item["band"] = data.get("band", "")
                item["recommended"] = data.get("recommended", "")
                item["regression"] = bool(data.get("regression"))
                v = data.get("verify") or {}
                if v:
                    item["verify"] = {"pass_rate": v.get("pass_rate"),
                                      "avg": v.get("avg_score")}
            out.append(item)
        return out

    def gate(self, adapter: str, *, tier: str | None = None,
             promote: bool | None = None) -> dict:
        st = self.status(adapter)
        if not st or st["status"] != "waiting":
            return {"ok": False, "error": "No gate is open for this adapter."}
        data = st["data"]
        if st["stage"] == "gate_promote" and promote is not None:
            if promote:                       # green "Freigabe": adopt the candidate
                self._promote(adapter, data.get("verify"))
                self._set(adapter, "done", "done",
                          self._log({**data, "promoted": True, "bad_rounds": 0},
                                    "promoted by you"))
            else:                             # red: discard, keep previous, collect more
                self._recollect(adapter, data,
                                self._log(data, "rejected by you — collecting more"))
            return {"ok": True}
        return {"ok": False, "error": "Gate expects a different decision."}

    def is_active(self, adapter: str) -> bool:
        """True if the worker is currently mid-stage on this adapter, so it must
        not be renamed or deleted out from under the running pipeline."""
        with self._lock:
            row = self._db.execute(
                "SELECT status FROM pipelines WHERE adapter=?", (adapter,)).fetchone()
        return bool(row and row["status"] in ("running", "awaiting_train"))

    def rename_adapter(self, old: str, new: str) -> int:
        """Move auto-pipeline state to a new adapter name after a rename. adapter
        is the PRIMARY KEY, so drop any stale row already under the new name first
        — the renamed adapter's state takes over cleanly."""
        with self._lock:
            self._db.execute("DELETE FROM pipelines WHERE adapter=?", (new,))
            cur = self._db.execute(
                "UPDATE pipelines SET adapter=? WHERE adapter=?", (new, old))
            self._db.commit()
            return cur.rowcount

    def forget(self, adapter: str) -> int:
        """Drop any auto-pipeline state for an adapter (used when it is deleted)."""
        with self._lock:
            cur = self._db.execute("DELETE FROM pipelines WHERE adapter=?", (adapter,))
            self._db.commit()
            return cur.rowcount

    def stop(self) -> None:
        self._stop.set()

    # --- internals ---
    def _set(self, adapter: str, stage: str, status: str, data: dict) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE pipelines SET stage=?, status=?, data=?, updated=? WHERE adapter=?",
                (stage, status, json.dumps(data), time.time(), adapter))
            self._db.commit()

    def _log(self, data: dict, msg: str) -> dict:
        data.setdefault("log", [])
        data["log"].append(f"{int(time.time())}: {msg}")
        data["log"] = data["log"][-200:]
        return data

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._reconcile_training()
            row = self._claim()
            if not row:
                time.sleep(2.0)
                continue
            try:
                self._run_stage(row)
            except Exception as exc:  # noqa: BLE001
                data = json.loads(row["data"] or "{}")
                self._set(row["adapter"], row["stage"], "failed",
                          self._log(data, f"ERROR in {row['stage']}: {exc}"))

    def _train_queue(self):
        """The local single-GPU queue, or — when configured — a client to the
        shared external queue (:5600) so this gym lines up in one circuit with
        the other trainers and never runs a second GPU job. Same surface either
        way (enqueue / job)."""
        if self.settings.remote_queue.enabled:
            from .remote_queue import RemoteQueue
            return RemoteQueue(self.settings)
        return self.queue

    def _reconcile_training(self) -> None:
        """Pipelines parked on their training job ('awaiting_train') re-enter the
        line once that job finishes, so they get promoted to run check/verify in
        turn. This keeps the worker free while a job sits in the GPU queue."""
        with self._lock:
            rows = self._db.execute(
                "SELECT adapter, data FROM pipelines WHERE status='awaiting_train'").fetchall()
            rows = [(r["adapter"], r["data"]) for r in rows]
        tq = self._train_queue()
        for adapter, raw in rows:
            data = json.loads(raw or "{}")
            jid = data.get("train_job")
            if jid is None:
                # Parked on training but no job id to follow — unrecoverable; fail
                # cleanly instead of sitting in awaiting_train forever.
                self._set(adapter, "train", "failed",
                          self._log(data, "awaiting_train with no job id — failing"))
                continue
            job = tq.job(jid)
            if job is None:
                # Job not found (queue restart / remote dropped it). Tolerate a
                # transient miss, but give up after a long unfindable stretch.
                miss = int(data.get("train_missing", 0)) + 1
                data["train_missing"] = miss
                if miss >= 60:
                    self._set(adapter, "train", "failed", self._log(
                        data, f"training job #{jid} not found after {miss} checks — failing"))
                else:
                    self._set(adapter, "train", "awaiting_train", data)
                continue
            data.pop("train_missing", None)
            if job["status"] in ("done", "failed", "cancelled"):
                self._set(adapter, "train", "queued", data)   # re-enter; train stage advances it

    def _continuous_loop(self) -> None:
        """Continuous mode: periodically (re)start adapters whose cooldown has
        passed and that have gathered enough new gold since their last training."""
        while not self._stop.is_set():
            try:
                if self.settings.continuous_enabled:
                    self._continuous_tick()
            except Exception:  # noqa: BLE001 — never let the scheduler die
                pass
            waited, interval = 0, max(30, self.settings.continuous_interval_sec)
            while waited < interval and not self._stop.is_set():
                time.sleep(5)
                waited += 5

    # Don't auto-retry a hard-failed pipeline for this long — otherwise continuous
    # mode restarts it the instant it fails (the gold count is still over the bar)
    # and hot-loops the failure.
    _FAIL_BACKOFF_SEC = 3600.0

    def _continuous_tick(self) -> None:
        store = AdapterStore(self.settings.adapters_path)
        now = time.time()
        with self._lock:
            busy = {r["adapter"] for r in self._db.execute(
                "SELECT adapter FROM pipelines WHERE status IN "
                "('running','awaiting_train','queued','waiting')").fetchall()}
            recent_fail = {r["adapter"] for r in self._db.execute(
                "SELECT adapter FROM pipelines WHERE status='failed' AND updated > ?",
                (now - self._FAIL_BACKOFF_SEC,)).fetchall()}
        for spec in store.list():
            if spec.name in busy or spec.name in recent_fail:
                continue
            art = store.artifact_dir(spec.name)
            gold_now = Pool(self.settings.pool_path, spec.pool or spec.name).stats().gold
            if champion.continuous_eligible(
                    enabled=True, in_pipeline=False,
                    cooldown_until=spec.cooldown_until, now=now, gold_now=gold_now,
                    gold_at_train=champion.gold_at_last_train(art),
                    min_new=self.settings.continuous_min_new_gold):
                self.start(spec.name)

    def _claim(self) -> dict | None:
        """Single active pipeline at a time. Keep working the one that is already
        'running' (depth-first through its stages) until it gates or finishes;
        only then promote the next 'queued' pipeline, oldest first (FIFO)."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM pipelines WHERE status='running' "
                "ORDER BY created ASC LIMIT 1").fetchone()
            if row:
                return dict(row)
            row = self._db.execute(
                "SELECT * FROM pipelines WHERE status='queued' "
                "ORDER BY created ASC LIMIT 1").fetchone()
            if not row:
                return None
            self._db.execute(
                "UPDATE pipelines SET status='running', updated=? WHERE adapter=?",
                (time.time(), row["adapter"]))
            self._db.commit()
            d = dict(row)
            d["status"] = "running"
            return d

    def _run_stage(self, row: dict) -> None:
        adapter, stage = row["adapter"], row["stage"]
        data = json.loads(row["data"] or "{}")
        store = AdapterStore(self.settings.adapters_path)
        spec = store.load(adapter)
        if spec is None:
            self._set(adapter, stage, "failed", self._log(data, "spec vanished"))
            return
        pool = Pool(self.settings.pool_path, spec.pool or spec.name)

        if stage == "collect":
            # The advisor decides what + where to search before collecting.
            plan = advisor.advise(spec, self.settings)
            data["plan"] = plan
            self._log(data, f"advisor: domain={plan.get('domain', '?')} "
                            f"sources={plan.get('source_categories')} "
                            f"({plan.get('engine', '?')})")
            self._set(adapter, "collect", "running", data)
            c = spec.confluence
            conf = ({"enabled": True, "base_url": c.base_url, "auth_type": c.auth_type,
                     "email": c.email, "space": c.space,
                     "token": crypto.decrypt(c.token_enc)}
                    if c.enabled and c.token_enc else None)
            j = spec.jira
            jira = ({"enabled": True, "base_url": j.base_url, "auth_type": j.auth_type,
                     "email": j.email, "project": j.project,
                     "token": crypto.decrypt(j.token_enc)}
                    if j.enabled and j.token_enc else None)
            # Stream the collector's own progress into the pipeline log so the
            # UI shows it stepping through queries instead of looking frozen.
            def collect_log(m: str) -> None:
                self._set(adapter, "collect", "running", self._log(data, m))
            summary = collector.collect(spec.model_dump(), pool.dir,
                                        self.settings.collector, collect_log,
                                        plan=plan, confluence=conf, jira=jira,
                                        anon=self.settings.anonymize)
            data["collect"] = summary
            self._set(adapter, "analyse", "running",
                      self._log(data, f"collected {summary['kept']} examples"))

        elif stage == "analyse":
            report = analysis.analyse(pool.dir, spec.model_dump(), spec.min_tier)
            data["analysis"] = report
            data["chosen_tier"] = spec.min_tier   # auto: use the adapter's tier floor
            # No human tier gate — keep working this pipeline straight into train.
            self._set(adapter, "train", "running",
                      self._log(data, f"analysed — grade {report['pool_grade']}, "
                                f"forecast {report['forecast']['expected_verdict']}; "
                                f"tier {spec.min_tier}"))

        elif stage == "train":
            # Training is delegated to the dedicated GPU queue (one job at a time
            # too). We DON'T block the pipeline worker waiting for it — otherwise
            # a cooldown-delayed job would freeze the whole line. Enqueue once,
            # then park as 'awaiting_train' and yield; _reconcile_training()
            # advances us when the job finishes.
            jid = data.get("train_job")
            if jid is None:
                # Snapshot the gold count now, so continuous mode measures "new
                # gold since last train" from here.
                champion.mark_trained(store.artifact_dir(adapter), pool.stats().gold)
                r = self._train_queue().enqueue(adapter)
                if not r.get("ok"):
                    self._set(adapter, "train", "failed",
                              self._log(data, r.get("error", "enqueue failed")))
                    return
                jid = r["job_id"]
                data["train_job"] = jid
                wait = r.get("starts_in_min") or 0
                where = " on the shared queue" if r.get("remote") else ""
                self._set(adapter, "train", "awaiting_train", self._log(
                    data, f"training queued as job #{jid}{where}"
                    + (f" (starts in ~{wait} min — cooldown)" if wait else "")))
                return
            job = self._train_queue().job(jid)
            if not job or job["status"] not in ("done", "failed", "cancelled"):
                self._set(adapter, "train", "awaiting_train", data)   # still waiting; yield
                return
            data["train_result"] = json.loads(job["result"] or "{}")
            if data["train_result"].get("ok"):
                self._set(adapter, "check", "running",
                          self._log(data, "training done — checking the adapter"))
            else:
                self._set(adapter, "train", "failed", self._log(data, "training failed"))

        elif stage == "check":
            backend = select_backend(self.settings.backend).name
            answers = judge.run_acceptance(spec, store.artifact_dir(adapter),
                                           backend, self.settings)
            data["acceptance"] = answers
            self._set(adapter, "verify", "running",
                      self._log(data, f"ran {len(answers)} acceptance prompts"))

        elif stage == "verify":
            verdict = judge.judge(spec, data.get("acceptance", []), self.settings)
            data["verify"] = verdict
            judge.save_verify(store.artifact_dir(adapter), verdict)   # for the live view
            spec = store.load(adapter)  # the queue set cooldown during training
            data["cooldown"] = {"optimal_cooldown_min": spec.optimal_cooldown_min,
                                "cooldown_until": spec.cooldown_until}
            band = _band(verdict)
            data["band"] = band
            champ = champion.load(store.artifact_dir(adapter))
            data["champion"] = champ
            regression = champion.is_regression(verdict, champ)
            data["regression"] = regression
            # Head-to-head on the FROZEN held-out set (#5/#6/#7): run the candidate
            # and the live champion over the same excluded-from-training examples,
            # scoring classifier answers by real accuracy. A guardrail, never fatal.
            head_loss = False
            try:
                _, eval_rows, _ = pool.split_for_training(
                    min_tier=spec.min_tier, val_ratio=self.settings.training.val_split)
                bk = select_backend(self.settings.backend).name
                h2h = judge.verify_against_eval_set(
                    spec, store.artifact_dir(adapter), bk, self.settings, eval_rows)
                data["eval"] = {k: h2h.get(k) for k in ("candidate", "champion", "base", "decision")}
                # Don't auto-ship a candidate that loses to the live model OR (first
                # version) fails to beat the un-adapted base.
                head_loss = h2h.get("decision") in ("champion_wins", "base_floor_fail")
            except Exception as exc:  # noqa: BLE001
                self._log(data, f"eval-set head-to-head skipped: {exc}")
            msg = (f"verdict: {band} ({verdict['pass_rate']}% pass, "
                   f"avg {verdict['avg_score']})")
            if regression:
                msg += (f" — REGRESSION vs v{champ.get('version')} "
                        f"({champ.get('pass_rate')}% pass)")
            if head_loss:
                why = ("fails to beat the base model" if data.get("eval", {}).get("decision")
                       == "base_floor_fail" else "loses to the live model")
                msg += f" — {why} on the frozen eval set"
            action = _gate_action(band, self.settings.pipeline_policy)
            # Never auto-ship something worse than the live version — gate it,
            # whether the regression shows in the scalar verdict or the head-to-head.
            if (regression or head_loss) and action == "promote":
                action = "gate"
            # First-ever version: there's no champion to catch a regression against,
            # so only auto-ship if it's clearly excellent; otherwise get human eyes.
            if champ is None and action == "promote" and band != "sehr_gut":
                action = "gate"
            if action == "promote":
                self._promote(adapter, verdict)
                self._set(adapter, "done", "done",
                          self._log({**data, "promoted": True, "bad_rounds": 0},
                                    msg + " → automatically promoted"))
            elif action == "recollect":
                self._recollect(adapter, data, self._log(data, msg))
            else:  # gate: wait for the one-click green/red decision
                data["recommended"] = ("recollect" if (band == "schlecht" or regression
                                                       or head_loss) else "promote")
                self._set(adapter, "gate_promote", "waiting",
                          self._log(data, msg + f" → awaiting your decision ({data['recommended']})"))

    def rollback(self, adapter: str) -> dict:
        """Make the previous promoted version the champion again."""
        store = AdapterStore(self.settings.adapters_path)
        rec = champion.rollback(store.artifact_dir(adapter))
        if rec is None:
            return {"ok": False, "error": "No earlier version to roll back to."}
        return {"ok": True, "champion": rec}

    def _promote(self, adapter: str, verify: dict | None = None) -> None:
        """Adopt the freshly trained candidate as the live adapter, recording its
        score + version. Until a run is promoted nothing is served, so NOT
        promoting is the rollback: the previous adapter stays live, untouched."""
        store = AdapterStore(self.settings.adapters_path)
        rec = champion.promote(store.artifact_dir(adapter), verify or {})
        # Best-effort: make it the served Ollama model if a GGUF exists, and tag a
        # stable per-release alias ({name}-r{version}) so every shipped version is
        # independently addressable and rollback can point back at a named one.
        try:
            from . import deploy
            from .ollama_client import OllamaClient
            spec = store.load(adapter)
            if spec is not None:
                r = deploy.create_ollama_model(spec, store.artifact_dir(adapter),
                                               OllamaClient(self.settings.ollama_host))
                if r.get("ok") and rec:
                    deploy.tag_version(spec.name, rec.get("version", 0))
        except Exception:  # noqa: BLE001 — promotion of the record already happened
            pass

    def _recollect(self, adapter: str, data: dict, data_logged: dict) -> None:
        """Weak result: keep the previous adapter live and collect more data.
        Stops after pipeline_max_bad_rounds consecutive weak rounds so a bad data
        source can't loop forever — it flags for a human instead."""
        bad = int(data.get("bad_rounds", 0)) + 1
        data = dict(data_logged)
        data["bad_rounds"] = bad
        if bad >= self.settings.pipeline_max_bad_rounds:
            self._set(adapter, "done", "failed", self._log(
                data, f"{bad} weak rounds in a row — stopping. The data source is "
                "likely the limit (check the brief, the Jira/Confluence connector, "
                "or add gold examples)."))
            return
        # Drop the finished job so the next pass re-trains; keep the live adapter.
        data.pop("train_job", None)
        data.pop("train_result", None)
        self._set(adapter, "collect", "queued", self._log(
            data, f"weak — kept the previous adapter, collecting more (round {bad})"))
