# Auto-Company × Magatama — Sandbox-Test-Kit

Ein **Drop-in-Kit**, um Auto-Company (Codex-Pfad, ohne Token-Limit) als
**gated Build-Beschleuniger** gegen eine **isolierte Kopie** von Magatama
laufen zu lassen — ohne jeden Zugriff auf Produktion, Host „Erik",
Enforcement oder echte Netz-Backbones.

> Leitprinzip: **Der Swarm baut das Produkt, er betreibt es nicht.**
> MagatamaLLM bleibt zur Laufzeit der einzige proof-first Resolver.
> Auto-Company arbeitet nur am Quellcode der Kopie.

## Warum „nur Kopie"

Magatama ist im Kern selbst schon ein „Auto-Company für Security":
autonome `Scan → Fix → Train`-Schleife, eigenes Resolver-Modell,
`Proof Before Memory`. Auto-Companys **ungatete** Schleife in die
**Runtime** zu setzen würde genau diese Beweiskette aushebeln — und
Magatamas eigener Supply-Chain/MCP-IoC-Scan (Patient Zero) würde einen
solchen Swarm zu Recht als Risiko flaggen. Deshalb: nur DEV, nur Kopie,
gated.

## Ist-Zustand im Repo (aus `main` verifiziert)

Magatama hat **bereits** eine gatete Multi-Agent-Harness — dieses Kit
baut darauf auf, statt ein zweites Framework danebenzustellen:

- `.codex/config.toml`: `multi_agent = true`, `approval_policy =
  "on-request"`, `strict`-Profil mit `sandbox_mode = "read-only"`,
  `max_depth = 1`
- `.claude/agents/` mit 18 Rollen (architect, security-reviewer,
  planner, tdd-guide …) + `.claude/rules`
- `AGENTS.md`: evidence-/proof-first ist Vorgabe

**Konsequenz:** Aus Auto-Company wird nur die **Breite an Builder-Rollen**
geerntet (parallel pro Paket), eingehängt in die vorhandene
`on-request`-Harness. Kein Import der ungateten Schleife.

## Inhalt des Kits

| Datei | Zweck |
|---|---|
| `setup-copy.sh` | Erzeugt die isolierte Kopie: strippt Secrets, stubbt Konnektoren, installiert Kill-Switch |
| `GUARDRAILS.md` | Block für `PROMPT.md` / `CLAUDE.md` der Kopie — Allow/Deny + Approval-Gates |
| `auto-company.allow` / `.deny` | Pfad-Allowlist/Denylist für den Swarm |
| `agents/roster.md` | Die 14 Rollen → echte Pakete, gemappt auf vorhandene `.claude/.codex`-Rollen |
| `codex-sandbox-profile.toml` | Ergänzung für `.codex/config.toml`: `swarm-sandbox`-Profil + Builder-Rollen |
| `Makefile.sandbox` | `make -f Makefile.sandbox swarm-sandbox` startet den gateten Lauf |

## Schnellstart

```bash
# 1. Isolierte Kopie erzeugen (aus einem Mirror des Originals):
./setup-copy.sh /pfad/zum/magatama-mirror  ../magatama-sandbox

# 2. In die Kopie wechseln und Kit-Regeln sind bereits eingehängt:
cd ../magatama-sandbox

# 3. Gateten Swarm-Lauf starten:
make -f Makefile.sandbox swarm-sandbox
```

## Autonomie-Grad

Default ist **gated**: Der Swarm baut autonom, aber `git push`, Deploy und
jede Netzwerk-Egress-Aktion brauchen menschliche Freigabe (siehe
`GUARDRAILS.md`). Zum reinen Trockentest lässt sich in `Makefile.sandbox`
`AUTONOMY=full` setzen — läuft dann ungated, aber **strikt** in der
Kopie ohne Infra-Env-Vars. Nie mit `full` gegen etwas fahren, das echte
Credentials sehen kann.

## Kill-Switch

`setup-copy.sh` legt in der Kopie eine Datei `SWARM_STOP` an (leer =
läuft, vorhanden mit Inhalt `STOP` = Schleife hält beim nächsten Zyklus).
