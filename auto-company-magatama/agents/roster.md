# 14 Build-Rollen → echte Magatama-Pakete

**Wichtig:** Magatama hat bereits `.claude/agents/` (18 Rollen) und
`.codex/agents/` (explorer, reviewer, docs_researcher) mit
`approval_policy = "on-request"` + `strict`-Profil (read-only). Dieses
Kit **erfindet nichts Neues**, sondern nutzt genau diese Harness und
ergänzt nur die **Breite an Builder-Rollen**, um den pre-1.0
Paket-Backlog zu parallelisieren. Vorhandene Rollen wiederverwenden!

| # | Build-Rolle | Vorhandene Magatama-Rolle nutzen | Ziel-Pakete (allow) |
|---|-------------|----------------------------------|---------------------|
| 1 | Orchestrator | planner | consensus/Backlog, keine Code-Writes an Infra |
| 2 | Product Manager | planner | Tickets/Specs aus FEATURE-TABLE.md |
| 3 | Architect | architect, code-architect | packages/core, Modulgrenzen |
| 4 | Frontend Eng | e2e-runner, performance-optimizer | packages/dashboard, packages/web |
| 5 | Backend Eng | code-architect | packages/code, packages/core (Mock-Daten) |
| 6 | Data/ML Eng | (neu) ml-builder | packages/mind, packages/fine-tuner, training-data |
| 7 | QA/Test Eng | tdd-guide, pr-test-analyzer, e2e-runner | packages/**/tests |
| 8 | Security Reviewer | security-reviewer | Review aller Swarm-Diffs (Dogfood) |
| 9 | DevOps (Sandbox) | (neu, eingeschränkt) | ci/ nur in Kopie; NIE deploy.sh/ops |
| 10 | Technical Writer | docs-lookup | docs/, CHANGELOG_PENDING.md |
| 11 | UX/Designer | (neu) | packages/dashboard (Indigo #6366f1, Kanji) |
| 12 | Compliance Analyst | (neu) | packages/comply, compliance/ |
| 13 | Data-Viz | performance-optimizer | packages/dashboard Charts |
| 14 | Growth/Marketer | seo-specialist | changelog/Release-Notes (proof-led, Content-Policy) |

## Säulen → Pakete (bestätigt aus dem Repo)

- 符 Code → `packages/code`   ✅ allow
- 天 Cloud → `packages/cloud`   ⛔ deny (deploy-nah)
- 心 Mind → `packages/mind`   ✅ allow
- 雷 Strike → `packages/strike`   ⛔ **hart tabu** (Enforcement)
- 鎧 Guard → `packages/guard`   ✅ allow (defensiv)
- 法 Comply → `packages/comply`   ✅ allow
- Backbone → `packages/switchblade`   ⛔ **hart tabu** (RTBH)

## Go-to-Market & Release (neu, für Firmengründung / Verkauf)

Diese Rollen liegen als fertige `.codex/agents/*.toml` im Kit-Ordner
`codex-agents/`. Outward-facing Rollen sind **DRAFT-ONLY** (siehe
`GTM-GUARDRAILS.md`) — nie autonomer Versand/Kontakt.

| Rolle | Datei | Zweck | Sicherung |
|---|---|---|---|
| release-manager | `release-manager.toml` | Weg zu 1.0: Checkliste, CHANGELOG, Gap-Analyse | kein Deploy/Tag ohne Freigabe |
| marketing-drafter | `marketing-drafter.toml` | Landingpage/One-Pager/Blog → `outputs/gtm/marketing/` | draft-only |
| sales-drafter | `sales-drafter.toml` | Pitch-Deck/Demo/FAQ → `outputs/gtm/sales/` | kein Kundenkontakt |
| investor-relations | `investor-relations.toml` | Exec Summary/Data-Room → `outputs/gtm/investor/` | kein Investorenkontakt |

## Unklare Pakete — jetzt geklärt

- `packages/nightforge` = LLM-Training-Scheduler (MLX/Ollama/Remote).
  Code bauen ✅, echte Trainings-Runs ⛔ gated.
- `packages/den` = Recon-Pillar (Fritzbox/WiFi/Frame-Scanner).
  Code bauen ✅, echter Scan ⛔ gated.

## Regel

Jede Rolle: erst `GUARDRAILS.md` lesen, `SWARM_STOP` prüfen,
`auto-company.allow/.deny` respektieren. Tabu-Pakete nur unter
`codex -p strict` (read-only). Alles Schreibende ausserhalb der
Allowlist → on-request-Approval.
