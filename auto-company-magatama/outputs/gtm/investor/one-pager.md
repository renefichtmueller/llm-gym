<!-- DRAFT — human review before any external use. Numbers = 2026-04-20 snapshot,
     from self-operated (dogfood) infrastructure, NOT paying customers.
     Refresh with current numbers and strip any internal identifiers before sending. -->

# MAGATAMA 勾玉 — Investor One-Pager (DRAFT)

**Unified security operating system that closes the loop: detect → correlate →
remediate → verify → learn.** Most security tools stop at detection. Magatama
continues into remediation, evidence, and self-improvement.

## The gap we close

Security work is cross-domain by default, but tooling is fragmented across
AppSec, cloud/CNAPP, AI/LLM security, runtime, compliance, and network. Teams
drown in findings and still fix by hand. Magatama unifies these planes into one
operating loop and drives verified remediation — increasingly autonomously.

## Six pillars, one loop

符 Code · 天 Cloud · 心 Mind (AI/LLM security) · 雷 Strike (offensive) ·
鎧 Guard · 法 Comply — feeding one shared model with an LLM-first remediation
engine (**MagatamaLLM**) plus Claude/Codex and human operators as resolvers.

## Operating proof (self-hosted deployment, 2026-04-20 snapshot)

> Generated on our own infrastructure as a live dogfood deployment — this is
> operating evidence, **not customer revenue**.

- **627 verified fixes** tracked end-to-end (518 by our own MagatamaLLM)
- **98.2% success rate on LLM-driven remediation** (3,827 attempts)
- **3,669 training artifacts** feeding the learning loop — verified fixes
  become model training data (compounding, not static)
- **52 assets**, **25 monitored services**, **12 hosts** with live telemetry
- **27 active exposures** tracked, **20 rollback-ready**; 5 of 6 enforcers live

## Why it's a differentiator

- **心 Mind** is a rare, deep AI/LLM-security pillar (12-layer detection,
  MCP-guard, agent-security) — several capabilities are first-to-market.
- The **verify-and-learn loop** turns remediation into a compounding asset.
- Category-spanning: sits between ASPM, CNAPP, AI-security, and SecOps —
  cross-sell surface for a strategic acquirer.

## Stage & the ask

Pre-launch, **v0.3.x**, architecture built and live-operating on own infra.
Raising **[Betrag]** to reach 1.0 and first design-partner customers:
harden test coverage & CI, ship the go-to-market surface, and convert
operating proof into commercial validation.

*Honest status: pre-revenue; the numbers above are self-operated proof, not
paying customers. Roadmap items (multi-cloud CSPM, security-graph, Opengrep)
are marked as roadmap, not shipped.*

---
*Contact: [Name] · [Kontakt] — draft for internal review, not for distribution.*
