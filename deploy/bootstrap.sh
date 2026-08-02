#!/usr/bin/env bash
#
# bootstrap.sh — one-shot, idempotent deploy of the Weblo pipeline onto the
# always-free GCP e2-micro (Debian 12). Implements doc 07 steps 3-8.
#
# Run ON the VM, as a sudo-capable user, from the repo you copied over:
#     sudo bash /opt/pipeline/deploy/bootstrap.sh
#
# Safe to re-run: every step checks-then-acts. Secrets are read from
# $APP_DIR/.env on the VM and never printed. The Cloudflare tunnel is created
# via the API using CLOUDFLARE_API_TOKEN (no interactive `cloudflared login`).
#
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/pipeline}"
SVC_USER="${SVC_USER:-weblo}"
ENV_FILE="$APP_DIR/.env"
TUNNEL_NAME="samples"
HOSTNAME_FQDN="samples.webloapp.com"
SAMPLES_PORT_DEFAULT="8788"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo (need root for apt/useradd/systemd)"
[ -f "$ENV_FILE" ] || die "$ENV_FILE not found. Copy the repo + .env to $APP_DIR first (see DEPLOY.md)."

# --- read only the keys we need from .env, without printing them -----------
envget() { grep -E "^$1=" "$ENV_FILE" | head -1 | sed "s/^$1=//; s/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*\$//"; }
CF_TOKEN="$(envget CLOUDFLARE_API_TOKEN)"
CF_ACCOUNT="$(envget CLOUDFLARE_ACCOUNT_ID)"
SAMPLES_PORT="$(envget SAMPLES_PORT)"; SAMPLES_PORT="${SAMPLES_PORT:-$SAMPLES_PORT_DEFAULT}"
[ -n "$CF_TOKEN" ]   || die "CLOUDFLARE_API_TOKEN missing in .env"
[ -n "$CF_ACCOUNT" ] || die "CLOUDFLARE_ACCOUNT_ID missing in .env"

cf_api() {  # cf_api METHOD PATH [json-body]  -> prints JSON response only
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -s -X "$method" "https://api.cloudflare.com/client/v4${path}" \
      -H "Authorization: Bearer ${CF_TOKEN}" -H "Content-Type: application/json" --data "$body"
  else
    curl -s -X "$method" "https://api.cloudflare.com/client/v4${path}" \
      -H "Authorization: Bearer ${CF_TOKEN}" -H "Content-Type: application/json"
  fi
}

# ---------------------------------------------------------------------------
say "STEP 3 — base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git sqlite3 jq curl >/dev/null

say "STEP 3 — service user + app dir"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$APP_DIR/logs" "$APP_DIR/samples_cache"
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR"
chmod 600 "$ENV_FILE"
chown "$SVC_USER:$SVC_USER" "$ENV_FILE"

say "STEP 3 — python venv + deps (no playwright)"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  sudo -u "$SVC_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$SVC_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip >/dev/null
sudo -u "$SVC_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" >/dev/null

say "STEP 4 — initialize database (WAL mode)"
sudo -u "$SVC_USER" bash -c "cd '$APP_DIR' && .venv/bin/python -c 'import db; db.init_db()'"

# ---------------------------------------------------------------------------
say "STEP 5 — install cloudflared"
if ! command -v cloudflared >/dev/null 2>&1; then
  mkdir -p /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared bookworm main' \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  apt-get install -y -qq cloudflared >/dev/null
fi

