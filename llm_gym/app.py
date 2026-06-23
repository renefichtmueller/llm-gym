"""FastAPI app: serves the UI and the JSON API.

Sync route handlers run in FastAPI's threadpool, so the blocking Ollama/Git/pool
calls don't need async plumbing. The single training queue runs in its own
background thread (see queue.py).
"""
from __future__ import annotations

import base64
import hmac
import os
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from . import (academic, advisor, analysis, anonymize, champion, collector, crypto,
               deploy, judge, offload, system_check, vault)
from .adapters import AdapterSpec, AdapterStore, valid_adapter_name
from .config import Settings, load_settings, save_settings
from .gold import GoldEntry, add_gold
from .ollama_client import OllamaClient
from .pipeline import Pipeline
from .pool import Pool, git_pull, git_push, list_pools
from .queue import TrainingQueue

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="LLM Gym", version="0.1.0")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Optional HTTP Basic auth. Active only when LLMGYM_AUTH_USER and
    LLMGYM_AUTH_PASS are both set in the environment (read at request time, never
    stored) — so local use stays open, but an exposed instance can be gated.
    Browser-friendly: the 401 triggers the native login prompt and the browser
    then carries the credentials on every request (API + static)."""

    async def dispatch(self, request, call_next):
        user = os.environ.get("LLMGYM_AUTH_USER", "")
        pw = os.environ.get("LLMGYM_AUTH_PASS", "")
        # Fail closed on a half-configured pair: exactly one var set is almost
        # always a deploy mistake — lock the instance rather than silently serving
        # it open. Both empty = explicit local no-auth mode.
        if bool(user) != bool(pw):
            return Response("Auth misconfigured: set both LLMGYM_AUTH_USER and "
                            "LLMGYM_AUTH_PASS, or neither.", status_code=503)
        if user and pw:
            ok = False
            hdr = request.headers.get("Authorization", "")
            if hdr.startswith("Basic "):
                try:
                    u, _, p = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
                    ok = hmac.compare_digest(u, user) and hmac.compare_digest(p, pw)
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                return Response("Authentication required", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="LLM Gym"'})
        return await call_next(request)


app.add_middleware(BasicAuthMiddleware)

# Shared singletons.
settings = load_settings()
queue = TrainingQueue(settings)
pipeline = Pipeline(settings, queue)


def _offload_log(msg: str) -> None:
    try:
        settings.runs_path.mkdir(parents=True, exist_ok=True)
        with open(settings.runs_path / "offload.log", "a", encoding="utf-8") as fh:
            fh.write(f"{int(time.time())}: {msg}\n")
    except OSError:
        pass


# Disk-pressure watchdog: archive regenerable artifacts to the NAS when low.
offload.start_watchdog(settings, _offload_log)


def _pool_for(name: str) -> Pool:
    spec = _store().load(name)
    return Pool(settings.pool_path, (spec.pool or name) if spec else name)


def _safe_pool(name: str) -> Pool:
    """Pool for an arbitrary, user-supplied pool name. The name becomes a folder
    (Pool() mkdirs on construction), so validate it before touching the FS."""
    if not valid_adapter_name(name):
        raise HTTPException(400, "Invalid pool name.")
    return Pool(settings.pool_path, name)


def _public(spec: AdapterSpec) -> dict:
    """Adapter as JSON for the API — never expose the encrypted/plaintext token."""
    d = spec.model_dump()
    d["lora_alpha"] = spec.lora_alpha
    d["lora_scale"] = spec.lora_scale
    d["trained"] = _store().has_artifact(spec.name)
    champ = champion.load(_store().artifact_dir(spec.name))
    d["champion"] = ({"version": champ.get("version"), "pass_rate": champ.get("pass_rate"),
                      "can_rollback": bool(champ.get("history"))} if champ else None)
    for src in ("confluence", "jira"):
        has = bool(getattr(spec, src).token_enc)
        d[src] = {**d[src], "token_enc": "", "token": "", "has_token": has}
    return d


def _confluence_runtime(spec: AdapterSpec) -> dict | None:
    """Decrypt the token for a collection run; kept in memory only."""
    c = spec.confluence
    if not (c.enabled and c.token_enc):
        return None
    return {"enabled": True, "base_url": c.base_url, "auth_type": c.auth_type,
            "email": c.email, "space": c.space, "token": crypto.decrypt(c.token_enc)}


def _jira_runtime(spec: AdapterSpec) -> dict | None:
    j = spec.jira
    if not (j.enabled and j.token_enc):
        return None
    return {"enabled": True, "base_url": j.base_url, "auth_type": j.auth_type,
            "email": j.email, "project": j.project, "token": crypto.decrypt(j.token_enc)}


def _store() -> AdapterStore:
    return AdapterStore(settings.adapters_path)


def _ollama() -> OllamaClient:
    return OllamaClient(settings.ollama_host)


# ---------- UI ----------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# ---------- system ----------
@app.get("/api/system")
def api_system() -> dict:
    report = system_check.run_check()
    return {
        "system": report.__dict__,
        "ollama_up": _ollama().is_up(),
        "active_base_model": settings.active_base_model,
    }


# ---------- settings ----------
@app.get("/api/settings")
def api_get_settings() -> dict:
    return settings.model_dump()


@app.post("/api/settings")
def api_set_settings(payload: dict) -> dict:
    global settings
    # Don't let the API rewrite filesystem/infra locations — those are deploy-time,
    # not user-tunable. Strip path-like keys before merging.
    payload = {k: v for k, v in (payload or {}).items()
               if k != "pool_path" and not k.endswith("_path")}
    merged = {**settings.model_dump(), **payload}
    settings = Settings(**merged)
    save_settings(settings)
    # The queue and pipeline run on background threads and captured the settings
    # object at startup — point them at the new one so changes (collector budget,
    # cooldown, training defaults, judge) take effect without a restart.
    queue.settings = settings
    pipeline.settings = settings
    return {"ok": True, "settings": settings.model_dump()}


# ---------- ollama ----------
@app.get("/api/ollama/models")
def api_ollama_models() -> dict:
    try:
        return {"ok": True, "models": [m.__dict__ for m in _ollama().list_models()]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "models": []}


class PullReq(BaseModel):
    model: str


@app.post("/api/ollama/pull")
def api_ollama_pull(req: PullReq) -> dict:
    try:
        _ollama().pull(req.model)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------- adapters ----------
@app.get("/api/adapters")
def api_list_adapters() -> dict:
    return {"adapters": [_public(spec) for spec in _store().list()]}


@app.post("/api/adapters")
def api_save_adapter(spec: AdapterSpec) -> dict:
    existing = _store().load(spec.name)
    # Encrypt freshly entered tokens; keep the saved one when editing without re-entering.
    for src in ("confluence", "jira"):
        cfg, old = getattr(spec, src), getattr(existing, src, None) if existing else None
        if cfg.token:
            cfg.token_enc = crypto.encrypt(cfg.token)
        elif old and not cfg.token_enc:
            cfg.token_enc = old.token_enc
        cfg.token = ""                                  # never persist plaintext
    saved = _store().save(spec)
    # Open + link the training pool right away, so it shows up and the collector
    # has somewhere to write. Pool() creates the folder on construction.
    pool = Pool(settings.pool_path, saved.pool or saved.name)
    return {"ok": True, "adapter": _public(saved), "pool": pool.name}


@app.get("/api/adapters/{name}")
def api_get_adapter(name: str) -> dict:
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    return _public(spec)


@app.delete("/api/adapters/{name}")
def api_delete_adapter(name: str, purge_pool: bool = False) -> dict:
    """Delete the adapter spec + its trained artifacts. Pool data is kept unless
    purge_pool=true."""
    spec = _store().load(name)
    if spec is None:
        raise HTTPException(404, "No such adapter")
    if queue.has_active_job(name) or pipeline.is_active(name):
        raise HTTPException(409, "A training job or pipeline is in flight for this "
                            "adapter — let it finish or cancel it before deleting.")
    pool_name = spec.pool or name
    _store().delete(name)
    # Clear transient queue/pipeline state so we don't leave orphaned rows (and a
    # future adapter reusing the name doesn't inherit stale history/cooldown).
    queue.forget(name)
    pipeline.forget(name)
    if purge_pool:
        import shutil
        shutil.rmtree(settings.pool_path / pool_name, ignore_errors=True)
    return {"ok": True, "deleted": name, "pool_purged": purge_pool}


@app.post("/api/adapters/{name}/rename")
def api_rename_adapter(name: str, payload: dict = Body(...)) -> dict:
    """Rename an adapter *in place*: move its spec, trained artifacts, linked pool
    folder (when the pool tracked the name 1:1), and its queue/pipeline history.
    This is the correct way to rename — re-saving under a new name would instead
    leave a duplicate that shares the old pool."""
    if not valid_adapter_name(name):
        raise HTTPException(400, "Invalid adapter name.")
    new = (payload or {}).get("new_name", "").strip()
    if not valid_adapter_name(new):
        raise HTTPException(400, "Name must be 1–64 chars: letters, digits, "
                                 "'.', '_', '-' (and not start with '.').")
    store = _store()
    spec = store.load(name)
    if spec is None:
        raise HTTPException(404, "No such adapter")
    if new == name:
        return {"ok": True, "adapter": _public(spec), "pool": spec.pool or spec.name}
    # Target must be free as a spec AND as a (possibly spec-less) artifact dir.
    if store.load(new) is not None or store.artifact_dir(new).exists():
        raise HTTPException(409, f"An adapter named '{new}' already exists.")

    # Don't rename out from under an in-flight or queued run — paths would dangle.
    if collector.SERVICE.is_running(name):
        raise HTTPException(409, "A collection is running for this adapter.")
    if queue.has_active_job(name) or pipeline.is_active(name):
        raise HTTPException(409, "A training job or pipeline is in flight for this adapter.")

    old_pool = spec.pool or name
    # Only move the pool FOLDER when this adapter is its sole 1:1 owner. If any
    # other adapter shares the same pool, leave the folder put and keep pointing
    # the renamed adapter at it (new_pool=None preserves spec.pool).
    shared = any((s.pool or s.name) == old_pool for s in store.list() if s.name != name)
    pool_tracked = (old_pool == name) and not shared
    new_pool = new if pool_tracked else None
    if pool_tracked and (settings.pool_path / new).exists():
        raise HTTPException(409, f"A pool folder named '{new}' already exists.")

    # Rename the spec (+ artifacts) FIRST — it is the operation most likely to
    # fail (collisions, IO). Only then move the pool folder, rolling the spec
    # back if that move fails, so we never leave a spec pointing at a gone pool.
    try:
        saved = store.rename(name, new, new_pool=new_pool)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if pool_tracked:
        src, dst = settings.pool_path / name, settings.pool_path / new
        if src.exists():
            try:
                src.rename(dst)
            except OSError as exc:
                try:
                    store.rename(new, name, new_pool=old_pool)   # revert the spec
                except Exception as rev:  # noqa: BLE001
                    raise HTTPException(500, f"Could not move the pool folder ({exc}) "
                                        f"AND could not revert the spec rename ({rev}); "
                                        f"adapter is now '{new}' pointing at pool "
                                        f"'{old_pool}'. Resolve manually.")
                raise HTTPException(500, f"Could not move the pool folder: {exc}")

    queue.rename_adapter(name, new)
    pipeline.rename_adapter(name, new)
    collector.SERVICE.rename_adapter(name, new)
    # Cosmetic: carry the per-adapter collect log along. Never let this (or a stray
    # IO error) 500 the request after the durable rename has already succeeded.
    try:
        old_log = settings.runs_path / f"collect-{name}.log"
        if old_log.exists():
            old_log.rename(settings.runs_path / f"collect-{new}.log")
    except OSError:
        pass
    # Make sure the (possibly custom/shared) pool folder exists for the new name.
    Pool(settings.pool_path, saved.pool or saved.name)
    return {"ok": True, "adapter": _public(saved), "pool": saved.pool or saved.name,
            "renamed_from": name}


@app.post("/api/adapters/{name}/train")
def api_train_adapter(name: str, seed_only: bool = False) -> dict:
    return queue.enqueue(name, seed_only=seed_only)


@app.get("/api/adapters/{name}/plan")
def api_plan(name: str) -> dict:
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    from .trainer import select_backend
    backend = select_backend(settings.backend).name
    return deploy.assignment_plan(spec, backend, _store().artifact_dir(name))


@app.post("/api/adapters/{name}/assign")
def api_assign(name: str) -> dict:
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    return deploy.create_ollama_model(spec, _store().artifact_dir(name), _ollama())


@app.post("/api/adapters/{name}/deploy")
def api_deploy(name: str) -> dict:
    """Full local deploy: fuse -> GGUF -> quantize -> ollama create -> smoke.
    Writes a log; returns the result (or a clear error + the manual plan if the
    GPU/llama tools aren't on this host)."""
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    log_path = settings.runs_path / f"deploy-{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")

    return deploy.build_and_register(spec, _store().artifact_dir(name), _ollama(), log)


