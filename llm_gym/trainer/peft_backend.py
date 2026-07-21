"""NVIDIA / CPU trainer using transformers + peft + trl.

Targets recent transformers/peft/trl. Trains on the chat-formatted JSONL,
evaluates on the validation split. Imports are deferred so the gym runs
without torch installed.

Two modes (`training_mode`):
  lora — train a LoRA adapter on top of the frozen base (default). Saves
         peft's standard layout (adapter_model.safetensors).
  full — fine-tune every weight in the base model itself. No get_peft_model/
         LoraConfig wrapping at all; `rank`/`scale`/`dropout`/`lora_keys` are
         unused. Saves a full model checkpoint (model.safetensors, or a
         sharded model-*.safetensors + index for larger models) instead of
         a small adapter delta. Needs dramatically more memory (full
         fp16/fp32 weights + optimizer state + gradients for every
         parameter, not just a low-rank slice) — expect this to fail with
         an OOM on hardware that trains LoRA fine.
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


def _has_saved_weights(out_dir: Path) -> bool:
    """True if save_pretrained() produced a usable checkpoint — either a
    single-file safetensors (LoRA adapter or a small full model) or a
    sharded full-model checkpoint (index + shards)."""
    return ((out_dir / "adapter_model.safetensors").exists()
            or (out_dir / "model.safetensors").exists()
            or (out_dir / "model.safetensors.index.json").exists())


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
              profile: dict | None = None,
              training_mode: str = "lora") -> TrainResult:
        import torch
        from datasets import Dataset
        from transformers import (AutoModelForCausalLM, AutoTokenizer,
                                   Trainer, TrainingArguments,
                                   DataCollatorForSeq2Seq)

        out_dir.mkdir(parents=True, exist_ok=True)
        repo = resolve_base(base_model, "hf")
        log(f"[peft] mode={training_mode} model={repo} iters={iters}"
            + (f" rank={rank}" if training_mode == "lora" else ""))

        tok = AutoTokenizer.from_pretrained(repo)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token

        def render(rows: list[dict]) -> Dataset:
            # Completion-only loss: mask the prompt tokens with -100 so the model is
            # trained ONLY on the assistant's answer, matching the mlx path's
            # --mask-prompt. Without this the peft backend learned to also generate
            # the user prompt, diverging from mlx for the same adapter spec.
            ids, masks, labels = [], [], []
            for r in rows:
                msgs = r.get("messages")
                if not msgs:
                    continue
                full = tok.apply_chat_template(msgs, tokenize=True,
                                               truncation=True, max_length=2048)
                lab = list(full)
                try:
                    # Prompt = everything before the final (assistant) turn, plus the
                    # generation prefix. Tokens up to that boundary are not learned.
                    prompt = tok.apply_chat_template(msgs[:-1], tokenize=True,
                                                     add_generation_prompt=True)
                    plen = min(len(prompt), len(full))
                    for i in range(plen):
                        lab[i] = -100
                except Exception:
                    pass  # fall back to full-sequence loss for this row
                ids.append(full)
                masks.append([1] * len(full))
                labels.append(lab)
            return Dataset.from_dict({"input_ids": ids, "attention_mask": masks,
                                      "labels": labels})

        train_ds = render(_load_jsonl(data_dir / "train.jsonl"))
        valid_rows = _load_jsonl(data_dir / "valid.jsonl")
        eval_ds = render(valid_rows) if valid_rows else None
        if len(train_ds) == 0:
            return TrainResult(False, self.name, iters, None, None, None,
                               str(out_dir), "Empty training set.")

        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            repo, torch_dtype=dtype, use_safetensors=True,
            device_map="auto" if torch.cuda.is_available() else None)

        if training_mode == "full":
            log("[peft] full fine-tune: every weight is trainable — this needs far "
                "more memory than LoRA (full fp16/fp32 weights + optimizer state + "
                "gradients for the whole model, not a low-rank slice).")
        else:
            from peft import LoraConfig, get_peft_model
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
            # These periodic snapshots are never read back (we never pass
            # resume_from_checkpoint=); the real artifact is the save_pretrained()
            # call below. Bound them so "full" mode (full-model-sized checkpoints,
            # not a small LoRA delta) can't fill the disk over a long run.
            save_total_limit=2,
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
            # Pads input_ids/attention_mask with the tokenizer pad and labels with
            # -100, preserving the per-row prompt mask built in render().
            data_collator=DataCollatorForSeq2Seq(tok, label_pad_token_id=-100))
        trainer.train()

        final = None
        if eval_ds is not None:
            final = float(trainer.evaluate().get("eval_loss"))
        model.save_pretrained(str(out_dir))
        if training_mode == "full":
            tok.save_pretrained(str(out_dir))  # needed to actually serve/convert a full checkpoint
        ok = _has_saved_weights(out_dir)
        return TrainResult(
            ok=ok, backend=self.name, iters=iters,
            selected_val_loss=final, final_val_loss=final,
            selected_iter=iters, adapter_path=str(out_dir),
            message="" if ok else "No checkpoint file produced.")
