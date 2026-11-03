#!/usr/bin/env sh
#
# Start the Fleet board sync (brignano/ai-tools, workflow fleet-board) so new
# issues and PRs reach the board within minutes. Run from cron on the Docker
# LXC every ten minutes:
#
#   */10 * * * * /root/homelab/scripts/fleet-board-kick.sh >> /var/log/fleet-board-kick.log 2>&1
#
# Why this exists
# ---------------
# fleet-board is scheduled every 15 minutes, but GitHub runs scheduled
# workflows when it has spare capacity: on 2026-11-01..03 it ran every 4 to 10
# hours, so a new issue waited that long to appear on the board. A
# workflow_dispatch call starts a run straight away, so this box asks for one.
# ai-tools is public, so the runs cost nothing.
#
# Setup (once, on CT 100)
# -----------------------
# A fine-grained token, repository access brignano/ai-tools only, permission
# "Actions: read and write" and nothing else, in a root-only file:
#
#   mkdir -p /root/.config/homelab
#   echo 'FLEET_DISPATCH_TOKEN=github_pat_...' > /root/.config/homelab/fleet-board.env
#   chmod 600 /root/.config/homelab/fleet-board.env
#
# Until that file exists every run is a no-op and says so as
# homelab_fleet_kick_configured 0.
#
set -u

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
# shellcheck source=scripts/metrics.sh
. "$REPO/scripts/metrics.sh"

ENV_FILE="${HL_FLEET_ENV:-/root/.config/homelab/fleet-board.env}"
URL="${HL_FLEET_DISPATCH_URL:-https://api.github.com/repos/brignano/ai-tools/actions/workflows/fleet-board.yml/dispatches}"

configured=0
ok=0

finish() {
  metrics_open fleet_board_kick
  metric_help homelab_fleet_kick_configured gauge \
    "1 once fleet-board.env exists and the Fleet board sync is kicked from here"
  metric homelab_fleet_kick_configured "$configured"
  metric_help homelab_fleet_kick_success gauge \
    "1 if the last fleet-board-kick.sh run started the workflow"
  metric homelab_fleet_kick_success "$ok"
  metrics_close
}
trap finish EXIT

if [ ! -r "$ENV_FILE" ]; then
  exit 0
fi
configured=1

# Read the one value rather than sourcing the file: it is data, not a script.
TOKEN=$(sed -n 's/^FLEET_DISPATCH_TOKEN=//p' "$ENV_FILE" | head -n1 | tr -d '"'"'"' \r')
if [ -z "$TOKEN" ]; then
  echo "$(date -Is) fleet-board-kick: FLEET_DISPATCH_TOKEN missing from $ENV_FILE" >&2
  exit 1
fi

# 204 No Content is success; anything else says why on stderr for the log.
out=$(mktemp)
code=$(curl -sS -o "$out" -w '%{http_code}' --max-time 30 \
  -X POST "$URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  -d '{"ref":"main","inputs":{"apply":true}}') || code=000
body=$(head -c 300 "$out" 2>/dev/null); rm -f "$out"
if [ "$code" = "204" ]; then
  ok=1
  exit 0
fi
echo "$(date -Is) fleet-board-kick: HTTP $code $body" >&2
exit 1
