#!/usr/bin/env bash
# Erzeugt eine ISOLIERTE Test-Kopie von Magatama fuer den Auto-Company-Swarm.
# Strippt Secrets, stubbt scharfe Konnektoren, haengt Guardrails + Kill-Switch ein.
#
# Nutzung:  ./setup-copy.sh <quelle-mirror-dir> <ziel-sandbox-dir>
set -euo pipefail

SRC="${1:?Quelle (Magatama-Mirror) fehlt}"
DST="${2:?Ziel (Sandbox-Verzeichnis) fehlt}"
KIT="$(cd "$(dirname "$0")" && pwd)"

echo ">> Kopiere $SRC -> $DST (ohne .git-History)"
mkdir -p "$DST"
rsync -a --exclude '.git' "$SRC"/ "$DST"/

cd "$DST"

echo ">> Entferne Secrets / Prod-Konfiguration"
find . -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' \
  -o -name '*.key' -o -name '*credential*' -o -name '*.tunnel' \
  -o -name 'wrangler.toml' \) -print -delete || true

echo ">> Stubbe scharfe Konnektoren (Platzhalter, die nichts tun)"
for mod in enforcement switchblade peercortex runpod; do
  if [ -d "$mod" ]; then
    rm -rf "$mod"
    mkdir -p "$mod"
    printf '# STUB: im Sandbox deaktiviert. Kein echter %s-Zugriff.\n' "$mod" > "$mod/README.stub"
  fi
done
mkdir -p mocks
echo '{"note":"mock findings/telemetry data goes here"}' > mocks/sample-data.json

echo ">> Haenge Guardrails + Rollen-Kit ein"
cp "$KIT/GUARDRAILS.md" ./GUARDRAILS.md
cp "$KIT/auto-company.allow" "$KIT/auto-company.deny" ./
mkdir -p .claude/agents
cp "$KIT/agents/roster.md" .claude/agents/roster.md
# Guardrails vorn an PROMPT.md / CLAUDE.md haengen, falls vorhanden
for f in PROMPT.md CLAUDE.md; do
  if [ -f "$f" ]; then
    { echo "<!-- Auto-Company Sandbox-Guardrails -->"; cat GUARDRAILS.md; echo; cat "$f"; } > "$f.new"
    mv "$f.new" "$f"
    echo "   -> Guardrails in $f eingehaengt"
  fi
done

echo ">> Installiere Kill-Switch (leer = laeuft)"
: > SWARM_STOP

echo ">> Fertig. Sandbox liegt in: $DST"
echo "   Start:  make -f $KIT/Makefile.sandbox swarm-sandbox"
echo "   Stop :  echo STOP > $DST/SWARM_STOP"
