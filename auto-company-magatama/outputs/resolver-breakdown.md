# Magatama — Resolver-Aufschlüsselung (wer hat was behoben)

> Quelle: `outputs/acquisition-2026-04-21/data/resolver-stats.json` +
> `training-control.json`. Snapshot **2026-04-20**. Das Live-Dashboard zeigt
> aktuell 0 (DB-/Backend-Vorfall, siehe release-gap — Daten nicht gelöscht,
> Lese-Symptom via `overview.ts` `.catch(()=>({rows:[]}))`).

## Verifizierte Fixes nach Resolver (gesamt 627)

| Resolver | Fixes | Anteil | Audit-Artefakte | Letzte Aktivität |
|---|---:|---:|---:|---|
| MagatamaLLM (primär) | 518 | 82,6 % | 3.039 (2.749 Exposure + 290 Host-Audit) | 2026-04-20 |
| Claude (sekundär) | 96 | 15,3 % | 2 | 2026-04-20 |
| Codex | 13 | 2,1 % | 1 | 2026-04-18 |
| Manual | 0 | 0 % | 0 | — |
| Other | 0 | 0 % | 0 | — |
| **Gesamt** | **627** | 100 % | **3.042** | |

## Fix-Versuche (Learning-Chain, gesamt 10.752)

| Fix-Typ | Versuche | Erfolgsquote |
|---|---:|---:|
| LLM (echte Remediation) | 3.827 | 98,2 % |
| Deterministic (Cascade/Klassifikation) | 6.915 | 0,27 % |
| Dependency | 10 | 100 % |
| **Aggregat** | **10.752** | 35,2 % (irreführend) |

Trainings-Artefakte gesamt: **3.669**.

## Ehrliche Lesart (für Pitch/DD)

- **MagatamaLLM ist der Arbeitspferd-Resolver** (~83 % Fixes, ~99,9 % der
  Audit-Artefakte). Das ist die „compounding engine"-Story — belegbar.
- **Erfolgsquote = 98,2 % (LLM-Pfad)**, NICHT 35 %. Die 35 % nur nennen, wenn
  gleichzeitig erklärt wird, dass deterministische Cascade-Versuche per Design
  „scheitern".
- **Manual = 0** ist ein starkes Autonomie-Signal (kein Mensch musste selbst
  fixen), aber im Kontext „self-operated proof, pre-revenue" darstellen.

## Offen (Live-DB nötig)

- Gesamtzahl **evaluierter Findings** (Live-`findings`-Tabelle) — Proxy hier:
  3.042 Audit-Artefakte + 10.752 Versuche. Echte Zahl via
  `SELECT count(*) FROM findings;` / `fix_artifacts` auf Erik nach DB-Recovery.
