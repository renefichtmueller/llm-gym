# LLM Gym — the full walkthrough

A start-to-finish guide for someone new to fine-tuning. By the end you will have
trained a LoRA adapter on a local model and assigned it to an app through Ollama.
No cluster, no cloud, no account.

---

## 0. The mental model

Two ideas carry the whole system.

**A base model** is a general LLM (here: Qwen2.5, served by Ollama). You do not
change it. **An adapter** (LoRA) is a small set of extra weights trained on top
of the frozen base. The base stays general; the adapter makes it good at *one*
job using *your* data.

> Train the adapter, not the model. It is cheaper, faster, reversible, and you
> can keep many adapters for one base.

The adapter is **bound to its base**. Train on `qwen2.5:3b` → serve on
`qwen2.5:3b`. You cannot attach a 3B adapter to a 14B model. Want both sizes?
Define and train two adapters.

---

## 1. Set up the gym

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m llm_gym          # http://127.0.0.1:8000
```

Open the **Dashboard**. The system check reads your hardware and recommends a
base size:

- It measures your memory budget (unified RAM on Apple Silicon, VRAM on NVIDIA).
- It needs headroom for LoRA training, not just the weights, so the thresholds
  are conservative: ~8 GB → 3B, ~16 GB → 7B, ~28 GB → 14B.
- It detects the training backend: **MLX** (Apple Silicon), **PEFT** (NVIDIA/CPU),
  or **simulate** if neither is installed.

Install a backend when you are ready for real training (see the README). Until
then, everything below still works in **simulate** mode — you will see the full
flow with placeholder weights.

Pull the recommended base from the dashboard, or:

```bash
ollama pull qwen2.5:3b
```

---

## 2. Define an adapter

Go to **Adapters → Define an adapter**. The definition says *what the adapter
should do* before you train it:

- **Name** — becomes the artifact folder and the served model name.
- **Objective** — what it should be able to do, in plain language.
- **Capabilities** — a concrete checklist. This is also your acceptance criteria.
- **Acceptance prompts** — prompts you will smoke-test the result on.
- **Base model** — the base it binds to.
- **Pool** — the training data that feeds it.
- **Rank / learning rate / iters** — sensible defaults are pre-filled (rsLoRA,
  rank 16, lr 1e-5).

Save it. The gym writes `adapters/<name>.json`. Two ready examples live in
[`../examples`](../examples). The format is documented in
[`../examples/adapter-spec.md`](../examples/adapter-spec.md).

A good objective is narrow and testable. "Be helpful" is not trainable.
"Given a finding with title, severity and evidence, return a step-by-step,
verifiable fix" is.

---

## 3. Build a good training pool

This is where adapters are won or lost. The pool is chat-format JSONL under
`data/pool/<name>/`. Each line is one example:

```json
{"messages":[{"role":"user","content":"<the task>"},
             {"role":"assistant","content":"<the ideal answer>"}],
 "_meta":{"source":"manual","verified":false,"quality_score":70}}
