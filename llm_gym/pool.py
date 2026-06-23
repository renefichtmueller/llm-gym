"""The training pool.

A pool is a named folder of JSONL files. Each line is one training example in
chat format:

    {"messages": [{"role": "user", "content": "..."},
                  {"role": "assistant", "content": "..."}],
     "_meta": {"source": "...", "verified": false, "quality_score": 70}}

Layout per pool (under POOL_ROOT/<name>/):
    train.jsonl    curated training split
    valid.jsonl    held-out evaluation split
    gold.jsonl     verified gold-standard pairs (highest trust)
    raw/           anything you dumped in but have not curated yet
    merged.jsonl   deduplicated union of train.jsonl + gold.jsonl (built on demand)

The pool can optionally live in a Git remote (any host). Git is never required.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def _hash_messages(messages: list[dict]) -> str:
    norm = json.dumps(
        [(m.get("role"), (m.get("content") or "").strip()) for m in messages],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _valid_example(obj: dict) -> bool:
    msgs = obj.get("messages")
    return isinstance(msgs, list) and len(msgs) >= 2 and all(
        isinstance(m, dict) and m.get("role") and m.get("content") for m in msgs
    )


@dataclass
class PoolStats:
    name: str
    train: int
    valid: int
    gold: int
    collected: int
    raw: int
    unique: int
    duplicates: int


class Pool:
    def __init__(self, root: Path, name: str) -> None:
        self.name = name
        self.dir = root / name
        (self.dir / "raw").mkdir(parents=True, exist_ok=True)

    # --- file helpers ---
    @property
    def train_file(self) -> Path:
        return self.dir / "train.jsonl"

    @property
    def valid_file(self) -> Path:
        return self.dir / "valid.jsonl"

    @property
    def gold_file(self) -> Path:
        return self.dir / "gold.jsonl"

    @property
    def collected_file(self) -> Path:
        return self.dir / "collected.jsonl"

    @property
    def merged_file(self) -> Path:
        return self.dir / "merged.jsonl"

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    @staticmethod
    def _append(path: Path, rows: Iterable[dict]) -> int:
        n = 0
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
        return n

    # --- public API ---
    def append(self, examples: list[dict], split: str = "train") -> dict:
        """Append valid examples to a split (train|valid|gold|raw). Skips dupes
        already present in train+gold."""
        target = {
            "train": self.train_file, "valid": self.valid_file,
            "gold": self.gold_file, "collected": self.collected_file,
            "raw": self.dir / "raw" / "ingest.jsonl",
        }[split]
        seen = {_hash_messages(e["messages"]) for e in
                self._read(self.train_file) + self._read(self.gold_file)
                if _valid_example(e)}
        kept, skipped = [], 0
        for ex in examples:
            if not _valid_example(ex):
                skipped += 1
                continue
            h = _hash_messages(ex["messages"])
            if split in ("train", "gold") and h in seen:
                skipped += 1
                continue
            seen.add(h)
            kept.append(ex)
        added = self._append(target, kept)
        return {"added": added, "skipped": skipped}

    def build_merged(self, min_tier: str | None = None) -> dict:
        """Deduplicated union of gold + train (+ collected at/above min_tier),
        with cross-split leakage removed (examples present in valid are dropped)."""
        from . import scoring
        valid_hashes = {_hash_messages(e["messages"]) for e in
                        self._read(self.valid_file) if _valid_example(e)}
        rows = self._read(self.gold_file) + self._read(self.train_file)
        collected_used = 0
        if min_tier:
            for ex in self._read(self.collected_file):
                tier = (ex.get("_meta", {}) or {}).get("tier", "bronze")
                if scoring.meets_tier(tier, min_tier):
                    rows.append(ex)
                    collected_used += 1
        seen: set[str] = set()
        merged: list[dict] = []
        leaked = 0
        for ex in rows:
            if not _valid_example(ex):
                continue
            h = _hash_messages(ex["messages"])
            if h in valid_hashes:
                leaked += 1
                continue
            if h in seen:
                continue
            seen.add(h)
            merged.append(ex)
        self.merged_file.write_text(
            "\n".join(json.dumps(m, ensure_ascii=False) for m in merged) + "\n"
            if merged else "", encoding="utf-8")
        return {"merged": len(merged), "dropped_leakage": leaked,
                "collected_used": collected_used}

    def merged_examples(self) -> list[dict]:
        """The merged set as a list (build_merged must have run; reads its file)."""
        return [e for e in self._read(self.merged_file) if _valid_example(e)]

    def split_for_training(self, min_tier: str | None = None, *,
                           val_ratio: float = 0.1, seed: int = 1234
                           ) -> tuple[list[dict], list[dict], dict]:
        """Build the merged training set, then guarantee a held-out valid split.

        A hand-curated valid.jsonl is used verbatim (and merged becomes train).
        Otherwise the merged set is split deterministically — `val_ratio` held out,
        disjoint from train — so the trainer can emit a real Val loss and the
        best-checkpoint selection has something to compare. Tiny pools (<6) get a
        1-example valid as a floor (mirrors the old trainers' small-pool fallback).
        Returns (train_rows, valid_rows, info)."""
        import random
        self.build_merged(min_tier)
        merged = self.merged_examples()
        curated = [e for e in self._read(self.valid_file) if _valid_example(e)]
        if curated:
            return merged, curated, {"valid_source": "curated",
                                     "train": len(merged), "valid": len(curated)}
        n = len(merged)
        if n < 6:
            return merged, (merged[:1] if merged else []), {
                "valid_source": "tiny", "train": n, "valid": 1 if merged else 0}
        order = list(range(n))
        random.Random(seed).shuffle(order)
        k = max(2, round(n * val_ratio))
        vidx = set(order[:k])
        train = [merged[i] for i in range(n) if i not in vidx]
        valid = [merged[i] for i in sorted(vidx)]
        return train, valid, {"valid_source": "auto-split",
                              "train": len(train), "valid": len(valid)}

    @staticmethod
    def _answers(rows: list[dict]) -> list[str]:
        out = []
        for ex in rows:
            for m in reversed(ex.get("messages", []) or []):
                if m.get("role") == "assistant":
                    out.append((m.get("content") or "").strip().lower())
                    break
        return out

    @staticmethod
    def answer_distinct_ratio(rows: list[dict]) -> float:
        """Fraction of distinct assistant answers — a pool whose answers are nearly
        all identical trains a parrot, not a skill. 0.0 when there are no answers."""
        a = Pool._answers(rows)
        return len(set(a)) / len(a) if a else 0.0

    @staticmethod
    def _is_label(s: str) -> bool:
        s = (s or "").strip()
        return 0 < len(s) <= 40 and len(s.split()) <= 5

    @staticmethod
    def distinctness_ok(rows: list[dict], min_ratio: float) -> tuple[bool, str]:
        """Answer-variety gate, classifier-aware. A classifier lane (answers are
        short labels, e.g. 'injection'/'safe') legitimately repeats a small label
        set — it only needs >=2 distinct labels. Free-text lanes need min_ratio
        distinct answers, else they'd train a parrot."""
        a = Pool._answers(rows)
        if not a:
            return False, "no answers"
        distinct = len(set(a))
        label_frac = sum(1 for x in a if Pool._is_label(x)) / len(a)
        if label_frac >= 0.7:
            return (distinct >= 2), f"classifier, {distinct} distinct labels"
        ratio = distinct / len(a)
        return (ratio >= min_ratio), f"{ratio:.0%} distinct answers"

    @staticmethod
    def write_jsonl(path: Path, rows: list[dict]) -> None:
        path.write_text(
            ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
            if rows else "", encoding="utf-8")

    def stats(self) -> PoolStats:
        train = [e for e in self._read(self.train_file) if _valid_example(e)]
        gold = [e for e in self._read(self.gold_file) if _valid_example(e)]
        raw_dir = self.dir / "raw"
        raw = sum(len(self._read(p)) for p in raw_dir.glob("*.jsonl"))
        hashes = [_hash_messages(e["messages"]) for e in train + gold]
        unique = len(set(hashes))
        collected = len([e for e in self._read(self.collected_file) if _valid_example(e)])
        return PoolStats(
            name=self.name, train=len(train),
            valid=len([e for e in self._read(self.valid_file) if _valid_example(e)]),
            gold=len(gold), collected=collected, raw=raw, unique=unique,
            duplicates=len(hashes) - unique,
        )

    def search(self, query: str, limit: int = 50) -> list[dict]:
        q = query.lower().strip()
        hits: list[dict] = []
        for split, path in (("gold", self.gold_file), ("train", self.train_file)):
            for i, ex in enumerate(self._read(path)):
                if not _valid_example(ex):
                    continue
                text = " ".join(m.get("content", "") for m in ex["messages"]).lower()
                if q in text:
                    hits.append({"split": split, "line": i, "example": ex})
                    if len(hits) >= limit:
                        return hits
        return hits


def list_pools(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# --- optional Git remote ---
def _strip_token(url: str) -> str:
    """Drop any embedded user[:token]@ from an http(s) URL so it's safe to store."""
    return re.sub(r"^(https?://)[^/@]*@", r"\1", url or "")


def _inject_token(url: str, token: str) -> str:
    """Put a token into an http(s) URL for a single push (never persisted)."""
    return re.sub(r"^(https?://)", rf"\1x-access-token:{token}@",
                  _strip_token(url), count=1)


def git_pull(pool_dir: Path, remote: str, branch: str = "main",
             token: str = "") -> dict:
    """Build/refresh a pool FROM a Git remote (Gitea, GitHub, …): clone the repo to
    a temp dir and copy its JSONL splits into the pool. The token is injected only
    for the clone (never stored), and masked in any error. Lets you keep a pool's
    canonical data in Git and auto-build the local pool from it."""
    if not remote:
        return {"ok": False, "msg": "No Git remote configured."}
    import shutil
    import tempfile
    clean = _strip_token(remote)
    auth = _inject_token(clean, token) if token else clean
    tmp = tempfile.mkdtemp()
    try:
        r = subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, auth, tmp],
                           text=True, capture_output=True, timeout=300)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout)[-300:]
            return {"ok": False, "msg": msg.replace(token, "***") if token else msg}
        copied = []
        for fn in ("train.jsonl", "valid.jsonl", "gold.jsonl", "collected.jsonl"):
            src = Path(tmp) / fn
            if src.exists():
                shutil.copy(src, pool_dir / fn)
                copied.append(fn)
        return {"ok": True, "copied": copied,
                "msg": f"built pool from git ({', '.join(copied) or 'no JSONL found'})"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def git_push(pool_dir: Path, remote: str, branch: str,
             name: str, email: str, token: str = "") -> dict:
    """Init (if needed), commit and push the pool folder to a Git remote.

    The remote is stored in .git/config WITHOUT any token. If a token is given it
    is injected only into the push URL (transient) and never written to disk; any
    token echoed back in an error is masked.
    """
    if not remote:
        return {"ok": False, "msg": "No Git remote configured."}
    clean = _strip_token(remote)

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=pool_dir, text=True,
                              capture_output=True)

    if not (pool_dir / ".git").exists():
        run("init", "-b", branch)
        run("config", "user.name", name)
        run("config", "user.email", email)
    if run("remote").stdout.strip() == "":
        run("remote", "add", "origin", clean)
    else:
        run("remote", "set-url", "origin", clean)   # ensure no stored token
    run("add", "-A")
    commit = run("commit", "-m", "pool: update training data")
    if "nothing to commit" in (commit.stdout + commit.stderr):
        return {"ok": True, "msg": "Nothing to commit."}
    target = _inject_token(clean, token) if token else "origin"
    push = run("push", "-u", target, branch)
    msg = (push.stderr or push.stdout)[-300:]
    if token:
        msg = msg.replace(token, "***")
    return {"ok": push.returncode == 0, "msg": msg}
