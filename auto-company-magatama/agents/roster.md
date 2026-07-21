# Die 14 Auto-Company-Rollen → Magatama-Module

Rollen-Vorlage. Pro Rolle eine Datei unter `.claude/agents/` (Claude Code)
bzw. das Codex-Äquivalent erzeugen. Jede Rolle erbt die `GUARDRAILS.md`.
Die reale Rollenliste des Auto-Company-Repos hat Vorrang — hier ist die
auf Magatama gemappte Soll-Zerlegung.

| # | Rolle | Baut (erlaubt) | Tabu |
|---|-------|----------------|------|
| 1 | Orchestrator (consensus keeper) | Backlog planen, `consensus.md` pflegen, Squads bilden | Code an Infra |
| 2 | Product Manager | Tickets/Specs aus der Modul-Map, SLA/EPSS-Priorisierung | — |
| 3 | Architect | Modulgrenzen, Refactors (non-infra), API-Verträge | Enforcement-Kern |
| 4 | Frontend Engineer | Overview, Security Atlas (Maps), Attack-Path-Graph, Intelligence-Charts | Live-Enforce-Buttons |
| 5 | Backend Engineer | Findings-Pipeline, API, SSE-Feed — **gegen Mock-Daten** | Prod-DB, Host „Erik" |
| 6 | Data/ML Engineer | LLM-Gym-Seeds, Trainingspaare, EPSS/NVD-Anreicherung | RunPod-Runs mit Echtdaten |
| 7 | QA / Test Engineer | Testsuite über die 51 hinaus, CI-Gates | — |
| 8 | Security Reviewer (defensiv) | Review der Swarm-Diffs, SAST, Dependency-Checks | Self-Pentest gg. echte Infra |
| 9 | DevOps (Sandbox) | CI-Config, `docker-compose` **nur für die Kopie** | Prod-Deploy, CF-Tunnel |
| 10 | Technical Writer | Modul-Docs, CHANGELOG-Automatik | — |
| 11 | UX / Designer | CI-Style (Indigo `#6366f1`, Kanji), Accessibility | — |
| 12 | Compliance Analyst | 15-Framework-Mappings, Audit-Texte | — |
| 13 | Data-Viz Engineer | Score-Trend, EPSS-Verteilung, Top-CVEs nach EPSS | — |
| 14 | Growth / Marketer | Public-Changelog/Release-Notes nach Content-Policy (keine IPs/Secrets/Pfade) | — |

## Mapping auf die sechs Säulen

Der Swarm arbeitet quer, aber Findings/Tickets bleiben in Magatamas
Säulen-Struktur verortet:

- 符 **Code** → Rollen 3,4,5,7,8
- 天 **Cloud** → Rollen 5,9 (nur Sandbox-Infra)
- 心 **Mind** → Rollen 6 (LLM Gym / Training-Seeds)
- 雷 **Strike** → **read-only** für den Swarm (Enforcement ist Tabu)
- 鎧 **Guard** → Rolle 8 (defensiver Review)
- 法 **Comply** → Rolle 12

## Globale Regel

Jede Rolle liest zuerst `GUARDRAILS.md`, prüft `SWARM_STOP`, respektiert
`auto-company.allow` / `.deny`. Schreiben ausserhalb der Allowlist =
Approval-Gate.
