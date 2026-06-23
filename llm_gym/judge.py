"""Check a trained adapter, then verify it against what it was supposed to do.

Two parts:
  1. run_acceptance — generate answers to the adapter's acceptance prompts using
     the base model + the trained adapter.
  2. judge — have a judge model grade each answer against the adapter's objective
     and capabilities (does it do what it promised?).

The judge runs locally on Ollama by default (offline, free). A public AI
(ChatGPT / Claude / Copilot via any OpenAI-compatible endpoint) can be switched
on in Settings — its key is read from the environment at call time and never
stored. Everything is built into the gym; nothing extra to install.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .ollama_client import OllamaClient
from .trainer.base import resolve_base

_JUDGE_PROMPT = """You are grading whether an AI answer does the job it was built for.

ADAPTER OBJECTIVE:
{objective}

CAPABILITIES IT SHOULD HAVE:
{capabilities}

THE PROMPT IT WAS GIVEN:
{prompt}

THE ANSWER IT PRODUCED:
{answer}

Grade strictly. Reply with ONLY a JSON object, no prose:
{{"score": <0-100>, "passed": <true|false>, "reason": "<one short sentence>"}}"""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"score": 0, "passed": False, "reason": "judge gave no JSON"}
    try:
        d = json.loads(m.group(0))
        return {"score": float(d.get("score", 0)),
                "passed": bool(d.get("passed", False)),
                "reason": str(d.get("reason", ""))[:200]}
    except Exception:
        return {"score": 0, "passed": False, "reason": "judge JSON unparseable"}


def _has_weights(adapter_dir: Path) -> bool:
    return (adapter_dir / "adapters.safetensors").exists() or \
           (adapter_dir / "adapter_model.safetensors").exists()


def _resolve_installed(name: str, installed: list[str], *,
                       family: bool = True) -> str | None:
    """Match a desired model name against installed Ollama tags. Ollama appends
    ':latest' to untagged models, so accept exact and ':latest'. The bare-prefix
    family match (e.g. 'qwen2.5' -> 'qwen2.5:14b') is only safe when resolving a
    BASE family; for the trained-adapter lookup pass family=False, otherwise an
    un-trained same-family tag (the base!) could be mislabelled as the trained
    model and silently judged instead of the adapter."""
    if not name:
        return None
    if name in installed:
        return name
    if f"{name}:latest" in installed:
        return f"{name}:latest"
    if family and ":" not in name:
        for m in installed:
            if m.split(":")[0] == name:
                return m
    return None


def pick_eval_model(spec, adapter_dir: Path, backend: str, settings):
    """Decide which model ANSWERS the acceptance prompts. Returns (model, mode, note):
      mlx         -> serve the real local adapter via mlx_lm (model is None)
      override    -> the explicit Settings eval model
      trained     -> the adapter assigned into Ollama (named after the adapter)
      base        -> the adapter's base model in Ollama (an honest, un-adapted baseline)
      unavailable -> nothing usable; note explains what to do (no fake score)."""
    if backend == "mlx" and _has_weights(adapter_dir):
        return None, "mlx", ""
    try:
        installed = [m.name for m in OllamaClient(settings.ollama_host).list_models()]
    except Exception as exc:  # noqa: BLE001
        return None, "unavailable", f"Ollama is not reachable ({exc})."
    override = (settings.judge.eval_model or "").strip()
    if override:
        m = _resolve_installed(override, installed)
        if m:
            return m, "override", ""
        return None, "unavailable", f"Eval model '{override}' is not in Ollama."
    trained = _resolve_installed(spec.name, installed, family=False)
    if trained:
        return trained, "trained", ""
    base = _resolve_installed(spec.base_model, installed)
    if base:
        return base, "base", ""
    return None, "unavailable", (
        f"No eval model in Ollama. Pull the base (ollama pull {spec.base_model}), "
        f"assign the trained adapter, or set an Eval model in Settings.")


def run_acceptance(spec, adapter_dir: Path, backend: str, settings) -> list[dict]:
    """Generate the adapter's answer for each acceptance prompt, recording which
    model produced it and in what mode."""
    prompts = spec.acceptance_prompts or []
    if not prompts:
        return []
    model, mode, note = pick_eval_model(spec, adapter_dir, backend, settings)
    system = spec.objective or spec.description or ""
    out = []
    for p in prompts:
        if mode == "mlx":
            ans = _generate_mlx(spec, adapter_dir, p)
        elif model:
            ans = _generate_ollama(settings, model, p, system)
        else:
            ans = f"(not evaluated — {note})"
        out.append({"prompt": p, "answer": ans, "eval_model": model or "", "eval_mode": mode})
    return out


def _generate_ollama(settings, model: str, prompt: str, system: str) -> str:
    try:
        txt = OllamaClient(settings.ollama_host, timeout=240).generate(
            model, prompt, num_predict=400, system=system)
        return txt.strip()[:4000] or "(empty response)"
    except Exception as exc:  # noqa: BLE001
        return f"(generation failed: {exc})"


def _generate_mlx(spec, adapter_dir: Path, prompt: str) -> str:
    repo = resolve_base(spec.base_model, "mlx")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "mlx_lm", "generate", "--model", repo,
             "--adapter-path", str(adapter_dir), "--prompt", prompt,
             "--max-tokens", "256", "--temp", "0.0"],
            capture_output=True, text=True, timeout=300)
        return (r.stdout or "").strip()[-2000:] or "(no output)"
    except Exception as exc:  # noqa: BLE001
        return f"(generation failed: {exc})"


def judge(spec, items: list[dict], settings) -> dict:
    """Grade each (prompt, answer). Returns per-item verdicts + an aggregate."""
    jcfg = settings.judge
    caps = "\n".join(f"- {c}" for c in (spec.capabilities or [])) or "(none stated)"
    results = []
    for it in items:
        body = _JUDGE_PROMPT.format(objective=spec.objective or spec.description,
                                    capabilities=caps, prompt=it["prompt"],
                                    answer=it["answer"])
        verdict = (_judge_public(body, jcfg) if jcfg.mode == "public"
                   else _judge_local(body, jcfg, settings))
        results.append({**it, **verdict})
    if results:
        passed = sum(1 for r in results if r["passed"])
        avg = round(sum(r["score"] for r in results) / len(results), 1)
        pass_rate = round(100 * passed / len(results))
    else:
        passed, avg, pass_rate = 0, 0.0, 0
    # Surface how answers were produced so a low score from an un-adapted base
    # model (or an unreachable Ollama) isn't read as the adapter failing.
    eval_mode = results[0].get("eval_mode", "") if results else ""
    eval_model = results[0].get("eval_model", "") if results else ""
    return {"items": results, "avg_score": avg, "pass_rate": pass_rate,
            "passed": passed, "total": len(results),
            "judge": jcfg.mode, "overall": _overall(pass_rate, avg),
            "eval_mode": eval_mode, "eval_model": eval_model}


# ---- frozen held-out eval set: answer-vs-expected, label scoring, head-to-head ----

_EVAL_JUDGE_PROMPT = """Grade how well the ANSWER matches the EXPECTED answer.