say "STEP 5 — create/find the '$TUNNEL_NAME' tunnel via API"
LIST="$(cf_api GET "/accounts/${CF_ACCOUNT}/cfd_tunnel?name=${TUNNEL_NAME}&is_deleted=false")"
[ "$(echo "$LIST" | jq -r '.success')" = "true" ] || die "CF tunnel list failed: $(echo "$LIST" | jq -c '.errors')"
TUNNEL_ID="$(echo "$LIST" | jq -r '.result[0].id // empty')"
if [ -z "$TUNNEL_ID" ]; then
  CREATE="$(cf_api POST "/accounts/${CF_ACCOUNT}/cfd_tunnel" \
    "{\"name\":\"${TUNNEL_NAME}\",\"config_src\":\"cloudflare\"}")"
  [ "$(echo "$CREATE" | jq -r '.success')" = "true" ] || die "CF tunnel create failed: $(echo "$CREATE" | jq -c '.errors')"
  TUNNEL_ID="$(echo "$CREATE" | jq -r '.result.id')"
  echo "created tunnel ${TUNNEL_ID}"
else
  echo "reusing existing tunnel ${TUNNEL_ID}"
fi

say "STEP 5 — set remote ingress config (-> localhost:${SAMPLES_PORT})"
CFG="$(cf_api PUT "/accounts/${CF_ACCOUNT}/cfd_tunnel/${TUNNEL_ID}/configurations" \
  "{\"config\":{\"ingress\":[{\"hostname\":\"${HOSTNAME_FQDN}\",\"service\":\"http://localhost:${SAMPLES_PORT}\"},{\"service\":\"http_status:404\"}]}}")"
[ "$(echo "$CFG" | jq -r '.success')" = "true" ] || die "CF ingress config failed: $(echo "$CFG" | jq -c '.errors')"

say "STEP 5 — route DNS ${HOSTNAME_FQDN} -> tunnel"
ZONE="$(cf_api GET "/zones?name=webloapp.com")"
ZONE_ID="$(echo "$ZONE" | jq -r '.result[0].id // empty')"
[ -n "$ZONE_ID" ] || die "could not resolve zone id for webloapp.com"
CNAME_TARGET="${TUNNEL_ID}.cfargotunnel.com"
EXIST="$(cf_api GET "/zones/${ZONE_ID}/dns_records?name=${HOSTNAME_FQDN}")"
REC_ID="$(echo "$EXIST" | jq -r '.result[0].id // empty')"
DNS_BODY="{\"type\":\"CNAME\",\"name\":\"${HOSTNAME_FQDN}\",\"content\":\"${CNAME_TARGET}\",\"proxied\":true}"
if [ -z "$REC_ID" ]; then
  R="$(cf_api POST "/zones/${ZONE_ID}/dns_records" "$DNS_BODY")"
else
  R="$(cf_api PUT "/zones/${ZONE_ID}/dns_records/${REC_ID}" "$DNS_BODY")"
fi
[ "$(echo "$R" | jq -r '.success')" = "true" ] || die "CF DNS route failed: $(echo "$R" | jq -c '.errors')"

say "STEP 5 — install cloudflared as a token-run systemd service"
# fetch the run token (kept out of logs); (re)install the service with it
TUN_TOKEN="$(cf_api GET "/accounts/${CF_ACCOUNT}/cfd_tunnel/${TUNNEL_ID}/token" | jq -r '.result')"
[ -n "$TUN_TOKEN" ] && [ "$TUN_TOKEN" != "null" ] || die "could not fetch tunnel run token"
systemctl stop cloudflared 2>/dev/null || true
cloudflared service uninstall 2>/dev/null || true
cloudflared service install "$TUN_TOKEN" >/dev/null
systemctl enable --now cloudflared >/dev/null
unset TUN_TOKEN

# ---------------------------------------------------------------------------
say "STEP 6 — the two daemon systemd services"
# NOTE: deliberately no EnvironmentFile= — config.py loads .env itself from
# WorkingDirectory, which correctly strips inline '# comments' that systemd
# would otherwise keep verbatim and corrupt (e.g. CAMPAIGN_NICHE).
cat > /etc/systemd/system/weblo-samples.service <<UNIT
[Unit]
Description=Weblo samples web app (serve_samples.py)
After=network-online.target cloudflared.service
Wants=network-online.target

