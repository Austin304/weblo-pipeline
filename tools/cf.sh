#!/usr/bin/env bash
# Cloudflare API wrapper that keeps the token secret.
#
# Reads CLOUDFLARE_API_TOKEN from .env at runtime and uses it for the request.
# The token is NEVER passed on the command line and NEVER printed, so it does
# not enter the agent's context or the chat transcript.
#
# Usage:
#   tools/cf.sh GET  /zones?name=webloapp.com
#   tools/cf.sh GET  /zones/<zone_id>/dns_records
#   tools/cf.sh POST /zones/<zone_id>/dns_records '{"type":"TXT","name":"x","content":"y"}'
#
# Only the API JSON response is printed (responses never contain the token).

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
METHOD="${1:?usage: cf.sh METHOD PATH [json-body]}"
API_PATH="${2:?usage: cf.sh METHOD PATH [json-body]}"
BODY="${3:-}"

# Extract the token: strip the key=, any inline # comment, and surrounding space.
TOKEN="$(grep -E '^CLOUDFLARE_API_TOKEN=' "$ENV_FILE" \
  | head -1 \
  | sed 's/^CLOUDFLARE_API_TOKEN=//; s/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//')"

if [ -z "$TOKEN" ]; then
  echo "ERROR: CLOUDFLARE_API_TOKEN not found or empty in $ENV_FILE" >&2
  exit 1
fi

BASE="https://api.cloudflare.com/client/v4"

if [ -n "$BODY" ]; then
  curl -s -X "$METHOD" "${BASE}${API_PATH}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$BODY"
else
  curl -s -X "$METHOD" "${BASE}${API_PATH}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json"
fi
