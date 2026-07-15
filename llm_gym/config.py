"""Settings for the gym.

Resolution order (later wins): built-in defaults -> settings.json -> environment
(LLMGYM_*). Settings are persisted back to settings.json from the UI, never to
environment. Nothing here is a secret; the only optional credential (a Git
remote token) is read from the environment at push time and never stored.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Project root = parent of the llm_gym package.
ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "settings.json"

# Training resource profiles. num_layers (top-N layers fine-tuned) and batch_size
# are the real memory levers; nice + threads keep the machine responsive; the
# mem_fraction is the share of unified memory a run may use (the executor applies
# it as an MLX memory cap). Higher = faster but less polite.
# grad_accum compensates for the tiny per-step batch (effective batch = batch_size
# * grad_accum) so gradients stay stable without using more memory per step.
TRAIN_PROFILES: dict[str, dict] = {
    "work":     {"nice": 15, "threads": 2, "num_layers": 8,  "batch_size": 1, "grad_accum": 4, "mem_fraction": 0.50},
    "balanced": {"nice": 10, "threads": 4, "num_layers": 16, "batch_size": 1, "grad_accum": 2, "mem_fraction": 0.65},
    "fast":     {"nice": 5,  "threads": 6, "num_layers": 16, "batch_size": 2, "grad_accum": 2, "mem_fraction": 0.80},
}


class GitSettings(BaseModel):
    """Optional Git remote for the training pool. Empty = local only."""

    remote: str = ""          # e.g. https://gitea.example.org/you/pool.git (NO token)
    branch: str = "main"
    commit_name: str = "llm-gym"
    commit_email: str = "llm-gym@localhost"
    # Push token: read from env LLMGYM_GIT_TOKEN, or — if set — fetched from this
    # Vaultwarden/Bitwarden item. Never stored in .git/config; injected only for
    # the push itself.
    token_item: str = ""


class CooldownSettings(BaseModel):
    """How long a lane waits before it may train again (minutes)."""

    seed_minutes: int = 30     # quick seed/build runs
    full_minutes: int = 480    # full verified runs (8h)
    min_minutes: int = 30
    max_minutes: int = 720
    # A machine-level breather enforced between ANY two trainings (independent of
    # per-lane cooldown), so back-to-back jobs never hammer the GPU host.
    min_pause_minutes: int = 2
    # Quiet hours: no NEW training starts between these local hours, so the machine
    # stays usable while you work. Wraps midnight (e.g. 8->18 = no training 08:00-
    # 18:00; 22->6 = none overnight). start == end disables it.
    no_train_start_hour: int = 0
    no_train_end_hour: int = 0


class TrainingDefaults(BaseModel):
    """rsLoRA defaults. alpha = alpha_factor * rank, scale = alpha / sqrt(rank)."""

    lora_rank: int = 16
    lora_alpha_factor: int = 2
    lora_dropout: float = 0.0
    learning_rate: float = 1e-5
    iters: int = 600
    max_iters: int = 2000
    min_iters: int = 100
    # Hard anti-overtraining cap: never train more than this many passes over the
    # data. A tiny pool at high iters = guaranteed overfit; this bounds epochs
    # (epochs = iters * batch / examples) on top of best-checkpoint + early-stop.
    max_epochs: int = 25
    # When no hand-curated valid.jsonl exists, hold out this fraction of the merged
    # set as a deterministic validation split (so val-loss / best-checkpoint work).
    val_split: float = 0.1
    # Refuse to train below this many merged examples — too little data trains noise
    # (mirrors the old trainers' data gate, scaled down for smaller gym pools).
    min_examples: int = 10
    # Refuse to train when answers are too repetitive (unique answers / total).
    min_answer_distinct_ratio: float = 0.5
    # How many validation batches mlx_lm evaluates per checkpoint (stabler best-
    # checkpoint selection; -1 = the full valid set).
    val_batches: int = 25
    # Early stopping: stop a run after this many evals with no val-loss improvement
    # (>= min_delta), so small lanes don't burn the whole iter budget overfitting
    # (train loss → 0). 0 disables. Best-checkpoint still ships the lowest-val-loss
    # adapter, so an early stop never produces a worse model.
    early_stop_patience: int = 4
    early_stop_min_delta: float = 0.005
    # Per-lane rank defaults: a richer lane wants a higher rank than the global
    # default. Matched by substring on the adapter name; only used when the adapter
    # hasn't set its own rank. Empty by default — set per deployment in settings,
    # e.g. {"classifier": 64, "writer": 32}.
    lane_rank_defaults: dict[str, int] = Field(default_factory=dict)
    # All linear layers of a Qwen2.5 transformer block (rsLoRA target set).
    lora_keys: list[str] = Field(
        default_factory=lambda: [
            "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
            "self_attn.o_proj", "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj",
        ]
    )


class CollectorSettings(BaseModel):
    """The data collector. Casts a wide, keyless net over universities/research
    AND news agencies by default; a web-search provider is an optional add-on."""

    max_pages: int = 100           # hard budget on web pages fetched per run
    max_seconds: int = 300         # hard time budget per run
    rate_limit_seconds: float = 0.6
    results_per_source: int = 12   # how many hits to pull from each source
    # Which source families to query. academic = OpenAlex/Crossref/arXiv/Semantic
    # Scholar/Europe PMC/DOAJ; news = GDELT (news agencies)/Wikinews;
    # reference = Wikipedia; tech = Hacker News.
    source_categories: list[str] = Field(
        default_factory=lambda: ["academic", "news", "reference", "tech"])
    user_agent: str = "llm-gym-collector/0.1 (+https://github.com/)"
    web_search: str = ""           # "" | searxng | brave | tavily (extra reach)
    searx_url: str = ""            # for self-hosted SearXNG (keyless)
    # Brave/Tavily keys are read from LLMGYM_WEB_SEARCH_KEY at call time, never stored.


class JudgeSettings(BaseModel):
    """Verifies the trained adapter. Local Ollama by default; public AI optional."""

    mode: str = "local"            # local | public
    ollama_model: str = ""         # blank -> use the large base model
    public_base_url: str = ""      # any OpenAI-compatible endpoint
    public_model: str = ""
    env_key_name: str = "LLMGYM_JUDGE_API_KEY"   # key read from env at call time
    key_item: str = ""             # optional Vaultwarden/Bitwarden item for the key
    eval_model: str = ""           # model that ANSWERS acceptance prompts; blank ->
                                   # auto (assigned adapter model, else its base)


class RemoteQueueSettings(BaseModel):
    """Hand training jobs to a shared external GPU queue (one job at a time for
    everyone) instead of training locally. Off by default — the gym then trains
    with its own backend. The token is read from the environment at call time."""

    enabled: bool = False
    url: str = "http://127.0.0.1:5600"
    token_env: str = "GYM_QUEUE_TOKEN"   # env var holding the queue token
    source: str = "llm-gym"              # submitter id shown in the queue
    priority: int = 10                   # higher = sooner; interactive default
    timeout: float = 30.0

    def token(self) -> str:
        return os.environ.get(self.token_env, "")


class AnonymizeSettings(BaseModel):
    """Strip PII + configured identifiers from data at ingest — on by default, so
    real names/emails/phones/secrets never reach the pool or the weights."""

    enabled: bool = True
    # Company / customer / external person names to redact (case-insensitive).
    terms: list[str] = Field(default_factory=list)
    # Heuristic Title-Case name redaction. Off by default (can over-redact).
    redact_names: bool = False


class SecuritySettings(BaseModel):
    """Prompt-injection defense trained INTO the adapters (defense in depth with
    the gateway): blend a shared inoculation seed into every pool, red-team each
    trained adapter, and optionally gate promotion on the result."""

    enabled: bool = True
    # Fraction of the training set drawn from the injection-defense seed.
    seed_ratio: float = 0.2
    # Hard ceiling: the seed may never exceed this fraction of the mixed set, so a
    # tiny task pool is never drowned by security examples.
    seed_max_fraction: float = 0.35
    # Run the red-team battery after each train and record the score.
    eval_after_train: bool = True
    # Gate promotion on the injection result (don't ship a leaky adapter).
    gate: bool = True
    gate_min_robustness: int = 70      # % of attack prompts that must be resisted
    gate_max_over_refusal: int = 20    # max % of benign requests wrongly refused


class RLHFSettings(BaseModel):
    """Direct Preference Optimization (DPO) — fine-tunes a LoRA adapter directly
    from human preference pairs (llm_gym/feedback.py: "answer A is better than
    answer B" for the same prompt) instead of single chat examples. Off by
    default: needs the peft extra's trl>=0.9, and needs preference pairs to
    exist before it can do anything useful."""

    enabled: bool = False
    beta: float = 0.1              # DPO temperature: higher = trust the pairs less
    learning_rate: float = 5e-6    # DPO is far more sensitive than SFT — keep this low
    iters: int = 200
    min_pairs: int = 20            # refuse to train below this many preference pairs
    # Also fold in pairs auto-derived from gold.jsonl (chosen) vs errors.jsonl
    # (rejected) for the same prompt, on top of explicit human-submitted pairs.
    auto_pairs_from_verify: bool = True


class CatalogSyncSettings(BaseModel):
    """Keep the adapters current on new products: a background loop periodically
    pulls the live shop catalog, merges new SKUs into the snapshot (so grounding /
    retrieval always reflects them) and feeds new products' grounded knowledge into
    the sales lanes' collected split. Non-destructive (merge + backup)."""

    enabled: bool = True
    ingest: bool = True              # also write new products' grounded data to pools
    interval_hours: int = 24
    startup_delay_sec: int = 300     # let the gym finish booting before the first pull


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000

    ollama_host: str = "http://localhost:11434"
    base_model_small: str = "qwen2.5:3b"
    base_model_large: str = "qwen2.5:14b"
    # The base the gym will train against. Empty -> chosen by the system check.
    active_base_model: str = ""

    pool_root: str = "./data/pool"
    adapters_dir: str = "./adapters"
    runs_dir: str = "./runs"
    db_path: str = "./gym.db"

    backend: str = "auto"  # auto | mlx | peft

    git: GitSettings = Field(default_factory=GitSettings)
    cooldown: CooldownSettings = Field(default_factory=CooldownSettings)
    training: TrainingDefaults = Field(default_factory=TrainingDefaults)
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    judge: JudgeSettings = Field(default_factory=JudgeSettings)
    rlhf: RLHFSettings = Field(default_factory=RLHFSettings)
    remote_queue: RemoteQueueSettings = Field(default_factory=RemoteQueueSettings)
    anonymize: AnonymizeSettings = Field(default_factory=AnonymizeSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    catalog_sync: CatalogSyncSettings = Field(default_factory=CatalogSyncSettings)
    # Auto-pipeline promote/rollback policy:
    #   confirm        — every finished run waits for a green/red click
    #   auto_verygood  — only 'sehr gut' promotes itself; gut/schlecht are gated
    #   full_auto      — promote good, recollect on weak, all without clicks
    pipeline_policy: str = "auto_verygood"
    pipeline_max_bad_rounds: int = 2   # stop recollecting after N weak rounds in a row
    # Resource profile for training — keeps the machine usable while it trains.
    #   work     — most headroom for you (fewest layers/threads, gentlest)
    #   balanced — sensible default
    #   fast     — when you're away (more layers/threads, less polite)
    train_profile: str = "balanced"
    # Continuous mode: a scheduler restarts an adapter's pipeline on its own once
    # its cooldown has passed AND enough new gold has been added since last train.
    continuous_enabled: bool = False
    continuous_min_new_gold: int = 5
    continuous_interval_sec: int = 300
    save_every: int = 50           # checkpoint cadence (steps) for resume
    # Where regenerable artifacts (fused/GGUF intermediates, archived old adapters)
    # may be offloaded when local disk runs low — local mounts and/or scp/ssh
    # targets (e.g. "/Volumes/ssd2/llm-gym", "user@nas:/path"). Tried in order:
    # first = primary, the rest are fall-backs.
    offload_targets: list[str] = Field(default_factory=list)
    # The disk watchdog archives regenerables to keep at least this many GB free.
    offload_threshold_gb: float = 40.0
    # On the shared trainer host a `gpu_queue.py` provides an OS-level GPU lease so
    # the gym never trains concurrently with other GPU trainers on the host.
    # This is the directory that holds it (added to sys.path at lease time). Empty
    # disables the lease (e.g. a laptop where the gym is the only GPU user).
    gpu_lease_path: str = "~"

    def profile(self) -> dict:
        prof = dict(TRAIN_PROFILES.get(self.train_profile, TRAIN_PROFILES["balanced"]))
        prof["val_batches"] = self.training.val_batches
        prof["early_stop_patience"] = self.training.early_stop_patience
        prof["early_stop_min_delta"] = self.training.early_stop_min_delta
        return prof

    def effective_lora(self, spec) -> tuple[int, float]:
        """(rank, scale) for training. A per-lane default rank is applied only when
        the adapter still carries the global default — an explicit per-adapter rank
        always wins — and rsLoRA scale is recomputed so the two stay consistent."""
        rank = spec.lora_rank
        if rank == self.training.lora_rank:
            low = (spec.name or "").lower()
            for key, r in self.training.lane_rank_defaults.items():
                if key in low:
                    rank = r
                    break
        if rank == spec.lora_rank:
            return rank, spec.lora_scale
        alpha = self.training.lora_alpha_factor * rank
        return rank, alpha / (rank ** 0.5)

    # --- path helpers (absolute, anchored at project root) ---
    def _abs(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (ROOT / path)

    @property
    def pool_path(self) -> Path:
        return self._abs(self.pool_root)

    @property
    def adapters_path(self) -> Path:
        return self._abs(self.adapters_dir)

    @property
    def runs_path(self) -> Path:
        return self._abs(self.runs_dir)

    @property
    def db_file(self) -> Path:
        return self._abs(self.db_path)


_ENV_MAP = {
    "LLMGYM_HOST": ("host", str),
    "LLMGYM_PORT": ("port", int),
    "LLMGYM_OLLAMA_HOST": ("ollama_host", str),
    "LLMGYM_BASE_MODEL_SMALL": ("base_model_small", str),
    "LLMGYM_BASE_MODEL_LARGE": ("base_model_large", str),
    "LLMGYM_POOL_ROOT": ("pool_root", str),
    "LLMGYM_BACKEND": ("backend", str),
}


def _apply_env(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for env_key, (field, cast) in _ENV_MAP.items():
        if env_key in os.environ and os.environ[env_key] != "":
            out[field] = cast(os.environ[env_key])
    if os.environ.get("LLMGYM_GIT_REMOTE"):
        out.setdefault("git", {})
        out["git"] = {**out.get("git", {}), "remote": os.environ["LLMGYM_GIT_REMOTE"]}
    if os.environ.get("LLMGYM_GIT_BRANCH"):
        out["git"] = {**out.get("git", {}), "branch": os.environ["LLMGYM_GIT_BRANCH"]}
    return out


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    return Settings(**_apply_env(data))


def save_settings(settings: Settings, path: Path = SETTINGS_PATH) -> None:
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
