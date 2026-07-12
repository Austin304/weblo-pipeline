#!/usr/bin/env bash
# Anthropic API wrapper that keeps the key secret.
# Reads ANTHROPIC_API_KEY from .env at runtime; never passes it on the command
# line and never prints it. Only the API JSON response is printed.
#
# Usage:
#   tools/anthropic.sh GET  /v1/models                 # cheap auth check (free)
#   tools/anthropic.sh POST /v1/messages '{...}'       # real call (costs tokens)

set -euo pipefail
ENV_FILE="${ENV_FILE:-.env}"
METHOD="${1:?usage: anthropic.sh METHOD PATH [json-body]}"
API_PATH="${2:?usage: anthropic.sh METHOD PATH [json-body]}"
BODY="${3:-}"

KEY="$(grep -E '^ANTHROPIC_API_KEY=' "$ENV_FILE" | head -1 \
  | sed 's/^ANTHROPIC_API_KEY=//; s/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//')"
if [ -z "$KEY" ]; then
  echo "ERROR: ANTHROPIC_API_KEY not found or empty in $ENV_FILE" >&2
  exit 1
fi

BASE="https://api.anthropic.com"
if [ -n "$BODY" ]; then
  curl -s -X "$METHOD" "${BASE}${API_PATH}" \
    -H "x-api-key: ${KEY}" -H "anthropic-version: 2023-06-01" \
    -H "Content-Type: application/json" --data "$BODY"
else
  curl -s -X "$METHOD" "${BASE}${API_PATH}" \
    -H "x-api-key: ${KEY}" -H "anthropic-version: 2023-06-01"
fi
