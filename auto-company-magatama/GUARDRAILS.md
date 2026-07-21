# Guardrails — in `PROMPT.md` / `CLAUDE.md` der Kopie einhängen

Diesen Block an den Anfang der Agent-Instruktionen der **Kopie** setzen.
Er gilt für alle 14 Rollen.

---

## Grundregeln (nicht verhandelbar)

1. **Nur DEV, nie OPS.** Du baust den Quellcode der Kopie. Du betreibst
   Magatama nicht, deployst nicht und triggerst keine Enforcement-Aktion.
2. **Proof-first bleibt.** Zur Laufzeit ist MagatamaLLM der einzige
   Resolver. Du erzeugst keine „Fixes", die in die echte Beweiskette /
   ins Training fließen.
3. **Kopie ist isoliert.** Es gibt keine echten Secrets, keinen Zugriff
   auf Host „Erik", keinen Cloudflare-Tunnel. Wenn du eine echte
   Credential oder eine Prod-Adresse siehst, ist das ein Fehler → STOP
   und melden, nicht benutzen.

## Harte Tabu-Zone (Denylist — niemals editieren/ausführen)

```
enforcement/**            # Block / Watch / Rollback / Exposure-Reaktion
switchblade/**            # Rack / NMS / RTBH
peercortex/**             # BGP / RPKI / ASPA
deploy/**                 # jede Deploy-Pipeline
**/wrangler.toml  **/*.tunnel   # Cloudflare
**/secrets/**  **/.env*  **/*credential*  **/*token*
selfpentest/targets/**    # ausser localhost-Stubs
runpod/**                 # Trainings-Compute mit Echtdaten
```

## Erlaubte Zone (Allowlist)

```
dashboard/**  ui/**  web/**        # Frontend-Module
findings/**  api/**  intel/**      # gegen MOCK-Daten
llm_gym/**  seeds/**  training/pairs/**
tests/**  ci/**  docs/**  changelog/**
compliance/**  mocks/**
```

## Approval-Gates (Mensch bestätigt, Default = gated)

Diese Aktionen NUR nach ausdrücklicher menschlicher Freigabe:
- `git push` (jeder Remote)
- jeder Deploy / Release
- jede Netzwerk-Egress-Aktion (HTTP, DNS, Paket-Install aus dem Netz)
- jedes Schreiben ausserhalb der Allowlist

## Kill-Switch

Vor jedem Zyklus prüfen: existiert `SWARM_STOP` mit Inhalt `STOP`,
sofort anhalten und Kontrolle zurückgeben.

## Selbst-Review (Dogfood)

Jeder Diff der Squad läuft durch die Rolle **Security Reviewer**
(defensiv): SAST, Dependency-Check, keine Secrets im Commit. Das ist
Magatamas eigenes Prinzip auf den Swarm selbst angewandt.
