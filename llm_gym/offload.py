"""Disk-pressure offload.

When the trainer host runs low on space, archive *regenerable* artifacts to a NAS
(or any target) and free the local copy. Targets are tried in order — first =
primary, the rest are fall-backs.

ONLY regenerable artifacts are ever moved — never the live adapter weights
(adapters.safetensors), the champion record, the specs, or the pool data:
  * `*-f16.gguf`                 fuse->GGUF intermediates
  * `fused/` dirs                fuse output (dequantized base + adapter)
  * `NNNN_adapters.safetensors`  old numbered checkpoints (the final is kept)
  * old run dirs                 finished job scratch/logs past run_age_days

Remote archiving uses tar-over-ssh (works even where the NAS has SFTP disabled).
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


def free_gb(path: Path) -> float | None:
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return None


def _size_gb(p: Path) -> float:
    try:
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9
        return p.stat().st_size / 1e9
    except OSError:
        return 0.0


def candidates(adapters_path: Path, runs_path: Path, *, run_age_days: int = 7,
               now: float | None = None) -> list[Path]:
    """Regenerable artifacts that may be offloaded. Order = cheapest/safest first."""
    now = now if now is not None else time.time()
    out: list[Path] = []
    ap, rp = Path(adapters_path), Path(runs_path)
    if ap.exists():
        out += sorted(ap.rglob("*-f16.gguf"))
        out += [d for d in ap.rglob("fused") if d.is_dir()]
        for ck in ap.rglob("[0-9]*_adapters.safetensors"):
            # keep the final adapters.safetensors; only old numbered checkpoints go
            if (ck.parent / "adapters.safetensors").exists():
                out.append(ck)
    if rp.exists():
        for d in sorted(rp.iterdir()):
            try:
                if d.is_dir() and (now - d.stat().st_mtime) > run_age_days * 86400:
                    out.append(d)
            except OSError:
                pass
    return out


def _archive(p: Path, target: str, log: LogFn) -> bool:
    """Copy p to target (user@host:/path or a local /path). Returns True on success.
    Remote uses tar-over-ssh so it works even where the NAS has SFTP turned off."""
    name = p.name
    if "@" in target and ":" in target:
        host, _, base = target.partition(":")
        base = base.rstrip("/")
        mk = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                             host, f"mkdir -p {shlex.quote(base)}"],
                            capture_output=True, text=True, timeout=60)
        if mk.returncode != 0:
            log(f"offload: mkdir failed on {host}: {(mk.stderr or '')[-160:]}")
            return False
        remote = f"{base}/{name}.tar.gz"
        cmd = (f"tar czf - -C {shlex.quote(str(p.parent))} {shlex.quote(name)} "
               f"| ssh -o BatchMode=yes {shlex.quote(host)} 'cat > {shlex.quote(remote)}'")
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3600)
    else:
        dest = Path(target)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log(f"offload: cannot write target {target}: {exc}")
            return False
        r = subprocess.run(["cp", "-R", str(p), str(dest / name)],
                           capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        log(f"offload: archive failed for {name}: {(r.stderr or '')[-160:]}")
        return False
    return True


def offload_once(settings, log: LogFn) -> dict:
    """If free space is below the threshold, archive regenerable artifacts to the
    first working target and delete the local copy, until back above the threshold
    or nothing is left to move. A no-op when there's headroom or no target set."""
    runs = Path(settings.runs_path)
    adapters = Path(settings.adapters_path)
    threshold = float(getattr(settings, "offload_threshold_gb", 0) or 0)
    targets = list(getattr(settings, "offload_targets", []) or [])
    free = free_gb(runs)
    if free is None or not threshold or not targets or free >= threshold:
        return {"ran": False, "free_gb": free, "threshold": threshold}
    log(f"offload: {free:.0f} GB free < {threshold:.0f} GB — archiving regenerables")
    moved, freed = 0, 0.0
    for p in candidates(adapters, runs):
        cur = free_gb(runs)
        if cur is not None and cur >= threshold:
            break
        sz = _size_gb(p)
        for tgt in targets:
            if _archive(p, tgt, log):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                moved += 1
                freed += sz
                log(f"offload: archived {p.name} ({sz:.1f} GB) -> {tgt.split(':')[0]}")
                break
    return {"ran": True, "moved": moved, "freed_gb": round(freed, 1),
            "free_gb": free_gb(runs), "threshold": threshold}


def start_watchdog(settings, log: LogFn, *, interval_sec: int = 1200,
                   stop: threading.Event | None = None) -> threading.Thread:
    """Background loop: check disk every interval and offload when low. Daemon."""
    def loop() -> None:
        while not (stop and stop.is_set()):
            try:
                offload_once(settings, log)
            except Exception:  # noqa: BLE001 — never let the watchdog die
                pass
            waited = 0
            while waited < interval_sec and not (stop and stop.is_set()):
                time.sleep(5)
                waited += 5

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
