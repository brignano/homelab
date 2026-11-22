#!/usr/bin/env sh
#
# Report every request the secure gateway refused. Run from cron on the Docker
# LXC (scripts/install-cron.sh installs it):
#
#   */5 * * * * /root/homelab/scripts/gateway-watch.sh >> /var/log/gateway-watch.log 2>&1
#
# Why this exists
# ---------------
# The gateway (docker/gateway/) is lock 3 of four. Cloudflare Access (lock 1)
# and cloudflared (lock 2) check the same token first, so in normal operation
# the gateway never refuses anything: a refusal here means a request got
# further than it should have, or a route is wrong. Either way you want to know
# now, not in tomorrow's digest. See docs/design/tsd-secure-gateway.md,
# "Seeing attacks".
#
# What it does
# ------------
# Reads `docker logs` of `gateway` (Caddy's JSON access log) and
# `gateway-verifier` (one line per token check) since its last run, joins them
# on the request id, and posts one message to #alerts listing what was refused
# and why. It also publishes homelab_gateway_requests_24h (allowed / refused /
# error), homelab_gateway_routes_live and homelab_gateway_tunnel_up for the
# digest and Grafana. Pings HEALTHCHECKS_GATEWAY_WATCH_URL every run.
#
# No-op (exit 0, homelab_gateway_configured 0) until docker/gateway/.env exists.
#
set -eu

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"

# shellcheck source=scripts/metrics.sh
. "$REPO/scripts/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$REPO/scripts/healthchecks.sh"
# shellcheck source=scripts/deploy-common.sh
. "$REPO/scripts/deploy-common.sh"
# shellcheck source=scripts/gateway-common.sh
. "$REPO/scripts/gateway-common.sh"

if docker ps --filter "name=^cloudflared$" --filter status=running --format '{{.Names}}' 2>/dev/null | grep -qx cloudflared; then
  tunnel=1
else
  tunnel=0
fi

gw_run gateway-watch watch HEALTHCHECKS_GATEWAY_WATCH_URL "Gateway refused requests" --tunnel-up "$tunnel"
