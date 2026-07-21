"""Anonymise text before it becomes training data.

Whatever the source — a collected web/Confluence page, a gold entry, or a manual
upload — real PII (emails, phones, IPs, secrets) and any identifiers you list
(company, customer or external person names) are stripped at ingest, so they never
end up in the pool or the trained weights. Configurable, on by default.
"""
from __future__ import annotations

import re

# Deterministic PII / secret patterns (order matters: secrets before generic).
_PII = [
    (re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
                re.DOTALL), "[key]"),
    # Provider-prefixed API keys/tokens (GitHub, OpenAI, HF, GitLab, Slack, …).
    (re.compile(r"\b(?:sk|ghp|gho|ghs|ghu|hf|nvapi|gsk|glpat|xox[baprs])[-_][A-Za-z0-9]{12,}\b"), "[token]"),
    # AWS access key IDs.
    (re.compile(r"\b(?:AKIA|ASIA|AROA|AIDA|AGPA|ANPA|ANVA)[0-9A-Z]{16}\b"), "[token]"),
    # Google API keys.
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[token]"),
    # Slack / incoming-webhook URLs.
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"), "[token]"),
    # JWTs (three base64url segments).
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"), "[token]"),
    # Generic bearer credentials in headers.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [token]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    # Phone-ish: a leading +/digit then >= 8 grouped digits; avoids plain integers.
    (re.compile(r"(?<![\w.])\+?\d[\d ()/.\-]{7,}\d(?![\w.])"), "[phone]"),
]

# Heuristic person-name: 2-3 Title-Case words. OFF by default — it also catches
# product names and places, so enable only when you accept that trade-off.
_NAME = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){1,2}\b")


def anonymize(text: str, *, terms=(), redact_names: bool = False) -> str:
    """Strip PII, then any configured `terms` (company/customer/external names),
    then optionally heuristic person-names."""
    if not text:
        return text
    for rx, repl in _PII:
        text = rx.sub(repl, text)
    for term in terms or ():
        t = (term or "").strip()
        if len(t) >= 2:
            text = re.sub(re.escape(t), "[redacted]", text, flags=re.IGNORECASE)
    if redact_names:
        text = _NAME.sub("[name]", text)
    return text


def anonymize_example(example: dict, *, terms=(), redact_names: bool = False) -> dict:
    """Return a copy of a chat training example with every message content
    anonymised. Leaves structure/roles intact."""
    msgs = [{**m, "content": anonymize(m.get("content", ""), terms=terms,
                                       redact_names=redact_names)}
            for m in (example.get("messages") or [])]
    return {**example, "messages": msgs}
