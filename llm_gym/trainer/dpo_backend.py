"""DPO (Direct Preference Optimization) LoRA training from human feedback.

Takes prompt/chosen/rejected preference pairs (see ../feedback.py) instead of
the chat-format train.jsonl the SFT backends use -- DPO's loss compares
P(chosen | prompt) against P(rejected | prompt) for the SAME prompt, so pairs
must share a prompt; feedback.py enforces that at collection time.

NVIDIA/CPU only (uses transformers + peft + trl, same install extra as
peft_backend.py's SFT path: `pip install llm-gym[peft]`). Imports are
deferred so the gym runs without torch installed. Saves the adapter with
peft's standard layout (adapter_model.safetensors), same as peft_backend.py,
so it slots into the exact same assign/deploy verification flow as an
SFT-trained adapter.
"""
from __future__ import annotations

from pathlib import Path

from .base import LogFn, TrainResult, resolve_base


def available() -> bool:
    try:
        import peft  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
        import trl  # noqa: F401
        return True
    except Exception:
        return False


def train_dpo(*, base_model: str, pairs: list[dict], out_dir: Path,
              rank: int, scale: float, dropout: float, learning_rate: float,
              iters: int, lora_keys: list[str], beta: float, log: LogFn,
              profile: dict | None = None) -> TrainResult:
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import DPOConfig, DPOTrainer

    out_dir.mkdir(parents=True, exist_ok=True)
    repo = resolve_base(base_model, "hf")
    log(f"[dpo] model={repo} rank={rank} iters={iters} beta={beta} pairs={len(pairs)}")

    if len(pairs) < 2:
        return TrainResult(False, "dpo", iters, None, None, None,
                            str(out_dir), "Not enough preference pairs to train.")

    ds = Dataset.from_list([
        {"prompt": p["prompt"], "chosen": p["chosen"], "rejected": p["rejected"]}
        for p in pairs
    ])
    split = ds.train_test_split(test_size=min(0.1, 2 / len(pairs)), seed=1234)
    train_ds, eval_ds = split["train"], split["test"]

    tok = AutoTokenizer.from_pretrained(repo)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        repo, torch_dtype=dtype, use_safetensors=True,
        device_map="auto" if torch.cuda.is_available() else None)

    target = sorted({k.split(".")[-1] for k in lora_keys})
    lora_config = LoraConfig(
        r=rank, lora_alpha=int(scale * (rank ** 0.5)), lora_dropout=dropout,
        target_modules=target, use_rslora=True, task_type="CAUSAL_LM")

    args = DPOConfig(
        output_dir=str(out_dir / "_hf"),
        max_steps=iters, learning_rate=learning_rate, beta=beta,
        per_device_train_batch_size=1, gradient_accumulation_steps=4,
        logging_steps=10, eval_strategy="steps" if len(eval_ds) else "no",
        eval_steps=max(10, iters // 8), warmup_ratio=0.05,
        lr_scheduler_type="cosine", report_to=[],
        fp16=torch.cuda.is_available(), max_prompt_length=1024, max_length=2048)

    class _Log(DPOTrainer):
        def log(self, logs, *a, **k):  # type: ignore[override]
            super().log(logs, *a, **k)
            if "loss" in logs:
                log(f"[dpo] step {self.state.global_step}: "
                    f"loss={logs['loss']:.4f} val={logs.get('eval_loss', '-')}")

    trainer = _Log(
        model=model, args=args, train_dataset=train_ds,
        eval_dataset=eval_ds if len(eval_ds) else None,
        processing_class=tok, peft_config=lora_config)
    trainer.train()

    final = None
    if len(eval_ds):
        final = float(trainer.evaluate().get("eval_loss"))
    trainer.save_model(str(out_dir))
    ok = (out_dir / "adapter_model.safetensors").exists()
    return TrainResult(
        ok=ok, backend="dpo", iters=iters,
        selected_val_loss=final, final_val_loss=final,
        selected_iter=iters, adapter_path=str(out_dir),
        message="" if ok else "No adapter file produced.")
