#!/usr/bin/env bash
# Telegram Bot API wrapper that keeps the bot token secret.
# Reads TELEGRAM_BOT_TOKEN from .env at runtime; the token is embedded in the
# request URL inside this subprocess only, never on the agent's command line and
# never printed. Only the API JSON response is printed (it does not contain the
# token).
#
# Usage:
#   tools/telegram.sh getMe                                  # validate bot token
#   tools/telegram.sh getUpdates                             # find your chat id (Phase 6 step 6)
#   tools/telegram.sh sendMessage 'chat_id=123&text=hello'   # form-encoded data

set -euo pipefail
ENV_FILE="${ENV_FILE:-.env}"
TG_METHOD="${1:?usage: telegram.sh METHOD [form-data]}"
DATA="${2:-}"

TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | head -1 \
  | sed 's/^TELEGRAM_BOT_TOKEN=//; s/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//')"
if [ -z "$TOKEN" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN not found or empty in $ENV_FILE" >&2
  exit 1
fi

URL="https://api.telegram.org/bot${TOKEN}/${TG_METHOD}"
if [ -n "$DATA" ]; then
  curl -s -X POST "$URL" --data "$DATA"
else
  curl -s "$URL"
fi
