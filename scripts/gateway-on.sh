#!/usr/bin/env sh
#
# Undo gateway-off.sh: start cloudflared again. Needs docker/tunnel/.env (the
# tunnel token) and the gateway stack up, since cloudflared joins gateway_edge.
set -eu

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
docker compose -f "$REPO/docker/tunnel/docker-compose.yml" up -d cloudflared
echo "cloudflared started. Check: docker logs --tail 20 cloudflared (look for 'Registered tunnel connection')"
