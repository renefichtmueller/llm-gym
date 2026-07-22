# Magatama — Release-Gap-Analyse (Runde 1)

> Read-only Erst-Analyse aus `rene/magatama@main` (GitLab). Heuristik auf
> Basis von Datei-/Code-Footprint + FEATURE-TABLE.md + CHANGELOG. **Kein**
> Laufzeit-Test, kein `.tsx` gezählt (nur `.ts`) — UI-Pakete daher unten
> mit Vorbehalt. Zweck: 1.0-Punch-List priorisieren, nicht abschliessend
> bewerten.

## Executive Summary

Magatama ist **deutlich weiter als die öffentliche „Live v0.0.0 / No Live
Data"-Fassade** suggeriert: Die Changelog belegt einen **live betriebenen
8-Pillar-Stack auf Host „Erik" mit funktionierendem Self-Heal**. Die Tiefe
ist real — `mind` allein hat 136 TS-Dateien (12-Layer-LLM-Pipeline,
MCP-Guard, Immune-Learning, 7-Phasen-Healing).

**Der kritische Release-Blocker ist nicht fehlende Funktion, sondern
fehlende Absicherung & Paketierung:** nahezu **keine automatisierten
Tests**, und ein öffentliches Auftreten, das das Produkt unter Wert zeigt.
Genau das sind die zwei Dinge, die eine technische Due Diligence zuerst
prüft.

## Implementierungstiefe pro Paket (Ist)

| Paket (Säule) | TS-Dateien | Tests | Einschätzung |
|---|---:|---:|---|
| `mind` 心 | 136 | 0 | Kronjuwel, sehr reif — aber **ungetestet** |
| `core` | 96 | 6 | Breit, minimale Tests |
| `cloud` 天 | 75 | 0 | Sensors/Feeds/Bridges da; CSPM (AWS/Azure/GCP) unklar |
| `guard` 鎧 | 24 | 0 | ungetestet |
| `comply` 法 | 24 | 0 | ungetestet |
| `code` 符 | 22 | 0 | fast alle P1-Scanner vorhanden, ungetestet |
| `admin` | 13 | 0 | |
| `strike` 雷 | 10 | 0 | recon/exploit/validate/fix/dast da |
| `cli` | 9 | 0 | |
| `infra-health` | 8 | 0 | |
| `web`/`dashboard` | 5/4 | 0 | **Vorbehalt: `.tsx` nicht gezählt** — UI-Umfang separat prüfen |
| `fine-tuner` | 0 | 0 | evtl. anderes Sprach-Target |

## P1-Abdeckung pro Säule (Feature-Table vs. Code)

**符 CODE** — starke Abdeckung: SAST, SCA, Secrets, IaC, Container, SBOM,
Malware, Supply-Chain, AI-Code, Reachability, AutoFix alle als Scanner
vorhanden. **Gaps:** C02 Opengrep-Engine (explizit?), C18 Git-Integration
(Gitea/GitHub/GitLab PR-Kommentare), C19 CI/CD-Pipeline-Integration.

**心 MIND** — sehr hohe Abdeckung: Detection-Rules, Behavioral (KillChain,
ConversationTracker), MCP-Guard (11 Module), Learning, 7-Phasen-Healing,
Sanitization, Validation. **Gap:** M02 „Sub-30ms" ist ein **Benchmark-
Nachweis**, kein Feature — für Investoren belegbar messen.

**天 CLOUD** — Threat-Feeds, L0-Sensors, GeoIP/IP-Intel, Deception,
Erik/SSH-Bridge, Health. **Gaps:** I01 CSPM (echte AWS/Azure/GCP-Anbindung),
I02 CIS-Benchmarks, I03 Security-Graph („toxic combinations") — die
großen Wiz-Parity-Features.

**雷 STRIKE** — recon/exploit/validate/fix/dast vorhanden (S01–S04).
**Gaps:** S05 Cross-Pillar-Kill-Chain, S06/S07 continuous/scheduled (ggf.
in `core/scheduler`).

**鎧 GUARD / 法 COMPLY** — vorhanden, aber dünn (je 24 Dateien, 0 Tests).
Comply ist fürs Investoren-Narrativ wichtig (15 Frameworks) → verifizieren.

## Release-Blocker (querschnittlich, Priorität hoch → niedrig)

1. **Testabdeckung ~0.** Reiche Logik, kaum Tests. Für 1.0 **und** DD der
   größte Hebel. → QA/tdd-Squad: Kern-Pfade jeder Säule mit Tests + CI-Gate.
2. **Öffentliches Auftreten unter Wert.** „v0.0.0 / Entries 0 / No Live
   Data" lässt ein reifes Produkt wie ein leeres wirken. → Release-Manager +
   Marketing-Drafter: echte (bereinigte) Live-Zahlen, Changelog befüllen,
   Versionsmarke setzen.
3. **CI/CD härten.** `.gitlab-ci.yml` vorhanden — Test-/Build-/Lint-Gates
   verbindlich machen, Deploy bleibt manuell/gated.
4. **P1-Parity-Lücken schließen** (CSPM, CIS, Security-Graph, Opengrep,
   Git-PR-Integration) — die Features, die Wettbewerber (Wiz/Aikido/Snyk)
   schon haben und die im Verkaufsgespräch abgefragt werden.
5. **Deploy-Fragilität.** Changelog zeigt wiederkehrende Deploy/Env-Drift-
   Vorfälle (Pillar-Liste, ecosystem.config.cjs vs. Live). Für 1.0:
   reproduzierbarer, idempotenter Deploy + Config-as-Code.

## Für Firmengründung / Investoren (aus Runde 1 ableitbar)

- **Stärke im Pitch:** 心 MIND (12-Layer-LLM-/Agent-Security, MCP-Guard) ist
  ein echter, seltener Differentiator (FEATURE-TABLE markiert vieles als
  „FIRST TO MARKET / UNIQUE"). Das ist die Kernstory.
- **DD-Risiko #1:** fehlende Tests + „No Live Data"-Fassade. Vor jedem
  Investoren-Kontakt schließen.
- **DD-Risiko #2:** Security-Produkt, das intern viel echte Infra berührt
  (Erik, Fritzbox, Credentials in Changelog-Historie). Sauberer, bereinigter
  Data-Room nötig — keine internen IPs/Secrets nach aussen.

## Vorgeschlagene 1.0-Punch-List (erste Iteration für den Swarm)

1. QA-Squad: Test-Harness + Kern-Tests je Säule (Ziel: P1-Pfade grün, CI-Gate).
2. Release-Manager: FEATURE-TABLE ↔ Ist als Ticket-Backlog; DoD je Säule.
3. Cloud-Squad: CSPM/CIS/Security-Graph als P1-Parity.
4. Code-Squad: Git-PR-Integration + Opengrep-Engine bestätigen/ergänzen.
5. Frontend: Dashboard „No Live Data" → echte bereinigte Live-Ansicht.
6. Marketing/Investor (draft-only): One-Pager + Exec Summary mit MIND als
   Kernstory, proof-led, ohne interne Details.

## Nächster Schritt

Diese Analyse ist **heuristisch** (Dateipräsenz, kein Laufzeittest). Runde 2
verifiziert die P1-Gaps am Code (z. B. „ist CSPM echt an AWS angebunden oder
Stub?") — dafür sollte der Swarm auf einer erreichbaren Kopie laufen.
