#!/usr/bin/env sh
#
# Keep 90 days of Cloudflare Access's authentication log on the box, and alert
# on what looks wrong in it. Run from cron on the Docker LXC
# (scripts/install-cron.sh installs it):
#
#   */5 * * * * /root/homelab/scripts/access-log-poll.sh >> /var/log/access-log-poll.log 2>&1
#
# Why this exists
# ---------------
# The Zero Trust free plan keeps Access logs for 24 hours (decided in #360:
# the box keeps its own copy rather than paying for retention). And Access is
# lock 1: the sign-ins it allows are the ones worth reading. This posts to
# #alerts when:
#
#   - someone signs in from a country or network not seen before for them
#     (the first run only learns, quietly);
#   - a service token is used from outside its expected source, for routes
#     that set GW_SOURCE_<ROUTE> (`aws` for the Alexa Lambda, or CIDRs);
#   - ACCESS_DENIED_ALERT (default 10) or more logins are refused in one run.
#
# It also counts logins for the digest, and service tokens expiring within 30
# days. Records go to /var/lib/homelab/gateway/access-log/<day>.jsonl.
#
# Credentials
# -----------
# A read-only API token ("Access: Audit Logs Read" and "Access: Service Tokens
# Read", nothing else) and the account id, in
# /root/.config/homelab/gateway-poll.env, mode 600. Not in docker/gateway/.env:
# no container needs it, so no container gets it. See docs/runbook-gateway.md.
#
# No-op (exit 0) until docker/gateway/.env exists. Pings
# HEALTHCHECKS_ACCESS_LOG_URL every run.
#
set -eu

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
POLL_ENV="${HL_GATEWAY_POLL_ENV:-/root/.config/homelab/gateway-poll.env}"

# shellcheck source=scripts/metrics.sh
. "$REPO/scripts/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$REPO/scripts/healthchecks.sh"
# shellcheck source=scripts/deploy-common.sh
. "$REPO/scripts/deploy-common.sh"
# shellcheck source=scripts/gateway-common.sh
. "$REPO/scripts/gateway-common.sh"

if [ -f "$GW_ENV" ] && [ ! -r "$POLL_ENV" ]; then
  # Configured gateway, no API token: say so every run (and fail the ping)
  # rather than look like a quiet Access log.
  echo "access-log-poll: $POLL_ENV is missing; the Access log is not being kept" >&2
fi

gw_run access-log-poll poll HEALTHCHECKS_ACCESS_LOG_URL "Cloudflare Access" --poll-env "$POLL_ENV"