# ---------- data collector ----------
@app.post("/api/adapters/{name}/advise")
def api_advise(name: str, payload: dict | None = None) -> dict:
    """The intelligence: recommend where + what to search. Returns clarifying
    questions instead of a plan when the brief is too thin to target."""
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    payload = payload or {}
    return advisor.advise(spec, settings, payload.get("topic", ""),
                          payload.get("answers"))


@app.post("/api/adapters/{name}/collect")
def api_collect(name: str, topic: str = "", advise: bool = False) -> dict:
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    log_path = settings.runs_path / f"collect-{name}.log"
    plan = advisor.advise(spec, settings, topic) if advise else None
    return collector.SERVICE.start(spec.model_dump(), _pool_for(name).dir,
                                   settings.collector, log_path, topic=topic, plan=plan,
                                   confluence=_confluence_runtime(spec),
                                   jira=_jira_runtime(spec))


@app.get("/api/adapters/{name}/collect/status")
def api_collect_status(name: str) -> dict:
    st = collector.SERVICE.status(name)
    log_path = settings.runs_path / f"collect-{name}.log"
    st = {**st, "log": log_path.read_text(encoding="utf-8") if log_path.exists() else ""}
    return st


# ---------- analysis ----------
@app.get("/api/adapters/{name}/analysis")
def api_analysis(name: str, min_tier: str = "") -> dict:
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    return analysis.analyse(_pool_for(name).dir, spec.model_dump(),
                            min_tier or spec.min_tier)


