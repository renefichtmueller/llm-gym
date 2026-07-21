"""Detect what the machine can run and recommend a base model size.

The recommendation is deliberately conservative: LoRA training needs headroom
on top of the model weights (optimizer state, activations, the dataset). The
thresholds below target 4-bit base weights + LoRA training overhead.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass


@dataclass
class ModelOption:
    size: str          # "3b" | "7b" | "14b"
    label: str
    min_gb: float      # usable memory needed to train a LoRA on this size (4-bit)
    runnable: bool
    recommended: bool


@dataclass
class SystemReport:
    os: str
    arch: str
    apple_silicon: bool
    cuda: bool
    gpu_name: str
    total_ram_gb: float
    usable_gb: float           # the memory budget we plan against
    backend: str               # detected training backend: mlx | peft | none
    backend_ready: bool        # a real backend is installed (not simulate)
    ollama_installed: bool
    options: list[dict]
    recommended_size: str
    install_cmd: str           # exact pip command for THIS machine
    setup_steps: list[str]     # copy-paste steps to enable real training
    notes: list[str]


def _total_ram_gb() -> float:
    try:
        import psutil  # local import keeps the module importable without psutil
        return round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        try:
            import os
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
        except Exception:
            return 0.0


def _cuda_info() -> tuple[bool, str, float]:
    """Return (has_cuda, gpu_name, vram_gb)."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
            return True, name, vram
    except Exception:
        pass
    # Fall back to nvidia-smi if torch is absent.
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=5,
            ).strip().splitlines()
            if out:
                name, mem = out[0].split(",")
                return True, name.strip(), round(float(mem) / 1024, 1)
        except Exception:
            pass
    return False, "", 0.0


def _detect_backend(apple_silicon: bool, cuda: bool) -> str:
    if apple_silicon:
        try:
            import mlx_lm  # noqa: F401  (type: ignore)
            return "mlx"
        except Exception:
            pass
    try:
        import peft  # noqa: F401  (type: ignore)
        return "peft"
    except Exception:
        pass
    return "none"


def run_check() -> SystemReport:
    sysname = platform.system()
    arch = platform.machine()
    apple_silicon = sysname == "Darwin" and arch in ("arm64", "aarch64")
    cuda, gpu_name, vram = _cuda_info()
    ram = _total_ram_gb()

    # On Apple Silicon the GPU shares unified memory, so the budget is system RAM.
    # On a discrete NVIDIA card the budget is VRAM.
    usable = vram if cuda else ram
    backend = _detect_backend(apple_silicon, cuda)

    sizes = [
        ("3b", "Qwen2.5-3B (small)", 8.0),
        ("7b", "Qwen2.5-7B (medium)", 16.0),
        ("14b", "Qwen2.5-14B (large)", 28.0),
    ]
    options: list[ModelOption] = []
    best = "3b"            # field fallback: smallest, even if nothing runs
    any_runnable = False
    for size, label, min_gb in sizes:
        runnable = usable >= min_gb
        if runnable:
            best = size
            any_runnable = True
        options.append(ModelOption(size, label, min_gb, runnable, False))
    # Only flag an option "recommended" when the machine can actually run it. When
    # nothing is runnable (e.g. <8 GB usable, or memory couldn't be read →
    # usable=0.0) leave every option unrecommended rather than showing "3b …
    # recommended" next to "runnable: no", which reads as a contradiction.
    for opt in options:
        opt.recommended = any_runnable and opt.size == best

    # Exact install command for THIS machine, so nobody has to guess.
    if apple_silicon:
        install_cmd = 'pip install "mlx-lm>=0.18"'
    else:
        install_cmd = ('pip install "torch>=2.2" "transformers>=4.40" '
                       '"peft>=0.10" "datasets>=2.18" "trl>=0.8"')
    setup_steps = [
        "source .venv/bin/activate",   # Windows: .venv\\Scripts\\activate
        install_cmd,
        "python -m llm_gym   # restart the gym",
    ]

    notes: list[str] = []
    if backend == "none":
        notes.append("Simulate mode: the full flow works, but no real weights are "
                     "trained yet. Run the setup command above to enable real "
                     "training.")
    if not cuda and not apple_silicon:
        notes.append("No GPU detected — training on CPU is possible but slow. "
                     "Prefer the 3B base.")
    if usable == 0.0:
        notes.append("Could not read memory size; recommendation may be off.")

    return SystemReport(
        os=sysname, arch=arch, apple_silicon=apple_silicon, cuda=cuda,
        gpu_name=gpu_name or ("Apple Silicon GPU" if apple_silicon else "CPU"),
        total_ram_gb=ram, usable_gb=usable, backend=backend,
        backend_ready=backend != "none",
        ollama_installed=shutil.which("ollama") is not None,
        options=[asdict(o) for o in options],
        recommended_size=best, install_cmd=install_cmd, setup_steps=setup_steps,
        notes=notes,
    )