[Service]
User=${SVC_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python serve_samples.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/weblo-replies.service <<UNIT
[Unit]
Description=Weblo reply watcher (watch_replies.py)
After=network-online.target
Wants=network-online.target

[Service]
User=${SVC_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python watch_replies.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now weblo-samples weblo-replies >/dev/null

say "STEP 7 — cron batch jobs (run.py job names; postcards/mail_job deferred)"
CRON_TMP="$(mktemp)"
cat > "$CRON_TMP" <<CRON
# Weblo pipeline — times UTC; per-lead 9-5 local window + cap + spacing enforced in code.
# flock -n = single-flight: skip this tick if a run is still going (a send batch
# can take ~15 min with pacing, and cron fires every 3 min). send + followups
# SHARE one lock so their sends can never overlap and together overshoot the
# daily cap. flock releases automatically when the process exits, even on crash.
#
# The find/build lines are NOT optional. With them off the funnel drains and the
# operator's phone goes quiet for a week with nothing to explain why — which is
# exactly what happened in late July. If you need to pause outreach, leave these
# running and turn off `send` instead: building costs cents, silence costs weeks.
*/3  *     * * *  cd ${APP_DIR} && flock -n /tmp/weblo-send.lock  .venv/bin/python run.py send      >> logs/cron.log 2>&1
17   *     * * *  cd ${APP_DIR} && flock -n /tmp/weblo-build.lock .venv/bin/python run.py build     >> logs/cron.log 2>&1
30   13    * * 1  cd ${APP_DIR} && flock -n /tmp/weblo-find.lock  .venv/bin/python run.py find      >> logs/cron.log 2>&1
0    14    * * *  cd ${APP_DIR} && flock -n /tmp/weblo-send.lock  .venv/bin/python run.py followups >> logs/cron.log 2>&1
# Re-judge already-paid-for SKIP leads under the current gate + find their
# emails. No Places spend, so it runs daily as a cheap funnel top-up.
45   11    * * *  cd ${APP_DIR} && flock -n /tmp/weblo-requal.lock .venv/bin/python run.py requalify 25 >> logs/cron.log 2>&1
# The daily heartbeat. 12:30 UTC = 7:30am Central, before the operator leaves for
# work. This is the message that makes a dead pipeline impossible to miss.
30   12    * * *  cd ${APP_DIR} && .venv/bin/python run.py heartbeat                                >> logs/cron.log 2>&1
CRON
crontab -u "$SVC_USER" "$CRON_TMP"
rm -f "$CRON_TMP"

say "STEP 8 — logrotate"
cat > /etc/logrotate.d/weblo <<'ROT'
/opt/pipeline/logs/*.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
ROT

# ---------------------------------------------------------------------------
say "STEP 9 — verify"
sleep 4
systemctl is-active cloudflared      >/dev/null && echo "  cloudflared:    active" || echo "  cloudflared:    NOT active"
systemctl is-active weblo-samples    >/dev/null && echo "  weblo-samples:  active" || echo "  weblo-samples:  NOT active"
systemctl is-active weblo-replies    >/dev/null && echo "  weblo-replies:  active" || echo "  weblo-replies:  NOT active"
echo "  cron jobs installed for ${SVC_USER}:"; crontab -u "$SVC_USER" -l | grep -c 'run.py' | sed 's/^/    /'

say "DONE — deploy complete."
cat <<NEXT

Next:
  - DNS/tunnel can take ~1 min to propagate. Then from anywhere:
      curl -I https://${HOSTNAME_FQDN}/healthz     # expect HTTP 200
  - Build a sample, then hit its slug URL and confirm a sample_visits row.
  - Email track: once SENDER_EMAIL/credentials.json are in place, run
      ${APP_DIR}/.venv/bin/python run.py send --dry-run
  - Logs:  journalctl -u weblo-replies -u weblo-samples -u cloudflared -f
           tail -f ${APP_DIR}/logs/cron.log
NEXT