# ---------- verify (run acceptance + judge) ----------
@app.post("/api/adapters/{name}/verify")
def api_verify(name: str) -> dict:
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    from .trainer import select_backend
    backend = select_backend(settings.backend).name
    answers = judge.run_acceptance(spec, _store().artifact_dir(name), backend, settings)
    result = judge.judge(spec, answers, settings)
    judge.save_verify(_store().artifact_dir(name), result)   # for the live view
    return result


@app.get("/api/adapters/{name}/cooldown")
def api_cooldown(name: str) -> dict:
    spec = _store().load(name)
    if not spec:
        raise HTTPException(404, "No such adapter")
    import time
    remaining = max(0, int((spec.cooldown_until - time.time()) / 60))
    return {"optimal_cooldown_min": spec.optimal_cooldown_min,
            "cooldown_until": spec.cooldown_until, "remaining_min": remaining}


# ---------- auto-pipeline ----------
@app.post("/api/adapters/{name}/pipeline/start")
def api_pipeline_start(name: str, skip_collect: bool = False) -> dict:
    return pipeline.start(name, skip_collect=skip_collect)


@app.get("/api/adapters/{name}/pipeline")
def api_pipeline_status(name: str) -> dict:
    st = pipeline.status(name)
    return st or {"stage": None, "status": "idle"}


