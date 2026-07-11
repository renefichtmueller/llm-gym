"""NVIDIA / CPU LoRA trainer using transformers + peft + trl.

Targets recent transformers/peft/trl. Trains a LoRA on the chat-formatted JSONL,
evaluates on the validation split, and saves the adapter with peft's standard
layout (adapter_model.safetensors). Imports are deferred so the gym runs without
torch installed.
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import LogFn, TrainResult, resolve_base


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


class PeftBackend:
    name = "peft"

    def available(self) -> bool:
        try:
            import peft  # noqa: F401
            import torch  # noqa: F401
            import transformers  # noqa: F401
            return True
        except Exception:
            return False

    def train(self, *, base_model: str, data_dir: Path, out_dir: Path,
              rank: int, scale: float, dropout: float, learning_rate: float,
              iters: int, lora_keys: list[str], log: LogFn,
              save_every: int = 50, resume: bool = False,
              profile: dict | None = None) -> TrainResult:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model
        from transformers import (AutoModelForCausalLM, AutoTokenizer,
                                   Trainer, TrainingArguments,
                                   DataCollatorForLanguageModeling)

        out_dir.mkdir(parents=True, exist_ok=True)
        repo = resolve_base(base_model, "hf")
        log(f"[peft] model={repo} rank={rank} iters={iters}")

        tok = AutoTokenizer.from_pretrained(repo)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token

        def render(rows: list[dict]) -> Dataset:
            texts = [tok.apply_chat_template(r["messages"], tokenize=False)
                     for r in rows if r.get("messages")]
            enc = tok(texts, truncation=True, max_length=2048)
            return Dataset.from_dict(enc)

        train_ds = render(_load_jsonl(data_dir / "train.jsonl"))
        valid_rows = _load_jsonl(data_dir / "valid.jsonl")
        eval_ds = render(valid_rows) if valid_rows else None
        if len(train_ds) == 0:
            return TrainResult(False, self.name, iters, None, None, None,
                               str(out_dir), "Empty training set.")

        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            repo, torch_dtype=dtype,
            device_map="auto" if torch.cuda.is_available() else None)
        # peft maps our suffixes (q_proj, ...) to module names automatically.
        target = sorted({k.split(".")[-1] for k in lora_keys})
        model = get_peft_model(model, LoraConfig(
            r=rank, lora_alpha=int(scale * (rank ** 0.5)),
            lora_dropout=dropout, target_modules=target,
            use_rslora=True, task_type="CAUSAL_LM"))

        args = TrainingArguments(
            output_dir=str(out_dir / "_hf"),
            max_steps=iters, learning_rate=learning_rate,
            per_device_train_batch_size=1, gradient_accumulation_steps=4,
            logging_steps=25, save_steps=save_every, eval_steps=save_every,
            eval_strategy="steps" if eval_ds else "no",
            warmup_ratio=0.05, lr_scheduler_type="cosine",
            report_to=[], fp16=torch.cuda.is_available())

        class _Log(Trainer):
            def log(self, logs, *a, **k):  # type: ignore[override]
                super().log(logs, *a, **k)
                if "loss" in logs:
                    log(f"[peft] step {self.state.global_step}: "
                        f"loss={logs['loss']:.4f} "
                        f"val={logs.get('eval_loss', '-')}")

        trainer = _Log(
            model=model, args=args, train_dataset=train_ds, eval_dataset=eval_ds,
            data_collator=DataCollatorForLanguageModeling(tok, mlm=False))
        trainer.train()

        final = None
        if eval_ds is not None:
            final = float(trainer.evaluate().get("eval_loss"))
        model.save_pretrained(str(out_dir))
        ok = (out_dir / "adapter_model.safetensors").exists()
        return TrainResult(
            ok=ok, backend=self.name, iters=iters,
            selected_val_loss=final, final_val_loss=final,
            selected_iter=iters, adapter_path=str(out_dir),
            message="" if ok else "No adapter file produced.")
