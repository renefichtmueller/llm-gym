#!/usr/bin/env bash
#
# cf_phone_tunnel.sh — one-shot setup for phone→laptop terminal over a Cloudflare
# Tunnel gated by Cloudflare Access, so there is NO token in the URL.
#
# Runs ON YOUR MAC (which can reach api.cloudflare.com). It:
#   1. verifies your Cloudflare API token and finds account/zone/team
#   2. creates (or reuses) a named tunnel + gets its connector token
#   3. points term.<domain> at the tunnel (DNS CNAME) and sets the ingress
#   4. creates (or reuses) a self-hosted Access app + an "allow your email" policy
#   5. starts cloudflared (background) and the llm-gym terminal, token-free
# Re-running is safe — every step is create-or-reuse.
#
# Usage:
#   CF_API_TOKEN=xxxxx ./scripts/cf_phone_tunnel.sh
#
# Token scopes needed (Cloudflare dashboard → My Profile → API Tokens → Custom):
#   Account · Cloudflare Tunnel · Edit
#   Account · Access: Apps and Policies · Edit
#   Account · Access: Organizations, Identity Providers, and Groups · Read
#   Zone    · DNS · Edit           (zone: <domain>)
#   Zone    · Zone · Read          (zone: <domain>)
#
# Overridable via environment:
#   DOMAIN (default fichtmueller.org)  SUBDOMAIN (term)  EMAIL (rf@flexoptix.net)
#   PORT (7681)  TUNNEL_NAME (phone-term)  REPO (~/llm-gym)  START (1; 0 = setup only)
set -euo pipefail

DOMAIN="${DOMAIN:-fichtmueller.org}"
SUBDOMAIN="${SUBDOMAIN:-term}"
EMAIL="${EMAIL:-rf@flexoptix.net}"
PORT="${PORT:-7681}"
TUNNEL_NAME="${TUNNEL_NAME:-phone-term}"
REPO="${REPO:-$HOME/llm-gym}"
START="${START:-1}"
HOSTNAME_FULL="${SUBDOMAIN}.${DOMAIN}"
API="https://api.cloudflare.com/client/v4"

die() { echo "❌ $*" >&2; exit 1; }
say() { echo "→ $*"; }
[ -n "${CF_API_TOKEN:-}" ] || die "CF_API_TOKEN ist nicht gesetzt. Aufruf: CF_API_TOKEN=xxx $0"
command -v curl >/dev/null || die "curl fehlt."
command -v python3 >/dev/null || die "python3 fehlt (einmal 'xcode-select --install')."

# --- tiny helpers ------------------------------------------------------------
capi() {  # capi METHOD PATH [JSON_BODY] -> raw response on stdout
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" "$API$path" \
      -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
      --data "$body"
  else
    curl -sS -X "$method" "$API$path" -H "Authorization: Bearer $CF_API_TOKEN"
  fi
}
# jq-free extraction: pipe JSON in, pass a python expression on `d` (the parsed body)
pick() { python3 -c 'import sys,json;d=json.load(sys.stdin);print(eval(sys.argv[1]))' "$1"; }
ok() {  # validate a response saved in $1 (context in $2); die with CF error message
  echo "$1" | python3 -c 'import sys,json
d=json.load(sys.stdin)
if not d.get("success"):
    msg="; ".join(str(e.get("message")) for e in d.get("errors",[])) or "unbekannter Fehler"
    sys.exit("CF-API: "+msg)' || die "$2 fehlgeschlagen."
}

# --- 1. token + account + zone + team ---------------------------------------
say "Token prüfen…"
r="$(capi GET /user/tokens/verify)"; ok "$r" "Token-Prüfung"
say "Zone $DOMAIN suchen…"
r="$(capi GET "/zones?name=$DOMAIN")"; ok "$r" "Zone-Abfrage"
ZONE_ID="$(echo "$r" | pick 'd["result"][0]["id"] if d["result"] else ""')"
ACCOUNT_ID="$(echo "$r" | pick 'd["result"][0]["account"]["id"] if d["result"] else ""')"
[ -n "$ZONE_ID" ] || die "Domain $DOMAIN liegt nicht (oder nicht sichtbar für diesen Token) auf Cloudflare."
say "Account $ACCOUNT_ID, Zone $ZONE_ID"

say "Team (Access-Org) ermitteln…"
r="$(capi GET "/accounts/$ACCOUNT_ID/access/organizations")"; ok "$r" "Access-Org-Abfrage"
AUTH_DOMAIN="$(echo "$r" | pick 'd["result"]["auth_domain"]')"
TEAM="${AUTH_DOMAIN%.cloudflareaccess.com}"
say "Team = $TEAM"

# --- 2. tunnel + connector token --------------------------------------------
say "Tunnel '$TUNNEL_NAME' anlegen/finden…"
r="$(capi GET "/accounts/$ACCOUNT_ID/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false")"; ok "$r" "Tunnel-Liste"
TUNNEL_ID="$(echo "$r" | pick 'd["result"][0]["id"] if d["result"] else ""')"
if [ -z "$TUNNEL_ID" ]; then
  r="$(capi POST "/accounts/$ACCOUNT_ID/cfd_tunnel" \
        "{\"name\":\"$TUNNEL_NAME\",\"config_src\":\"cloudflare\"}")"; ok "$r" "Tunnel-Anlage"
  TUNNEL_ID="$(echo "$r" | pick 'd["result"]["id"]')"