PROMPT:
{prompt}

EXPECTED ANSWER:
{expected}

THE ANSWER PRODUCED:
{answer}

Reply with ONLY a JSON object: {{"score": <0-100>, "passed": <true|false>, "reason": "<short>"}}"""


def _normalize_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _looks_like_label(expected: str) -> bool:
    """Classifier-style ground truth: a short label/class, not free prose. Such
    answers get scored by real accuracy (exact match) rather than an LLM's opinion."""
    e = (expected or "").strip()
    return 0 < len(e) <= 40 and len(e.split()) <= 5


def score_against_expected(answer: str, expected: str) -> dict:
    """Score a label/classifier answer against ground truth by normalized match.
    Returns {score, passed, method}."""
    na, ne = _normalize_label(answer), _normalize_label(expected)
    if not ne:
        return {"score": 0, "passed": False, "method": "none"}
    hit = na == ne or (f" {ne} " in f" {na} ")
    return {"score": 100 if hit else 0, "passed": hit, "method": "label"}


def run_eval_set(spec, eval_examples: list[dict], answer_fn, settings) -> dict:
    """Score a model over a FROZEN held-out eval set. Each example carries a user
    prompt and a KNOWN expected assistant answer (it was excluded from training).
    Classifier-style expected answers are scored by exact label match (real
    accuracy, #7); prose by a judge-vs-expected grade. answer_fn(prompt)->str."""
    items = []
    for ex in eval_examples:
        msgs = ex.get("messages", []) or []
        user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
        expected = next((m.get("content", "") for m in reversed(msgs)
                         if m.get("role") == "assistant"), "")
        if not user or not expected:
            continue
        answer = answer_fn(user)
        if _looks_like_label(expected):
            sc = score_against_expected(answer, expected)
        else:
            body = _EVAL_JUDGE_PROMPT.format(prompt=user, expected=expected, answer=answer)
            v = (_judge_public(body, settings.judge) if settings.judge.mode == "public"
                 else _judge_local(body, settings.judge, settings))
            sc = {"score": v["score"], "passed": v["passed"], "method": "judge"}
        items.append({"prompt": user, "expected": expected, "answer": answer, **sc})
    if not items:
        return {"n": 0, "pass_rate": 0, "avg_score": 0.0, "items": []}
    passed = sum(1 for i in items if i["passed"])
    return {"n": len(items), "passed": passed,
            "pass_rate": round(100 * passed / len(items)),
            "avg_score": round(sum(i["score"] for i in items) / len(items), 1),
            "items": items}