@app.post("/api/adapters/{name}/pipeline/gate")
def api_pipeline_gate(name: str, payload: dict) -> dict:
    return pipeline.gate(name, tier=payload.get("tier"),
                         promote=payload.get("promote"))


@app.post("/api/adapters/{name}/rollback")
def api_rollback(name: str) -> dict:
    """Roll the live version back to the previous promoted one."""
    if not _store().load(name):
        raise HTTPException(404, "No such adapter")
    return pipeline.rollback(name)


# ---------- pool ----------
@app.get("/api/pools")
def api_pools() -> dict:
    # Make sure every defined adapter has its linked pool folder (covers adapters
    # created before this was automatic).
    for spec in _store().list():
        Pool(settings.pool_path, spec.pool or spec.name)
    return {"pools": list_pools(settings.pool_path)}


@app.get("/api/pool/{name}/stats")
def api_pool_stats(name: str) -> dict:
    return _safe_pool(name).stats().__dict__


class AppendReq(BaseModel):
    examples: list[dict]
    split: str = "train"


@app.post("/api/pool/{name}/append")
def api_pool_append(name: str, req: AppendReq) -> dict:
    a = settings.anonymize
    examples = ([anonymize.anonymize_example(e, terms=a.terms, redact_names=a.redact_names)
                 for e in req.examples] if a.enabled else req.examples)
    return _safe_pool(name).append(examples, split=req.split)