fi
say "Tunnel $TUNNEL_ID"
r="$(capi GET "/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/token")"; ok "$r" "Tunnel-Token"
TUNNEL_TOKEN="$(echo "$r" | pick 'd["result"]')"

# --- 3. ingress config + DNS ------------------------------------------------
say "Ingress setzen ($HOSTNAME_FULL → http://localhost:$PORT)…"
r="$(capi PUT "/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" \
      "{\"config\":{\"ingress\":[{\"hostname\":\"$HOSTNAME_FULL\",\"service\":\"http://localhost:$PORT\"},{\"service\":\"http_status:404\"}]}}")"
ok "$r" "Ingress-Konfiguration"

say "DNS $HOSTNAME_FULL → Tunnel…"
CNAME_CONTENT="$TUNNEL_ID.cfargotunnel.com"
r="$(capi GET "/zones/$ZONE_ID/dns_records?type=CNAME&name=$HOSTNAME_FULL")"; ok "$r" "DNS-Abfrage"
REC_ID="$(echo "$r" | pick 'd["result"][0]["id"] if d["result"] else ""')"
DNS_BODY="{\"type\":\"CNAME\",\"name\":\"$HOSTNAME_FULL\",\"content\":\"$CNAME_CONTENT\",\"proxied\":true}"
if [ -z "$REC_ID" ]; then
  r="$(capi POST "/zones/$ZONE_ID/dns_records" "$DNS_BODY")"; ok "$r" "DNS-Anlage"
else
  r="$(capi PUT "/zones/$ZONE_ID/dns_records/$REC_ID" "$DNS_BODY")"; ok "$r" "DNS-Update"
fi

# --- 4. Access app + allow-email policy -------------------------------------
say "Access-App für $HOSTNAME_FULL anlegen/finden…"
r="$(capi GET "/accounts/$ACCOUNT_ID/access/apps?per_page=100")"; ok "$r" "Access-App-Liste"
APP_ID="$(echo "$r" | pick "next((a['id'] for a in d['result'] if a.get('domain')=='$HOSTNAME_FULL'), '')")"
if [ -z "$APP_ID" ]; then
  r="$(capi POST "/accounts/$ACCOUNT_ID/access/apps" \
        "{\"name\":\"Phone Terminal\",\"type\":\"self_hosted\",\"domain\":\"$HOSTNAME_FULL\",\"session_duration\":\"24h\"}")"
  ok "$r" "Access-App-Anlage"
fi
# re-read to get id + aud reliably
r="$(capi GET "/accounts/$ACCOUNT_ID/access/apps?per_page=100")"; ok "$r" "Access-App-Reload"
APP_ID="$(echo "$r" | pick "next((a['id'] for a in d['result'] if a.get('domain')=='$HOSTNAME_FULL'), '')")"
AUD="$(echo "$r" | pick "next((a['aud'] for a in d['result'] if a.get('domain')=='$HOSTNAME_FULL'), '')")"
[ -n "$APP_ID" ] && [ -n "$AUD" ] || die "Access-App/AUD nicht gefunden."

say "Allow-Policy für $EMAIL sicherstellen…"
r="$(capi GET "/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies")"; ok "$r" "Policy-Liste"
HAVE_POLICY="$(echo "$r" | pick 'len(d["result"])>0')"
if [ "$HAVE_POLICY" != "True" ]; then
  r="$(capi POST "/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies" \
        "{\"name\":\"Allow $EMAIL\",\"decision\":\"allow\",\"include\":[{\"email\":{\"email\":\"$EMAIL\"}}]}")"
  ok "$r" "Policy-Anlage"
fi

echo
echo "✅ Cloudflare fertig:"
echo "   URL   : https://$HOSTNAME_FULL"
echo "   Team  : $TEAM"
echo "   AUD   : $AUD"
echo

# --- 5. start cloudflared + the terminal ------------------------------------
if [ "$START" != "1" ]; then
  echo "Setup-only (START=0). Starte selbst mit:"
  echo "  cloudflared tunnel run --token $TUNNEL_TOKEN &"
  echo "  cd $REPO && ./run.sh terminal --host 127.0.0.1 --domain $HOSTNAME_FULL \\"
  echo "      --access-team $TEAM --access-aud $AUD --access-only"
  exit 0
fi

if ! command -v cloudflared >/dev/null; then
  if command -v brew >/dev/null; then say "cloudflared installieren…"; brew install cloudflared;
  else die "cloudflared fehlt und Homebrew ist nicht da. Installiere cloudflared, dann START=1 erneut."; fi
fi

say "cloudflared starten (Hintergrund, Log: ~/cloudflared.log)…"
nohup cloudflared tunnel run --token "$TUNNEL_TOKEN" > "$HOME/cloudflared.log" 2>&1 &
sleep 2

[ -x "$REPO/run.sh" ] || die "Repo nicht unter $REPO gefunden. Mit REPO=/pfad erneut aufrufen."
say "Terminal starten (token-frei, via Cloudflare Access)…"
echo "   → gleich erscheint der Banner; auf dem Handy https://$HOSTNAME_FULL öffnen."
echo "   → dauerhaft im Hintergrund später: sudo cloudflared service install $TUNNEL_TOKEN"
echo
cd "$REPO"
exec ./run.sh terminal --host 127.0.0.1 --domain "$HOSTNAME_FULL" \
  --access-team "$TEAM" --access-aud "$AUD" --access-only
