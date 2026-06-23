"""Assign a trained adapter to an application via Ollama.

A LoRA adapter has to meet its base model. Two ways to serve it:

  A) Fuse + register as a standalone Ollama model (recommended for serving):
     fuse the adapter into the base, convert to GGUF, `ollama create`. The app
     then just calls a normal Ollama model name.

  B) Keep it as an adapter and attach at inference time (adapter_path), useful
     when many adapters share one base.

This module returns the exact, copy-pasteable plan for the detected backend, and
can run `ollama create` once a GGUF exists. Conversion to GGUF needs llama.cpp
tools, so that step is shown explicitly rather than hidden.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .adapters import AdapterSpec
from .ollama_client import OllamaClient
from .trainer.base import resolve_base


# ChatML prompt template (Qwen2.5 family) — so the served model speaks the same
# format it was trained on, with sensible generation defaults.
_CHATML_TEMPLATE = (
    '{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}'
    '{{ range .Messages }}<|im_start|>{{ .Role }}\n{{ .Content }}<|im_end|>\n{{ end }}'
    '<|im_start|>assistant\n'
)
_DEFAULT_PARAMS = {"temperature": 0.3, "top_p": 0.9, "num_ctx": 8192}


def build_modelfile(base_ref: str, system: str, adapter_gguf: str | None,
                    *, chatml: bool = False, params: dict | None = None) -> str:
    lines = [f"FROM {base_ref}"]
    if adapter_gguf:
        lines.append(f"ADAPTER {adapter_gguf}")
    if chatml:
        lines.append(f'TEMPLATE """{_CHATML_TEMPLATE}"""')
    if system:
        # system is user-controlled (adapter objective/description). Neutralise the
        # triple-quote sequence so it can't close the SYSTEM block early and inject
        # arbitrary Modelfile directives (FROM/ADAPTER/PARAMETER ...).
        safe = system.replace('"""', "'''")
        lines.append(f'SYSTEM """{safe}"""')
    for k, v in (params or {}).items():
        lines.append(f"PARAMETER {k} {v}")
    if chatml:
        lines.append('PARAMETER stop "<|im_end|>"')
    return "\n".join(lines) + "\n"


def assignment_plan(spec: AdapterSpec, backend: str, artifact_dir: Path) -> dict:
    """Return the steps to turn the trained adapter into a usable model."""
    served_name = f"{spec.name}"
    if backend == "mlx":
        mlx_repo = resolve_base(spec.base_model, "mlx")
        steps = [
            f"python -m mlx_lm fuse --model {mlx_repo} "
            f"--adapter-path {artifact_dir} --save-path {artifact_dir}/fused --dequantize",
            f"# convert the fused model to a GGUF (llama.cpp; converter only emits "
            f"f16/bf16/q8_0, NOT k-quants):",
            f"python convert_hf_to_gguf.py {artifact_dir}/fused "
            f"--outfile {artifact_dir}/{spec.name}-f16.gguf --outtype f16",
            f"# then quantize to q4_K_M (this is the step that does k-quants):",
            f"llama-quantize {artifact_dir}/{spec.name}-f16.gguf "
            f"{artifact_dir}/{spec.name}.gguf q4_K_M",
            f"ollama create {served_name} -f {artifact_dir}/Modelfile",
        ]
        modelfile = build_modelfile(f"./{spec.name}.gguf", spec.objective, None)
    else:  # peft / simulate
        hf_repo = resolve_base(spec.base_model, "hf")
        steps = [
            f"# merge the LoRA into the base:",
            f"python -m peft.utils.merge_lora --base {hf_repo} "
            f"--adapter {artifact_dir} --out {artifact_dir}/merged   # or model.merge_and_unload()",
            f"# convert merged model to a GGUF (llama.cpp; f16/bf16/q8_0 only):",
            f"python convert_hf_to_gguf.py {artifact_dir}/merged "
            f"--outfile {artifact_dir}/{spec.name}-f16.gguf --outtype f16",
            f"# then quantize to q4_K_M:",
            f"llama-quantize {artifact_dir}/{spec.name}-f16.gguf "
            f"{artifact_dir}/{spec.name}.gguf q4_K_M",
            f"ollama create {served_name} -f {artifact_dir}/Modelfile",
        ]
        modelfile = build_modelfile(f"./{spec.name}.gguf", spec.objective, None)

    return {
        "served_model": served_name,
        "base_model": spec.base_model,
        "backend": backend,
        "modelfile": modelfile,
        "steps": steps,
        "adapter_only_note": (
            "Or keep it as an adapter and attach at inference with "
            f"--adapter-path {artifact_dir} on the matching {spec.base_model} base."
        ),
    }


