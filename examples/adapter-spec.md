# Adapter spec — the template, explained

An adapter is defined *before* it is trained. The definition is a small JSON
file (the gym writes one per adapter under `adapters/<name>.json`). You can also
fill it in from the **Adapters** tab in the UI. Every field:

| Field | What it is | Example |
|-------|------------|---------|
| `name` | Unique id. Becomes the artifact folder and the served model name. | `security-triage` |
| `description` | One line: what this adapter is for. | `Triage a finding and propose a fix.` |
| `objective` | The longer goal — what it should be able to do, in plain language. | see below |
| `capabilities` | A checklist of concrete skills. Doubles as your acceptance criteria. | `["Classify severity", ...]` |
| `acceptance_prompts` | Prompts you will use to smoke-test the result. | `["A port is open to 0.0.0.0..."]` |
| `base_model` | The base it trains on **and** is served on. An adapter cannot move between bases. | `qwen2.5:3b` |
| `pool` | The training pool that feeds it (folder under your pool root). | `security-triage` |
| `lora_rank` | LoRA rank. 16 is a good default; 32–64 for harder reasoning. | `16` |
| `lora_alpha_factor` | `alpha = factor × rank`. Keep at 2 (rsLoRA). | `2` |
| `lora_dropout` | LoRA dropout. 0.0 is fine for most SFT. | `0.0` |
| `learning_rate` | 1e-5 for 4-bit bases (higher tends to diverge). | `1e-05` |
| `iters` | Training iterations. The gym auto-caps this if the lane overfits. | `600` |
| `tags` | Free-form labels. | `["security"]` |

## Minimal example

```json
{
  "name": "my-adapter",
  "description": "What it is for, in one line.",
  "objective": "What it should be able to do, in a sentence or two.",
  "capabilities": ["First skill", "Second skill"],
  "acceptance_prompts": ["A prompt you will test it on."],
  "base_model": "qwen2.5:3b",
  "pool": "my-adapter",
  "lora_rank": 16,
  "learning_rate": 1e-05,
  "iters": 600
}
```

## Why bind to one base model

The LoRA weights are deltas on top of one specific set of base weights. Train on
`qwen2.5:3b` and the adapter only makes sense on `qwen2.5:3b`. If you want both a
small and a large variant, define two adapters (one per base) and train each.

## How `iters` gets adjusted

If a lane keeps overfitting (its best validation loss arrives early and the final
loss is much worse), the gym caps future runs to roughly `1.2 ×` the typical peak
iteration, rounded to the nearest 50, with a floor of 100. You set the ceiling;
the gym lowers it when the data says so.
