#!/usr/bin/env bash
# Magatama "alles auf 0" — READ-ONLY Diagnose fuer Host "Erik".
# Fuehrt nur SELECT count / Status-Checks aus. Aendert NICHTS.
# Nutzung:  bash erik-diagnose.sh
set -uo pipefail
APP="${MAGATAMA_DIR:-/opt/magatama}"
[ -f "$APP/.env" ] && set -a && . "$APP/.env" 2>/dev/null && set +a

DBH="${DB_HOST:-localhost}"; DBP="${DB_PORT:-5432}"
DBU="${DB_USER:-magatama}"; DBN="${DB_NAME:-magatama}"
psqlq(){ PGPASSWORD="${DB_PASSWORD:-}" psql -h "$DBH" -p "$DBP" -U "$DBU" -d "$DBN" -tAc "$1" 2>&1; }

echo "==================== 1) POSTGRES ERREICHBAR? ===================="
if psqlq "SELECT 1;" | grep -q '^1$'; then echo "  OK: DB $DBU@$DBH:$DBP/$DBN erreichbar"
else echo "  !! DB NICHT erreichbar -> genau das erzeugt die 0en (overview.ts .catch->rows:[])"; fi

echo "==================== 2) SIND DIE DATEN NOCH DA? ================="
echo "  fix_artifacts gesamt : $(psqlq "SELECT count(*) FROM fix_artifacts;")"
echo "  findings gesamt      : $(psqlq "SELECT count(*) FROM findings;")"
echo "  -- verifizierte Fixes pro Resolver --"
psqlq "SELECT COALESCE(resolver,'(null)'), count(*) FROM fix_artifacts GROUP BY 1 ORDER BY 2 DESC;" | sed 's/^/     /'
echo "  >> Kommen hier ~500+ / 627 -> DATEN SICHER, es ist Verbindung/Config."
echo "  >> Steht hier 0 -> Tabelle leer: pruefe ob Backend auf die RICHTIGE DB zeigt (Schritt 4)."

echo "==================== 3) DIENSTE (PM2 / DB / LLM) ==============="
command -v pm2 >/dev/null && pm2 list | sed 's/^/  /' || echo "  (pm2 nicht im PATH)"
echo "  Postgres-Prozess: $(pgrep -x postgres >/dev/null && echo laeuft || echo NICHT)"
OLH="${OLLAMA_HOST:-http://localhost:11434}"
echo "  Ollama ($OLH): $(curl -s --max-time 5 "$OLH/api/tags" >/dev/null && echo OK || echo 'offline -> erklaert LLM offline')"
echo "  LLM-Gateway :3103: $(curl -s --max-time 5 http://localhost:3103/health >/dev/null && echo OK || echo 'keine Antwort')"

echo "==================== 4) DEPLOY-/ENV-DRIFT (bekanntes Muster) ===="
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q origin 2>/dev/null
  LOCAL=$(git -C "$APP" rev-parse --short HEAD 2>/dev/null)
  REMOTE=$(git -C "$APP" rev-parse --short origin/main 2>/dev/null)
  echo "  checkout HEAD=$LOCAL  origin/main=$REMOTE  $([ "$LOCAL" = "$REMOTE" ] && echo '(current)' || echo '!! STALE — genau die Drift-Fehlerklasse')"
fi
echo "  MAGATAMA_PILLARS (env laufender Prozess): ${MAGATAMA_PILLARS:-<nicht gesetzt>}"
grep -n "MAGATAMA_PILLARS" "$APP/ecosystem.config.cjs" 2>/dev/null | sed 's/^/     ecosystem: /'
echo "  DB-Ziel laut env: host=$DBH db=$DBN  (gegen kanonisch vergleichen!)"

echo "==================== 5) SELF-HEAL / ALERTS ====================="
[ -f "$APP/.selfheal-state.json" ] && { echo "  .selfheal-state.json:"; cat "$APP/.selfheal-state.json" | sed 's/^/     /'; } || echo "  (kein selfheal-state)"
echo "  >> Die 31 Notifications im Dashboard zuerst lesen — sie beschreiben den Vorfall vermutlich."

echo "==================== FAZIT-LOGIK =============================="
echo "  DB ok + fix_artifacts>0  -> Daten sicher; UI-0 = Cache/Backend-Neustart noetig (pm2 restart core)."
echo "  DB ok + fix_artifacts=0  -> falsche/leere DB: Backend zeigt auf frische DB (Env/Deploy-Drift)."
echo "  DB nicht erreichbar      -> Postgres/Netzwerk: DB-Dienst + DB_* env pruefen."