@app.get("/api/pool/{name}/search")
def api_pool_search(name: str, q: str, limit: int = 50) -> dict:
    return {"hits": _safe_pool(name).search(q, limit)}


@app.post("/api/pool/{name}/build")
def api_pool_build(name: str) -> dict:
    return _safe_pool(name).build_merged()


@app.post("/api/pool/{name}/push")
def api_pool_push(name: str) -> dict:
    pool = _safe_pool(name)
    g = settings.git
    # Push token: env LLMGYM_GIT_TOKEN, else the configured vault item. Never stored.
    token = vault.resolve("LLMGYM_GIT_TOKEN", g.token_item)
    return git_push(pool.dir, g.remote, g.branch, g.commit_name, g.commit_email,
                    token=token)


@app.post("/api/pool/{name}/pull")
def api_pool_pull(name: str) -> dict:
    """Build/refresh this pool FROM its configured Git remote (Gitea/GitHub/…)."""
    pool = _safe_pool(name)
    g = settings.git
    token = vault.resolve("LLMGYM_GIT_TOKEN", g.token_item)
    return git_pull(pool.dir, g.remote, g.branch, token=token)


class GoldReq(BaseModel):
    pool: str
    problem_prompt: str
    solution_text: str
    executed_ok: bool = False
    verified: bool = False
    system_prompt: str = ""
    source: str = "manual"


@app.post("/api/pool/{name}/gold")
def api_pool_gold(name: str, req: GoldReq) -> dict:
    a = settings.anonymize
    anon = (lambda t: anonymize.anonymize(t, terms=a.terms, redact_names=a.redact_names)) \
        if a.enabled else (lambda t: t)
    entry = GoldEntry(
        problem_prompt=anon(req.problem_prompt), solution_text=anon(req.solution_text),
        executed_ok=req.executed_ok, verified=req.verified,
        system_prompt=anon(req.system_prompt), source=req.source)
    return add_gold(_safe_pool(name), entry)


# ---------- academic search ----------
class AcademicReq(BaseModel):
    query: str
    sources: list[str] | None = None
    limit: int = 10


@app.post("/api/academic/search")
def api_academic(req: AcademicReq) -> dict:
    return {"results": academic.search(req.query, req.sources, req.limit)}