def decide_head_to_head(candidate: dict, champion: dict | None,
                        margin: float = 3.0) -> str:
    """Compare candidate vs champion on the SAME frozen eval set (#6).
      no_champion     — nothing to compare against (first version)
      no_eval         — the eval set was empty
      candidate_wins  — candidate >= champion - margin on both metrics
      champion_wins   — candidate clearly below the champion (don't auto-ship)"""
    if not candidate or candidate.get("n", 0) == 0:
        return "no_eval"
    if not champion or champion.get("n", 0) == 0:
        return "no_champion"
    worse = (candidate["pass_rate"] < champion["pass_rate"] - margin
             or candidate["avg_score"] < champion["avg_score"] - margin)
    return "champion_wins" if worse else "candidate_wins"


def verify_against_eval_set(spec, adapter_dir: Path, backend: str, settings,
                            eval_examples: list[dict]) -> dict:
    """Run the candidate (trained adapter) AND, if it exists, the live champion
    Ollama model over the same frozen eval set, then decide the head-to-head.
    Returns {candidate, champion, decision}. champion is None if no live tag."""
    if not eval_examples:
        return {"candidate": {"n": 0}, "champion": None, "decision": "no_eval"}
    system = spec.objective or spec.description or ""

    def candidate_answer(prompt: str) -> str:
        if backend == "mlx" and _has_weights(adapter_dir):
            return _generate_mlx(spec, adapter_dir, prompt)
        # candidate isn't an Ollama tag yet during verify; fall back to base+adapter
        return _generate_mlx(spec, adapter_dir, prompt)

    cand = run_eval_set(spec, eval_examples, candidate_answer, settings)
    # The champion is the currently-live Ollama model (the previous version), still
    # tagged under the adapter name until this candidate is promoted.
    try:
        installed = [m.name for m in OllamaClient(settings.ollama_host).list_models()]
    except Exception:  # noqa: BLE001
        installed = []
    champ_tag = _resolve_installed(spec.name, installed, family=False)
    if champ_tag:
        champ = run_eval_set(
            spec, eval_examples,
            lambda p: _generate_ollama(settings, champ_tag, p, system), settings)
        return {"candidate": cand, "champion": champ, "base": None,
                "decision": decide_head_to_head(cand, champ)}
    # No champion (first version): it should at least beat the un-adapted base.
    base = None
    base_tag = _resolve_installed(spec.base_model, installed)
    if base_tag:
        base = run_eval_set(
            spec, eval_examples,
            lambda p: _generate_ollama(settings, base_tag, p, system), settings)
        if base.get("n"):
            worse = (cand["pass_rate"] < base["pass_rate"] - 3
                     or cand["avg_score"] < base["avg_score"] - 3)
            return {"candidate": cand, "champion": None, "base": base,
                    "decision": "base_floor_fail" if worse else "beats_base"}
    return {"candidate": cand, "champion": None, "base": base, "decision": "no_champion"}


