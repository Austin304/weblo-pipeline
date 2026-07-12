#!/usr/bin/env bash
# Lob API wrapper that keeps the key secret.
# Picks LOB_API_KEY_TEST or LOB_API_KEY_LIVE based on LOB_MODE in .env, uses it
# as the HTTP Basic username, and never prints it. Only the API JSON response is
# printed. Defaults to test mode so nothing real mails by accident.
#
# Usage:
#   tools/lob.sh GET  /v1/addresses?limit=1        # cheap auth check
#   tools/lob.sh POST /v1/postcards '<form-data>'

set -euo pipefail
ENV_FILE="${ENV_FILE:-.env}"
METHOD="${1:?usage: lob.sh METHOD PATH [data]}"
API_PATH="${2:?usage: lob.sh METHOD PATH [data]}"
DATA="${3:-}"

getval() { grep -E "^$1=" "$ENV_FILE" | head -1 \
  | sed "s/^$1=//; s/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//"; }

MODE="$(getval LOB_MODE)"; MODE="${MODE:-test}"
if [ "$MODE" = "live" ]; then KEY="$(getval LOB_API_KEY_LIVE)"; else KEY="$(getval LOB_API_KEY_TEST)"; fi
if [ -z "$KEY" ]; then
  echo "ERROR: Lob key for mode '$MODE' not found or empty in $ENV_FILE" >&2
  exit 1
fi

BASE="https://api.lob.com"
if [ -n "$DATA" ]; then
  curl -s -u "${KEY}:" -X "$METHOD" "${BASE}${API_PATH}" --data "$DATA"
else
  curl -s -u "${KEY}:" -X "$METHOD" "${BASE}${API_PATH}"
fi
