#!/usr/bin/env sh
#
# Dead man's switch. Run from cron on the Docker LXC:
#
#   */5 * * * * /root/homelab/scripts/heartbeat.sh
#
# Why this exists
# ---------------
# Every other alerting path in this lab runs ON the machine it watches:
# Grafana evaluates the rules and fires the Discord webhook, and the assistant
# bot posts the digest — all inside CT 100. When the box goes down, both go down
# with it and nothing tells you. A monitoring system cannot report its own
# death.
#
# So this inverts the logic. The box pings OUT on a schedule and Healthchecks
# alerts when the pings STOP. Silence becomes the signal, which is the only
# shape that survives the failure it's meant to catch. It also needs no inbound
# access — no port, no tunnel, no public endpoint.
#
# What it actually checks
# -----------------------
# Not "is the network up" — that would only prove cron ran. It pings only when
# the stack that would otherwise alert you is itself alive, so this also catches
# "host is up but Docker is wedged", which Grafana obviously cannot report.
# If those containers are missing it pings /fail instead of staying silent, so
# you hear about it immediately rather than after the grace period.
#
set -eu

REPO="${HL_REPO:-/root/homelab}"
ENV_FILE="$REPO/docker/monitoring/.env"

[ -f "$ENV_FILE" ] || { echo "heartbeat: $ENV_FILE not found" >&2; exit 1; }

# Read only the key we need; avoids sourcing a file full of other secrets.
URL=$(sed -n 's/^HEALTHCHECKS_PING_URL=//p' "$ENV_FILE" | tail -n1 | tr -d '"'"'"' \r')
[ -n "$URL" ] || { echo "heartbeat: HEALTHCHECKS_PING_URL is unset in $ENV_FILE" >&2; exit 1; }

# The services whose death would otherwise go unreported.
REQUIRED="grafana prometheus"

running() {
  docker ps --filter "name=^${1}$" --filter "status=running" --format '{{.Names}}' 2>/dev/null \
    | grep -qx "$1"
}

missing=""
for svc in $REQUIRED; do
  running "$svc" || missing="$missing $svc"
done

# --retry rides out a brief network blip so a flaky uplink doesn't page you;
# -m caps the whole attempt so this can never pile up under cron.
if [ -n "$missing" ]; then
  echo "heartbeat: not running:$missing — signalling failure" >&2
  curl -fsS -m 20 --retry 3 --retry-delay 5 \
    --data-raw "monitoring stack down:$missing" "$URL/fail" >/dev/null || true
  exit 1
fi

curl -fsS -m 20 --retry 3 --retry-delay 5 "$URL" >/dev/null
