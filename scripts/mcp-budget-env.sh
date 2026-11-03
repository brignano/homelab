#!/usr/bin/env bash
# Writes docker/mcp-budget/.env by asking for each value. Run it on CT 100:
#   ./scripts/mcp-budget-env.sh
#
# Passwords are typed hidden and never echoed, never put on a command line and
# never written anywhere except the .env (mode 600). The MCP bearer token is
# generated here. Refuses to overwrite an existing .env unless you say so.
set -euo pipefail
umask 077

repo=$(cd "$(dirname "$0")/.." && pwd)
env_file="$repo/docker/mcp-budget/.env"

if [ -e "$env_file" ]; then
  printf '%s exists. Overwrite? [y/N] ' "$env_file"
  read -r answer
  [ "$answer" = y ] || { echo "Left it alone."; exit 0; }
fi

ask_secret() {   # ask_secret VAR "prompt" [optional]
  local var=$1 prompt=$2 optional=${3:-} value
  while :; do
    printf '%s: ' "$prompt" >&2
    IFS= read -rs value
    printf '\n' >&2
    if [ -z "$value" ] && [ -z "$optional" ]; then echo "  Cannot be empty." >&2; continue; fi
    case "$value" in *$'\n'*) echo "  A newline can't go in .env." >&2; continue ;; esac
    printf -v "$var" '%s' "$value"
    return
  done
}

echo "Where each value comes from:"
echo "  Server password   password manager, entry https://budget.home.brignano.io"
echo "  Sync ID           Actual: Settings -> Show advanced settings -> Sync ID"
echo "  Encryption key    password manager (saved when you turned on end-to-end encryption)"
echo

ask_secret ACTUAL_PASSWORD "Actual server password (hidden)"
printf 'Sync ID (not secret): ' >&2
IFS= read -r ACTUAL_BUDGET_SYNC_ID
[ -n "$ACTUAL_BUDGET_SYNC_ID" ] || { echo "Sync ID cannot be empty." >&2; exit 1; }
case "$ACTUAL_BUDGET_SYNC_ID" in *\"* | *\'* | *' '*) echo "Sync ID looks wrong." >&2; exit 1 ;; esac
ask_secret ACTUAL_BUDGET_ENCRYPTION_PASSWORD "End-to-end encryption password (hidden, Enter if you never enabled it)" optional
MCP_BUDGET_TOKEN=$(openssl rand -hex 32)

# Double quotes, so a ' in a password is fine. Inside them Compose treats \, "
# and $ specially, so each gets a backslash (checked against `docker compose config`).
dq() {
  local v=$1
  v=${v//\\/\\\\}
  v=${v//\"/\\\"}
  v=${v//\$/\\\$}
  printf '"%s"' "$v"
}

{
  echo "ACTUAL_PASSWORD=$(dq "$ACTUAL_PASSWORD")"
  echo "ACTUAL_BUDGET_SYNC_ID=$(dq "$ACTUAL_BUDGET_SYNC_ID")"
  echo "ACTUAL_BUDGET_ENCRYPTION_PASSWORD=$(dq "$ACTUAL_BUDGET_ENCRYPTION_PASSWORD")"
  echo "MCP_BUDGET_TOKEN=$(dq "$MCP_BUDGET_TOKEN")"
} > "$env_file"
chmod 600 "$env_file"

echo
echo "Wrote $env_file (mode 600)."
echo "Bearer token for MCP clients, shown once. Save it in your password manager"
echo "as https://budget-mcp.home.brignano.io:"
echo "  $MCP_BUDGET_TOKEN"
