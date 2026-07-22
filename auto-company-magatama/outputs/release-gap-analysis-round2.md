# Magatama — Gap-Analyse Runde 2 (Code-Verifikation)

> Verifiziert am echten Code (`rene/magatama@main`) via GitLab-Suche + Trees.
> Ergänzt/korrigiert die heuristische Runde 1.

## 🟢 Goldfund für Fundraising/Verkauf: es gibt schon Traktionszahlen

`outputs/acquisition-2026-04-21/management-summary.md` existiert bereits und
widerlegt die „No Live Data"-Fassade mit **echten, betrieblichen Zahlen**
(Stand April 2026):

- **627** verifizierte Fixes (davon **518** MagatamaLLM, 96 Claude, 13 Codex)
- **52** Assets im Inventar, **25** überwachte Dienste, **27** aktive Exposures
- **3.669** Trainings-Artefakte, **10.752** getrackte Learning-/Fix-Versuche

Das ist genau das Proof-Material, das Investoren/Käufer sehen wollen — der
„compounding remediation engine"-Loop ist belegt, nicht behauptet. **Diese
Zahlen aktualisieren und ins Pitch/Data-Room heben** ist der schnellste
Wert-Hebel.

## Verifizierte P1-Gaps (Runde-1-Vermutungen bestätigt)

| Feature | Runde-1 | Runde-2 Befund | Status |
|---|---|---|---|
| C02 Opengrep-Engine | Gap? | Nur in CONCEPT/FEATURE-TABLE, **nicht im Code** | ⛔ Gap |
| I01 CSPM (AWS/Azure/GCP) | Gap? | Kein Provider-Code; „aws/azure/gcp" nur als Secret-Pattern & SSRF-Metadata | ⛔ Gap |
| I03 Security-Graph (toxic comb.) | Gap? | Nur Doku/Marketing-HTML, **kein Graph-Code** | ⛔ Gap |
| C18 Git-PR-Integration | Gap? | **Gitea real** (`core/src/routes/git.ts` + `.gitea/workflows/magatama-scan.yml`); GitHub/GitLab PR-Kommentare fehlen | 🟡 Teil |
| I02 CIS-Benchmarks | ? | `packages/cloud/src/l11-compliance/frameworks.ts` vorhanden | 🟢 vorhanden |

## Wichtige Korrektur: was „天 Cloud" wirklich ist

Das Cloud-Paket ist **kein Multi-Cloud-CSPM**, sondern eine **vollständige
SIEM/SOAR-artige Telemetrie-Pipeline** L0→L13:

```
l0-ingest (syslog/nginx/kern/auth-Sensoren) -> l1-normalize (geoip, ip-intel)
-> l2-detect -> l3-correlate -> l4-classify -> l5-score -> l6-decide
-> l7-enforce -> l8-heal -> l9-report -> l11-compliance -> l12-hardening
-> l13-threat-scanner
```

Das ist **stark und real** (host-/log-basierte Detection + Enforcement +
Self-Heal) — aber es ist etwas **anderes** als die FEATURE-TABLE-Zeile „CSPM
für AWS/Azure/GCP". Zwei ehrliche Optionen fürs 1.0-Narrativ:
1. **Repositionieren:** „Cloud" als host-/workload-Telemetrie-SOAR
   verkaufen (das ist es), CSPM als Roadmap markieren. **(empfohlen, ehrlich)**
2. **Bauen:** echte Cloud-Provider-Anbindung (AWS/Azure/GCP-API) nachrüsten
   — größerer Aufwand, für Wiz-Parity.

## UI-Umfang geklärt (`.tsx`-Vorbehalt aus Runde 1 aufgelöst)

`packages/dashboard` = **große, handgebaute statische HTML-Dashboards**
(`public/index.html` >2000 Zeilen, `site-v2.html`), nur 4 `.ts`/4 `.html` —
**keine** komponentenbasierte React/Next-App. `packages/web` = 5 `.ts`, keine
UI. Funktional vorhanden, aber:
- **Wartbarkeit/Skalierung:** Ein 2000-Zeilen-HTML ist Tech-Debt. Für 1.0 ok
  zu lassen, für Enterprise-DD ein Thema.
- Der Frontend-Builder-Squad sollte hier **nicht blind refactoren**, sondern
  gezielt Module extrahieren (nur nach Freigabe).

## Aktualisierte 1.0-Prioritäten (nach Runde 2)

1. **Traktions-Proof heben** (acquisition-summary aktualisieren → Data-Room).
   Höchster ROI, reine Aufbereitung, kein Code-Risiko.
2. **Testabdeckung** (weiter Blocker #1 aus Runde 1, unverändert).
3. **Cloud-Narrativ ehrlich schärfen** (repositionieren statt CSPM behaupten).
4. **Git-PR-Integration** auf GitHub/GitLab erweitern (Gitea-Basis existiert).
5. Opengrep / Security-Graph: als **Roadmap** markieren, nicht als „vorhanden"
   verkaufen (DD-Haftungsrisiko bei Falschbehauptung).

## Ehrlichkeits-Hinweis

Verifikation per Code-Suche/Trees, weiterhin ohne Laufzeit-Test. „Vorhanden"
= Code existiert, nicht = fehlerfrei/produktionsreif. Ob z. B. `l7-enforce`
oder die Scanner real durchlaufen, zeigt erst ein Testlauf auf einer Kopie.
