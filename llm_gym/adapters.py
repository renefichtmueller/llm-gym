"""Adapter definitions.

An adapter is defined before it is trained. The definition says what it should
*do*, which base it binds to, and which pool feeds it. This is the JSON a user
authors (or fills in via the UI) and what the trainer consumes.

A LoRA adapter is bound to the exact base model it was trained on. You cannot
train on qwen2.5:3b and serve on qwen2.5:14b. The gym records `base_model` per
adapter and only offers compatible base+adapter pairings at assign time.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# Names become file names and pool folder names, so keep them path-safe: no
# slashes, no dots-only, no surprises. Letters, digits, dot, underscore, hyphen.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def valid_adapter_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch(name or "")) and name not in (".", "..")


class ResearchBrief(BaseModel):
    """What the adapter is actually about — the context the advisor/collector use
    to target the right data. Stored on the adapter."""

    subject: str = ""            # the topic / field it researches
    scope: str = ""              # what is in scope and what is out
    questions: list[str] = Field(default_factory=list)  # key sub-questions to cover
    languages: list[str] = Field(default_factory=list)  # e.g. ["en", "de"]
    recency_years: int = 0       # 0 = any; else prefer the last N years

    @property
    def is_thin(self) -> bool:
        return not (self.subject.strip() or self.questions)


class ConfluenceConfig(BaseModel):
    """Optional Confluence source for an adapter. The token is stored encrypted
    (token_enc); plaintext never touches disk or the API response."""

    enabled: bool = False
    base_url: str = ""           # REST base, e.g. https://confluence.example.com/rest/api
    auth_type: str = "bearer"    # bearer (PAT, Server/DC) | basic (email + token, Cloud)
    email: str = ""              # for basic auth
    space: str = ""              # optional space key to restrict the search
    token_enc: str = ""          # AES-256-GCM encrypted token (persisted)
    token: str = ""              # transient plaintext input only — never persisted


class JiraConfig(BaseModel):
    """Optional Jira source (incl. Jira Service Management / ITSM). REST v2 + JQL.
    Token stored encrypted (token_enc); plaintext never persisted or returned."""

    enabled: bool = False
    base_url: str = ""           # REST base, e.g. https://jira.example.com/rest/api/2
    auth_type: str = "bearer"    # bearer (PAT, Server/DC) | basic (email + token, Cloud)
    email: str = ""              # for basic auth
    project: str = ""            # optional project key to restrict (e.g. an ITSM desk)
    token_enc: str = ""          # AES-256-GCM encrypted token
    token: str = ""              # transient plaintext input only


class AdapterSpec(BaseModel):
    # --- identity & intent ---
    name: str
    description: str = ""                      # one line: what this adapter is for
    objective: str = ""                        # longer: what it should be able to do
    capabilities: list[str] = Field(default_factory=list)      # bullet acceptance list
    acceptance_prompts: list[str] = Field(default_factory=list)  # smoke-test prompts
    brief: ResearchBrief = Field(default_factory=ResearchBrief)  # what it is about
    confluence: ConfluenceConfig = Field(default_factory=ConfluenceConfig)  # optional source
    jira: JiraConfig = Field(default_factory=JiraConfig)         # optional source (incl. ITSM/JSM)

    # --- binding & data ---
    base_model: str = "qwen2.5:3b"             # the base it trains on AND serves on
    pool: str = ""                             # pool name that feeds it
    min_tier: str = "gold"                     # lowest data tier allowed into training

    # --- cooldown (computed from the last run; enforced by the queue) ---
    optimal_cooldown_min: int = 0              # recommended wait before next train
    cooldown_until: float = 0.0                # epoch; cannot train before this

    # --- LoRA hyper-parameters (rsLoRA) ---
    lora_rank: int = 16
    lora_alpha_factor: int = 2                 # alpha = factor * rank
    lora_dropout: float = 0.0
    learning_rate: float = 1e-5
    iters: int = 600

    # --- bookkeeping ---
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        # Names become file/folder names — reject anything path-unsafe at the door,
        # so neither save nor pool creation can escape their directories.
        if not valid_adapter_name(v):
            raise ValueError("name must be 1–64 chars: letters, digits, '.', "
                             "'_', '-' (and not start with '.').")
        return v

    @field_validator("pool")
    @classmethod
    def _check_pool(cls, v: str) -> str:
        if v and not valid_adapter_name(v):
            raise ValueError("pool must be 1–64 chars: letters, digits, '.', "
                             "'_', '-' (and not start with '.').")
        return v

    @property
    def lora_alpha(self) -> int:
        return self.lora_alpha_factor * self.lora_rank

    @property
    def lora_scale(self) -> float:
        # rsLoRA: scale = alpha / sqrt(rank)
        return round(self.lora_alpha / math.sqrt(self.lora_rank), 4)


class AdapterStore:
    def __init__(self, adapters_dir: Path) -> None:
        self.dir = adapters_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _spec_path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def save(self, spec: AdapterSpec) -> AdapterSpec:
        if not spec.created_at:
            spec.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._spec_path(spec.name).write_text(
            spec.model_dump_json(indent=2), encoding="utf-8")
        return spec

    def load(self, name: str) -> AdapterSpec | None:
        path = self._spec_path(name)
        if not path.exists():
            return None
        return AdapterSpec(**json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[AdapterSpec]:
        specs: list[AdapterSpec] = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                specs.append(AdapterSpec(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001 — skip malformed specs, don't crash the list
                continue
        return specs

    def artifact_dir(self, name: str) -> Path:
        return self.dir / name

    def has_artifact(self, name: str) -> bool:
        d = self.artifact_dir(name)
        return (d / "adapters.safetensors").exists() or (d / "adapter_model.safetensors").exists()

    def rename(self, old: str, new: str, *, new_pool: str | None = None) -> AdapterSpec:
        """Move an adapter's spec file and its trained-artifact directory to a new
        name, in place. Optionally rewrite the pool field (when the pool tracked
        the adapter name 1:1). The caller owns moving the pool *folder* and the
        queue/pipeline history — this only touches the adapter's own files.

        Raises ValueError on an invalid new name, a missing source, or a target
        that already exists.
        """
        if not valid_adapter_name(new):
            raise ValueError(
                "Name must be 1–64 chars: letters, digits, '.', '_', '-' "
                "(and not start with '.').")
        spec = self.load(old)
        if spec is None:
            raise ValueError(f"No adapter named '{old}'.")
        if new == old:
            return spec
        if self._spec_path(new).exists() or self.artifact_dir(new).exists():
            raise ValueError(f"An adapter named '{new}' already exists.")

        spec.name = new
        if new_pool is not None:
            spec.pool = new_pool
        # Commit the new spec first, then move the trained artifacts, then drop
        # the old spec last. If any step fails the original spec+artifacts still
        # resolve together — worst case a duplicate spec, never weights orphaned
        # under a name with no spec.
        old_art, new_art = self.artifact_dir(old), self.artifact_dir(new)
        self.save(spec)                       # writes adapters/<new>.json
        try:
            if old_art.exists() and old_art.is_dir():
                old_art.rename(new_art)
        except OSError:
            self._spec_path(new).unlink(missing_ok=True)   # undo the new spec
            raise
        self._spec_path(old).unlink(missing_ok=True)
        return spec

    def delete(self, name: str) -> bool:
        """Remove the adapter spec and its trained artifacts. Returns False if the
        spec did not exist. Pool data is left untouched (caller decides)."""
        import shutil
        path = self._spec_path(name)
        existed = path.exists()
        if existed:
            path.unlink()
        art = self.artifact_dir(name)
        if art.exists() and art.is_dir():
            shutil.rmtree(art, ignore_errors=True)
        return existed
