"""The intelligence behind the adapter maker.

Given an adapter's name/description/objective, the advisor proposes a collection
plan: which source families to search, concrete pointers (which specific source
and why), a set of good search queries (broad-to-narrow), a recommended data
tier, and suggestions to sharpen the adapter itself (capabilities + acceptance
prompts).

It uses the local Ollama model (offline, keyless, already part of the gym). If
no capable model answers, it falls back to a transparent keyword heuristic, so
the feature always returns something useful.
"""
from __future__ import annotations

import json
import re

from . import collector, scoring
from .ollama_client import OllamaClient

_CATS = {"academic", "news", "reference", "tech"}

_PROMPT = """You are a research strategist configuring an automated data collector that
feeds a fine-tuning adapter. Decide WHAT to search and WHERE.

ADAPTER
name: {name}
description: {description}
objective: {objective}
capabilities:
{capabilities}

RESEARCH BRIEF / CONTEXT:
{context}

SUBJECT TO RESEARCH: {subject}

The collector can search these source families:
- academic  (OpenAlex, Crossref, arXiv, Semantic Scholar, Europe PMC, DOAJ, PLOS) — universities & papers
- news      (Google News agencies, GDELT, Wikinews) — current events & reporting
- reference (Wikipedia) — background & definitions
- tech      (Hacker News) — software/engineering discussion

Reply with ONLY a JSON object, no prose:
{{
  "domain": "<one short phrase>",
  "source_categories": ["academic", ...],
  "emphasis": ["<specific source>: <why it fits>", ...],
  "queries": ["<search query>", ...],
  "min_tier": "gold",
  "suggested_capabilities": ["<short skill>", ...],
  "suggested_acceptance_prompts": ["<test prompt>", ...],
  "rationale": "<2-3 sentences>"
}}
Rules: classify the SUBJECT's domain, route sources to it, and write 6-10 search
queries researching it — broad survey queries first, then specific angles.
Honour the brief's scope, key questions, languages (default English + German) and
recency. 4-8 capabilities, 4-6 acceptance prompts. Only use the four family names."""

_CLARIFY_PROMPT = """An adapter should research a subject, but its brief is thin:

name: {name}
objective: {objective}
brief so far: {context}

Ask 2-4 SHORT questions that would let you target the right data — pin down the
exact subject, the scope (what's in/out), the languages, and how recent the data
must be. Reply with ONLY a JSON array of question strings, no prose."""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _clean(plan: dict, spec, topic: str = "") -> dict:
    cats = [c for c in plan.get("source_categories", []) if c in _CATS]
    plan["source_categories"] = cats or _heuristic(spec, topic)["source_categories"]
    plan["queries"] = [q for q in plan.get("queries", []) if isinstance(q, str) and q.strip()][:10]
    if not plan["queries"]:
        plan["queries"] = _heuristic(spec, topic)["queries"]
    plan.setdefault("min_tier", "gold")
    if plan["min_tier"] not in ("platin", "gold", "silver"):
        plan["min_tier"] = "gold"
    for k in ("emphasis", "suggested_capabilities", "suggested_acceptance_prompts"):
        plan[k] = [s for s in plan.get(k, []) if isinstance(s, str) and s.strip()]
    plan.setdefault("domain", "")
    plan.setdefault("rationale", "")
    return plan


def _context(spec, topic: str, answers: list[str]) -> tuple[str, str]:
    """Return (subject, context-block) from the adapter's brief + this run's
    topic + any clarification answers."""
    b = spec.brief
    subject = (topic.strip() or b.subject.strip()
               or (answers[0].strip() if answers else "")).strip()
    parts = []
    if b.subject.strip():
        parts.append(f"subject: {b.subject.strip()}")
    if topic.strip():
        parts.append(f"topic this run: {topic.strip()}")
    if b.scope.strip():
        parts.append(f"scope: {b.scope.strip()}")
    if b.questions:
        parts.append("key questions: " + "; ".join(b.questions))
    if b.languages:
        parts.append("languages: " + ", ".join(b.languages))
    if b.recency_years:
        parts.append(f"recency: prefer the last {b.recency_years} years")
    if answers:
        parts.append("clarifications: " + " | ".join(a.strip() for a in answers))
    return subject, ("\n".join(parts) or "(no brief provided)")