# ---------- queue ----------
@app.get("/api/queue")
def api_queue() -> dict:
    return queue.status()


@app.get("/api/queue/{job_id}/log")
def api_queue_log(job_id: int) -> JSONResponse:
    return JSONResponse({"log": queue.job_log(job_id)})


@app.post("/api/queue/{job_id}/cancel")
def api_queue_cancel(job_id: int) -> dict:
    return queue.cancel(job_id)


# ---------- live view ----------
import re as _re
import socket as _socket
import time as _time

_ITER_RE = _re.compile(r"iter\s+(\d+)\s*/\s*(\d+)", _re.I)
_LOSS_RE = _re.compile(r"(?:val[\s_]*loss|loss)\D{0,4}([0-9]+\.[0-9]+)", _re.I)
_BACKEND_RE = _re.compile(r"backend=(\S+)")
_SYS_REPORT = None


def _sys_report():
    """Cache the (static) hardware report; only Ollama status changes per poll."""
    global _SYS_REPORT
    if _SYS_REPORT is None:
        _SYS_REPORT = system_check.run_check()
    return _SYS_REPORT


def _parse_train_progress(log: str) -> dict:
    cur = total = loss = None
    for m in _ITER_RE.finditer(log):
        cur, total = int(m.group(1)), int(m.group(2))
    for m in _LOSS_RE.finditer(log):
        loss = float(m.group(1))
    pct = round(100 * cur / total) if (cur is not None and total) else None
    return {"iter": cur, "total": total, "pct": pct, "loss": loss}


@app.get("/api/live")
def api_live() -> dict:
    """One snapshot for the live view: where training runs, what is training now
    (with progress + a log tail), and which adapters are collecting / in a
    pipeline. The frontend polls this."""
    from .trainer import select_backend
    rep = _sys_report()
    q = queue.status()
    running = q.get("running")
    train = None
    if running:
        log = queue.job_log(running["id"])
        started = running.get("started") or 0
        bm = _BACKEND_RE.search(log)
        train = {
            "adapter": running["adapter"], "job_id": running["id"],
            "started": started,
            "elapsed_s": int(_time.time() - started) if started else 0,
            "backend": bm.group(1) if bm else select_backend(settings.backend).name,
            "progress": _parse_train_progress(log),
            "log_tail": "\n".join(log.splitlines()[-40:]),
        }
    # Recent runs, enriched with the training verdict (from the job result) and
    # the adapter's latest verify outcome (pass-rate / avg), if any.
    import json as _json
    store = _store()
    recent_runs = []
    for r in (q.get("recent") or [])[:12]:
        row = {"id": r["id"], "adapter": r["adapter"], "status": r["status"],
               "when": r.get("finished") or r.get("started") or r.get("created")}
        if r["status"] in ("done", "failed") and r.get("result"):
            try:
                res = _json.loads(r["result"])
                row["verdict"] = res.get("verdict") or res.get("message") or ""
                row["val_loss"] = res.get("selected_val_loss")
            except Exception:  # noqa: BLE001
                pass
        v = judge.load_verify(store.artifact_dir(r["adapter"]))
        if v:
            row["verify"] = {"pass_rate": v.get("pass_rate"),
                             "avg": v.get("avg_score"), "mode": v.get("eval_mode")}
        recent_runs.append(row)

    return {
        "where": {
            "host": _socket.gethostname(),
            "os": rep.os, "arch": rep.arch, "device": rep.gpu_name,
            "backend": select_backend(settings.backend).name,
            "backend_ready": rep.backend_ready,
            "usable_gb": rep.usable_gb,
            "ollama_up": _ollama().is_up(),
        },
        "training": train,
        "pipelines": pipeline.active(),
        "collections": collector.SERVICE.active(),
        "recent_runs": recent_runs,
    }


# Serve the rest of the static assets (app.js, style.css).
app.mount("/static", StaticFiles(directory=STATIC), name="static")
