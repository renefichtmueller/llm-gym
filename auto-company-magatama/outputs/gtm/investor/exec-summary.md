<!-- DRAFT — human review before any external use. Proof-led, sanitized.
     All metrics are a 2026-04-20 snapshot from self-operated (dogfood)
     infrastructure, NOT customers. Refresh before use. No internal IPs/hosts. -->

# MAGATAMA 勾玉 — Executive Summary (DRAFT)

## What it is

Magatama is a **unified security operating system**. Where most tools stop at
detection, Magatama continues into correlation, routing, remediation,
verification, evidence generation, and learning — a closed
**signal → normalize → correlate → decide → act → verify → learn** loop.

It spans six pillars in one model: application security (符 Code), infrastructure
& telemetry security (天 Cloud), AI/LLM security (心 Mind), automated offensive
testing (雷 Strike), runtime guarding (鎧 Guard), and compliance (法 Comply).
Verified fixes become training data for the in-house **MagatamaLLM**, turning
remediation into a compounding engine rather than a static product.

## Why now / why it matters

Security is cross-domain, but the market is a patchwork of single-category
tools bolting on integrations. Magatama starts unified. That yields immediate
product differentiation, a credible AI-security-and-remediation narrative, and
a live system of record for verified fixes.

## Traction — operating proof, not customer revenue

We run Magatama as a live dogfood deployment on our own infrastructure. As of
the 2026-04-20 snapshot:

| Metric | Value |
|---|---|
| Verified fixes tracked | **627** (518 MagatamaLLM · 96 Claude · 13 Codex) |
| LLM-remediation success rate | **98.2%** (3,827 attempts) |
| Training artifacts in learning loop | **3,669** |
| Assets under management / monitored services | **52 / 25** |
| Hosts with live telemetry | **12** |
| Active exposures tracked / rollback-ready | **27 / 20** |
| Enforcers live | **5 of 6** |

**Honest framing:** this is operating evidence on self-operated infrastructure —
**pre-revenue, no paying customers yet.** It proves the architecture works end
to end at real scale on real systems; it does not yet prove market demand.

## Differentiation

- **心 Mind** — a first-class AI/LLM security pillar (12-layer detection,
  MCP-guard, agent-security, self-healing), not a bolt-on; several
  capabilities are first-to-market.
- **Closed remediation loop** — verify + learn compounds over time.
- **Category-spanning** — between ASPM, CNAPP/CSPM, AI-security, SecOps, and
  network response; a natural cross-sell layer for a strategic acquirer.

## What's real vs. roadmap (for due diligence)

- **Real & live:** the six-pillar loop, the L0–L13 telemetry/SOAR pipeline in
  Cloud, LLM remediation + learning, enforcement/rollback readiness, evidence.
- **Roadmap (not shipped):** multi-cloud CSPM (AWS/Azure/GCP), security-graph /
  toxic-combination analysis, Opengrep engine, GitHub/GitLab PR integration
  (Gitea integration exists). The Cloud pillar today is a host/workload SIEM-SOAR
  pipeline, not multi-cloud posture management.
- **Known pre-1.0 gaps:** low automated test coverage and CI hardening are the
  primary engineering work to 1.0.

## The plan & ask

Pre-launch at **v0.3.x**. Raising **[Betrag]** over **[Runway]** to: (1) harden
tests + CI to 1.0, (2) ship the go-to-market surface, (3) land **[N]** design
partners to convert operating proof into commercial validation, (4) mature the
autonomous-remediation path that is the long-term moat.

## Strategic buyer/partner fit

Security platforms, cloud/posture vendors, network-security vendors, AI/agent-
security buyers, and MSSP/MDR operators — each gains a remediation-and-evidence
operating layer that spans categories they currently cover in isolation.

---
*DRAFT for internal review. Not for distribution. Verify all figures and remove
placeholders before any external sharing.*