```

The gym keeps several files per pool:

| File | Meaning |
|------|---------|
| `train.jsonl` | curated training examples |
| `valid.jsonl` | held-out examples used only to grade the run |
| `gold.jsonl` | verified gold pairs (highest trust) |
| `raw/` | uncurated dumps you still need to clean up |
| `merged.jsonl` | built on demand — deduplicated union of gold + train, with leakage removed |

### 3a. What "perfect" means

- **Consistent shape.** Every example teaches the same task in the same format.
  Mixed formats teach confusion.
- **Right answers only.** One wrong label poisons more than ten right ones help.
- **Cover the edges.** Include the ambiguous, the "not enough info", the refusal.
- **No leakage.** An example must not appear in both `train` and `valid`. The gym
  drops leaked pairs when it builds `merged.jsonl`.
- **Don't let one source dominate.** If you bulk-import from one place, keep it a
  minority of the set so it doesn't drown your hand-curated data.
- **Dedup.** Exact duplicates waste training and inflate your sense of size. The
  gym dedups on a hash of the messages.

### 3b. Getting data in

Three ways, smallest effort last to biggest payoff:

1. **Write it.** On **Training pool → Add an example**, paste JSONL lines. This is
   how you author the precise, on-task examples that matter most.
2. **Search academic / university sources.** On the same tab, **Find data** queries
   OpenAlex, arXiv and Semantic Scholar. These index university and research
   output and are free to query. Results are **candidates** — they land in `raw/`,
   not in `train`. You edit and verify them before they count.
3. **Promote to gold.** The strongest signal (next section).

### 3c. Using and *modifying* data to fill the pool

Raw text is rarely a good training pair. Turn it into one:

- **Reshape into the task.** A paper abstract is not "the answer". Write the
  *question your adapter will actually be asked*, then craft the answer from the
  source. Example: instead of storing an abstract verbatim, store
  `user: "What defends against prompt injection at the model layer?"` /
  `assistant: "<your distilled, correct answer drawing on the source>"`.
- **Normalise the style.** Rewrite answers in the voice and length you want the
  adapter to produce. The adapter copies your style, so make the examples *be*
  the style.
- **Split long sources into focused pairs.** One dense document becomes several
  single-question examples. Smaller, sharper pairs train better than one giant blob.
- **Add the hard cases on purpose.** Take a real failure, write the correct
  handling, and add it. A handful of well-chosen corrections moves the needle more
  than another hundred easy examples.
- **Keep citations in `_meta`.** It costs nothing and lets you re-check sources.

The rule of thumb: every edit you make to a candidate should push it closer to
"exactly what I want the model to say when asked this". That is the whole game.

### 3d. Gold-standard input

A pair is **gold** only when both are true:

1. The solution was actually **executed** and succeeded.
2. The result was independently **verified**.

Use **Training pool → Gold-standard input** and tick both boxes. The gym refuses
anything that isn't gold and tells you to keep it as a candidate instead. Gold is
the cleanest signal you can give an adapter, because it is *known correct*, not
merely plausible. Failures are useful too — keep them as negative examples rather
than throwing them away.

### 3e. Optional: keep the pool in Git

Set a Git remote in **Settings** (any host — a self-hosted Gitea, GitHub, ...).
Then **Build merged** and **Push to Git** version your pool. A versioned pool is
auditable: you can see exactly which data produced which adapter. Tokens are read
from the environment at push time and never stored by the gym.

---

## 4. Train

On **Adapters**, pick your adapter and hit **Train**. What happens:

- The job goes into the **single queue**. Only one adapter trains at a time, so a
  second job waits — by design (no overlap, no out-of-memory).
- The **cooldown calculator** decides when the lane may run. A quick seed run has
  a short cooldown; a full run a long one (defaults 30 min / 480 min, clamped
  30–720). Queued jobs are staggered so they never collide.
- The gym builds `merged.jsonl`, splits off the validation set, and runs the
  backend (MLX or PEFT, or simulate).
- It tracks validation loss and selects the **best** checkpoint, not the final one
  (the final is often worse after overfitting).
- **Adaptive early-stop**: if the lane overfit on recent runs, the gym caps the
  iterations to about 1.2× the typical peak (rounded to 50, floor 100).

Watch progress in **Training queue → log**. When it finishes you get a plain
verdict: *Good*, *Solid*, *Weak*, *Overfit/unstable*, or *Broken*.

---

## 5. Assign the adapter to your app

Open **Show assign plan**. To serve through Ollama:

1. **Fuse** the adapter into the base.
2. **Convert** the fused model to GGUF (llama.cpp).
3. **`ollama create`** a model — your app then calls it by name like any other
   Ollama model.

The plan prints the exact commands for your backend. Once a GGUF exists, the
**Run ollama create** button does the last step for you. Your application code
changes by one line: point it at the new model name.

Alternatively, keep it as an adapter and attach it at inference with
`--adapter-path` on the matching base — handy when many adapters share one base.

---

## 6. The loop

Adapters get better the way data gets better:

1. Ship the adapter.
2. Collect where it was wrong.
3. Turn those into correct, verified pairs (gold where you can).
4. Add them to the pool, rebuild merged, retrain.
5. Compare the new verdict to the old champion; keep the winner.

Small, honest, well-shaped data beats large, noisy data every time. The gym just
makes the loop fast enough to actually run.