def _clarify(spec, settings, context: str) -> list[str]:
    model = settings.judge.ollama_model or settings.base_model_large
    body = _CLARIFY_PROMPT.format(name=spec.name, objective=spec.objective or "",
                                  context=context)
    try:
        raw = OllamaClient(settings.ollama_host, timeout=180).generate(
            model, body, num_predict=300)
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            qs = [str(q).strip() for q in json.loads(m.group(0)) if str(q).strip()]
            if qs:
                return qs[:4]
    except Exception:
        pass
    return ["Worum genau geht es — Thema bzw. Fachgebiet?",
            "Welcher Ausschnitt ist relevant (was rein, was raus)?",
            "Welche Sprachen sollen die Quellen haben?",
            "Wie aktuell müssen die Daten sein (z.B. letzte 5 Jahre)?"]


def advise(spec, settings, topic: str = "", answers: list[str] | None = None) -> dict:
    answers = [a for a in (answers or []) if a and str(a).strip()]
    subject, context = _context(spec, topic, answers)

    # Thin context -> ask clarifying questions instead of guessing a subject.
    if not subject:
        plan = _heuristic(spec, "")
        plan.update({"clarifying_questions": _clarify(spec, settings, context),
                     "needs_clarification": True, "subject": "",
                     "engine": "clarification"})
        return plan

    caps = "\n".join(f"- {c}" for c in (spec.capabilities or [])) or "(none yet)"
    body = _PROMPT.format(name=spec.name, description=spec.description or "",
                          objective=spec.objective or "", capabilities=caps,
                          context=context, subject=subject)
    model = settings.judge.ollama_model or settings.base_model_large
    try:
        raw = OllamaClient(settings.ollama_host, timeout=240).generate(
            model, body, num_predict=700)
        plan = _extract_json(raw)
        if plan.get("queries") or plan.get("source_categories"):
            plan = _clean(plan, spec, subject)
            plan.update({"engine": f"local:{model}", "subject": subject,
                         "clarifying_questions": [], "needs_clarification": False})
            return plan
    except Exception:
        pass
    plan = _heuristic(spec, subject)
    plan.update({"engine": "heuristic (no LLM available)", "subject": subject,
                 "clarifying_questions": [], "needs_clarification": False})
    return plan


# --- transparent keyword fallback ---------------------------------------------
_ROUTES = [
    (("medic", "health", "clinical", "patient", "disease", "drug", "therap", "bio", "gesundheit", "krank"),
     ["academic", "reference", "news"], "Europe PMC + PLOS for biomedical evidence"),
    (("security", "vulnerab", "exploit", "malware", "cyber", "attack", "threat", "sicherheit"),
     ["academic", "tech", "news"], "arXiv + Hacker News for security tooling, news for incidents"),
    (("ai", "ml", "llm", "model", "algorithm", "software", "code", "network", "comput", "daten"),
     ["academic", "tech", "reference"], "arXiv + Hacker News for CS/AI"),
    (("market", "business", "finance", "industry", "competitor", "policy", "regulation", "law",
      "markt", "wirtschaft", "recht"),
     ["news", "academic", "reference"], "Google News + Crossref for market & policy"),
    (("news", "current", "event", "politic", "war", "election", "nachricht", "ereignis"),
     ["news", "reference"], "Google News + GDELT for current reporting"),
]


def _heuristic(spec, topic: str = "") -> dict:
    text = " ".join([topic, spec.name or "", spec.description or "",
                     spec.objective or "", " ".join(spec.capabilities or [])]).lower()
    cats, emphasis = ["academic", "reference", "news"], "Broad academic + reference net"
    for keys, c, why in _ROUTES:
        if any(k in text for k in keys):
            cats, emphasis = c, why
            break
    queries = collector.derive_queries(spec.model_dump(), topic)
    return {
        "domain": topic.strip() or "(keyword-routed)",
        "source_categories": cats,
        "emphasis": [emphasis],
        "queries": queries[:10],
        "min_tier": "gold",
        "suggested_capabilities": [
            "Frage in Teilfragen zerlegen", "Quellen nach Typ & Autorität gewichten",
            "Aussagen über mehrere Quellen triangulieren",
            "Konfidenz kennzeichnen (HIGH/CONFLICTING/UNVERIFIED)",
            "Wissenslücken ausdrücklich benennen"],
        "suggested_acceptance_prompts": [
            "Fasse den Forschungsstand zu <Thema> zusammen, Kernaussage zuerst, mit Konfidenz pro Aussage.",
            "Vergleiche zwei Positionen zu <Thema> und nenne, was unbelegt ist."],
        "rationale": "Heuristische Routing-Empfehlung anhand der Schlüsselwörter im Adapter-Ziel.",
    }