def _overall(pass_rate: int, avg: float) -> str:
    if pass_rate >= 80 and avg >= 75:
        return "Matches the objective — ship it."
    if pass_rate >= 50:
        return "Partly there — improve the pool and retrain."
    return "Does not meet the objective yet."


_PAIR_QUALITY_PROMPT = """Rate this as a TRAINING example for an AI adapter.

OBJECTIVE: {objective}

QUESTION:
{user}

ANSWER:
{answer}

A perfect pair is a realistic, on-objective question with a correct, complete,
well-formed expert answer that directly addresses it — no refusals, no meta-talk,
no errors, no fluff. Score low if the answer dodges, is off-topic, incomplete, or
wrong. Reply with ONLY JSON: {{"score": <0-100>, "passed": <true if it is a clean,
usable training pair>, "reason": "<one short sentence>"}}"""


def score_pair_quality(spec, user: str, answer: str, settings) -> dict:
    """LLM grade of ONE candidate training pair — the active 'search for perfect
    pairs' tool, used to curate the highest-quality data (e.g. a lane's gold set).
    Returns {score, passed, perfect, reason}; a low score if no judge is reachable,
    so it can never wave junk through."""
    body = _PAIR_QUALITY_PROMPT.format(
        objective=(spec.objective or spec.description or "")[:600],
        user=(user or "")[:1500], answer=(answer or "")[:2500])
    v = (_judge_public(body, settings.judge) if settings.judge.mode == "public"
         else _judge_local(body, settings.judge, settings))
    sc = float(v.get("score", 0) or 0)
    passed = bool(v.get("passed", False))
    return {"score": sc, "passed": passed, "perfect": bool(passed and sc >= 85),
            "reason": str(v.get("reason", ""))[:200]}


def _judge_local(body: str, jcfg, settings) -> dict:
    model = jcfg.ollama_model or settings.base_model_large
    try:
        # Large local judges (e.g. qwen2.5:14b) can take well over the default
        # 30s, especially cold; give them room so a slow judge isn't a 0.
        return _extract_json(OllamaClient(settings.ollama_host, timeout=240).generate(
            model, body, num_predict=200))
    except Exception as exc:  # noqa: BLE001
        return {"score": 0, "passed": False, "reason": f"local judge error: {exc}"}


def _judge_public(body: str, jcfg) -> dict:
    from . import vault
    key = vault.resolve(jcfg.env_key_name, getattr(jcfg, "key_item", ""))
    if not key or not jcfg.public_base_url:
        return {"score": 0, "passed": False,
                "reason": f"public judge not configured (set {jcfg.env_key_name} "
                          "and the endpoint in Settings)"}
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{jcfg.public_base_url.rstrip('/')}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": jcfg.public_model,
                             "messages": [{"role": "user", "content": body}],
                             "temperature": 0})
            txt = r.json()["choices"][0]["message"]["content"]
        return _extract_json(txt)
    except Exception as exc:  # noqa: BLE001
        return {"score": 0, "passed": False, "reason": f"public judge error: {exc}"}


def save_verify(artifact_dir: Path, result: dict) -> None:
    """Persist the latest verify outcome next to the trained adapter, so the live
    view can show a finished run's verdict. No-op if the adapter isn't trained."""
    if not artifact_dir.exists():
        return
    keep = {k: result.get(k) for k in
            ("pass_rate", "avg_score", "overall", "eval_mode", "eval_model")}
    keep["ts"] = time.time()
    try:
        (artifact_dir / "last_verify.json").write_text(
            json.dumps(keep), encoding="utf-8")
    except OSError:
        pass


def load_verify(artifact_dir: Path) -> dict | None:
    p = artifact_dir / "last_verify.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