def create_ollama_model(spec: AdapterSpec, artifact_dir: Path,
                        client: OllamaClient) -> dict:
    """Run `ollama create` if a GGUF for this adapter already exists. Accepts both
    the plain `{name}.gguf` and the quantized `{name}-q4_K_M.gguf` that
    build_and_register produces."""
    gguf = artifact_dir / f"{spec.name}.gguf"
    if not gguf.exists():
        q4 = artifact_dir / f"{spec.name}-q4_K_M.gguf"
        if q4.exists():
            gguf = q4
    if not gguf.exists():
        return {"ok": False, "error": "No GGUF yet — run the fuse/convert steps "
                "from the plan first."}
    if not gguf_valid(gguf):
        return {"ok": False, "error": "GGUF failed the magic-byte check (likely a "
                "truncated/corrupt file) — refusing to register."}
    modelfile = build_modelfile(str(gguf), spec.objective, None,
                                chatml=True, params=_DEFAULT_PARAMS)
    mf_path = artifact_dir / "Modelfile"
    mf_path.write_text(modelfile, encoding="utf-8")
    try:
        _register_with_ollama(spec.name, mf_path, lambda m: None)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "served_model": spec.name}


def _which(name: str) -> str | None:
    return shutil.which(name)


def _free_gb(path: Path) -> float | None:
    """Free GB at path, or None if it can't be determined (so callers don't treat
    an unknown value as 0 and silently skip the low-disk guard)."""
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return None


def _dir_gb(path: Path) -> float:
    """Total size (GB) of a directory tree; 0.0 if it can't be read."""
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e9
    except OSError:
        return 0.0


def gguf_valid(path: Path) -> bool:
    """A real GGUF begins with the magic bytes b'GGUF' followed by a sane 4-byte
    little-endian version. Guards against a half-written / ENOSPC-zeroed file (or a
    file with the magic but no real body) being registered."""
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"GGUF":
                return False
            version = int.from_bytes(fh.read(4), "little")
            return 1 <= version <= 10
    except OSError:
        return False


def _register_with_ollama(name: str, modelfile_path: Path,
                          log: Callable[[str], None]) -> None:
    """Register a model via the `ollama create -f` CLI.

    The HTTP /api/create body's `modelfile` field is no longer accepted by newer
    Ollama (it errors "neither 'from' or 'files' was specified"). The CLI reads
    the Modelfile, imports the local GGUF named in FROM (handling the blob), and
    works across Ollama versions — so registration goes through it.
    """
    ollama = _which("ollama")
    if not ollama:
        raise RuntimeError("ollama CLI not on PATH — cannot register the model.")
    log(f"$ ollama create {name} -f {modelfile_path}")
    r = subprocess.run([ollama, "create", name, "-f", str(modelfile_path)],
                       capture_output=True, text=True)
    if r.stdout:
        log(r.stdout[-2000:])
    if r.returncode != 0:
        raise RuntimeError(f"ollama create failed: {(r.stderr or r.stdout or '')[-300:]}")


