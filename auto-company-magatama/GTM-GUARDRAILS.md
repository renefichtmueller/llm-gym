# Go-to-Market Guardrails (Marketing / Sales / Investor Relations)

Ziel: Magatama auf **Release-Niveau** bringen und **Kapitalgeber / Käufer**
ansprechen. Diese Rollen sind **outward-facing** und damit das höchste
Risiko im ganzen Swarm. Deshalb gilt kompromisslos:

## Eiserne Regel: DRAFT-ONLY

Marketing-, Sales- und Investor-Agenten **erzeugen ausschliesslich
Entwürfe** in `outputs/gtm/`. Sie dürfen **NIE**:

- E-Mails/Nachrichten senden (kein SMTP, kein Gmail/Slack/LinkedIn-API)
- Posts veröffentlichen (kein Social-Publishing)
- Investoren / Kunden / Presse **direkt kontaktieren**
- Preise, Verträge, Zahlen **verbindlich** zusagen
- Live-Webseiten deployen (`site/` nur als Entwurf bauen, Deploy = Mensch)

Jeder Versand, jede Veröffentlichung, jeder Erstkontakt läuft über den
**Menschen** (dich). Der Agent legt den Entwurf ab, du prüfst und sendest.

## Wahrheit & Proof (aus AGENTS.md abgeleitet)

- **Keine erfundenen Zahlen.** Metriken, Benchmarks, Kundenlogos,
  Testergebnisse nur, wenn sie durch echte Artefakte im Repo belegt sind
  (`proof-led`, wie im AGENTS.md). Pre-1.0 heisst: keine „Enterprise-ready"-
  oder Umsatz-Claims.
- **Keine vertraulichen Details nach aussen.** Es gilt Magatamas eigene
  Changelog-Content-Policy: keine internen IPs, Secrets, Pfade, Host-Namen
  („Erik"), Kundendaten. Das Pitch-Material ist bereinigt.
- **Security-Sensibilität:** Magatama ist ein Security-Produkt. Kein
  GTM-Text darf reale Schwachstellen/Exploits eurer eigenen oder fremder
  Infrastruktur offenlegen.

## Ablageorte

- `outputs/gtm/marketing/` – Landingpage-Copy, One-Pager, Blog-Entwürfe
- `outputs/gtm/sales/`     – Pitch-Deck-Outline, Demo-Skript, FAQ, Preis-Hypothesen (intern)
- `outputs/gtm/investor/`  – Executive Summary, Data-Room-Struktur, Fundraising-Narrativ

Alles hier ist **Entwurf zur menschlichen Freigabe**, kein Versand.
