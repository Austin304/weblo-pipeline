#!/usr/bin/env bash
# Report which .env keys are FILLED vs BLANK — WITHOUT printing any values.
# Lets the agent audit configuration without secrets entering its context.
#
# Usage: tools/env-status.sh

set -euo pipefail
ENV_FILE="${ENV_FILE:-.env}"

while IFS= read -r line; do
  # skip blanks and full-line comments
  case "$line" in
    ''|\#*) continue ;;
  esac
  # only KEY=VALUE lines
  case "$line" in
    *=*) ;;
    *) continue ;;
  esac
  key="${line%%=*}"
  val="${line#*=}"
  # strip inline comment + surrounding whitespace from the value
  val="$(printf '%s' "$val" | sed 's/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//')"
  if [ -z "$val" ]; then
    printf '%-28s BLANK\n' "$key"
  else
    printf '%-28s FILLED (%d chars)\n' "$key" "${#val}"
  fi
done < "$ENV_FILE"
