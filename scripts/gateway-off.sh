#!/usr/bin/env sh
#
# Kill switch: take every public endpoint offline in seconds.
#
# Stops cloudflared, the only thing on this box the internet can reach, and
# nothing else: the gateway, the verifier and every upstream keep running, so
# `gateway-on.sh` is instant and nothing on the LAN or tailnet notices.
# Cloudflare answers visitors with its own "tunnel unavailable" error (1033).
#
# A stopped cloudflared stays stopped: its restart policy is unless-stopped,
# auto-deploy.sh and repo-sync.sh leave stacks that are not running alone, and
# heartbeat.sh lists cloudflared as optional. The next digest and Grafana show
# homelab_gateway_tunnel_up 0. Cloudflare's "tunnel health" notification also
# fires; that one is expected after this.
set -eu

if ! docker ps --format '{{.Names}}' | grep -qx cloudflared; then
  echo "cloudflared is not running: the gateway is already off"
  exit 0
fi
docker stop cloudflared >/dev/null
echo "cloudflared stopped: every public endpoint is offline. Undo with scripts/gateway-on.sh"