def build_and_register(spec: AdapterSpec, artifact_dir: Path, client: OllamaClient,
                       log: Callable[[str], None], *, min_free_gb: float = 20.0) -> dict:
    """Full local deploy of a trained adapter: fuse the LoRA into the base,
    convert to GGUF, quantize to q4_K_M, write a ChatML Modelfile, `ollama create`,
    smoke-test. Every step is guarded — a missing tool or low disk returns a clear
    error (plus the manual plan) instead of crashing. Mirrors the production
    trainer's proven steps; the heavy GPU work is meant to run on the trainer host.
    """
    art = Path(artifact_dir)
    if not ((art / "adapters.safetensors").exists()
            or (art / "adapter_model.safetensors").exists()):
        return {"ok": False, "error": "No trained adapter to deploy."}

    convert = _which("convert_hf_to_gguf.py") or _which("convert-hf-to-gguf.py")
    quant = _which("llama-quantize") or _which("quantize")
    if not convert or not quant:
        return {"ok": False, "plan": assignment_plan(spec, "mlx", art),
                "error": "llama.cpp tools (convert_hf_to_gguf.py / llama-quantize) "
                         "not on PATH — run the steps from the plan on the trainer host."}

    free = _free_gb(art)
    if free is None:
        log("[deploy] could not determine free disk — proceeding without the space guard.")
    elif free < min_free_gb:
        return {"ok": False, "error": f"Only {free:.0f} GB free where the model is "
                f"built; need ~{min_free_gb:.0f} GB (fused + f16 + q4). Free space or "
                "point the output at a larger volume."}

    repo = resolve_base(spec.base_model, "mlx")
    fused = art / "fused"
    f16 = art / f"{spec.name}-f16.gguf"
    q4 = art / f"{spec.name}-q4_K_M.gguf"

    def run(cmd: list) -> None:
        log("$ " + " ".join(str(c) for c in cmd))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.stdout:
            log(r.stdout[-2000:])
        if r.returncode != 0:
            log((r.stderr or "")[-2000:])
            raise RuntimeError(f"{Path(str(cmd[0])).name} failed (exit {r.returncode})")

    try:
        run([sys.executable, "-m", "mlx_lm", "fuse", "--model", repo,
             "--adapter-path", str(art), "--save-path", str(fused), "--dequantize"])
        # Peak disk is (fused + f16) during conversion. Check the f16 will fit before
        # starting it, so we fail cleanly instead of half-writing on ENOSPC.
        fused_gb = _dir_gb(fused)
        free_now = _free_gb(art)
        if free_now is not None and fused_gb and free_now < fused_gb * 1.1:
            return {"ok": False, "error": f"Not enough disk to convert: fused model is "
                    f"~{fused_gb:.0f} GB and only {free_now:.0f} GB free (need room for "
                    "the f16 GGUF too). Free space or use a larger volume."}
        run([sys.executable, convert, str(fused), "--outfile", str(f16), "--outtype", "f16"])
        shutil.rmtree(fused, ignore_errors=True)   # free the fused dir BEFORE quantize
        run([quant, str(f16), str(q4), "q4_K_M"])
        if not gguf_valid(q4):
            q4.unlink(missing_ok=True)
            return {"ok": False, "error": "Quantized GGUF failed the magic-byte check "
                    "(likely a disk/ENOSPC issue) — not registering."}
    except RuntimeError as exc:
        q4.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc)}
    finally:
        # fused + f16 are always intermediates — clean up on success and failure.
        shutil.rmtree(fused, ignore_errors=True)
        f16.unlink(missing_ok=True)

    modelfile = build_modelfile(str(q4), spec.objective or spec.description,
                                None, chatml=True, params=_DEFAULT_PARAMS)
    mf_path = art / "Modelfile"
    mf_path.write_text(modelfile, encoding="utf-8")
    try:
        _register_with_ollama(spec.name, mf_path, log)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    smoke = ""
    try:
        smoke = client.generate(spec.name, "Say OK.", num_predict=8)
    except Exception:  # noqa: BLE001
        pass
    if not (smoke or "").strip():
        # Hard gate (like the old trainer): a model that won't even answer is not a
        # usable deploy — unregister it rather than leaving a broken tag live.
        ol = _which("ollama")
        if ol:
            subprocess.run([ol, "rm", spec.name], capture_output=True, text=True)
        return {"ok": False, "error": "smoke test failed — the model produced no "
                "output; unregistered it. Check the GGUF/Modelfile."}
    return {"ok": True, "served_model": spec.name, "gguf": str(q4), "smoke_ok": True}


def tag_version(name: str, version: int, log: Callable[[str], None] | None = None) -> bool:
    """Create a stable per-release alias `{name}-r{version}` via `ollama cp`, so
    every shipped version stays independently addressable (and rollback can point
    back at a named prior release). Best-effort; returns True on success."""
    ol = _which("ollama")
    if not ol or not version:
        return False
    alias = f"{name}-r{version}"
    r = subprocess.run([ol, "cp", name, alias], capture_output=True, text=True)
    if log:
        log(f"tagged release alias {alias}" if r.returncode == 0
            else f"could not tag {alias}: {(r.stderr or '')[-160:]}")
    return r.returncode == 0
