#!/usr/bin/env sh
#
# Every service reachable through Caddy must have a tile on the dashboard.
#
# Why this exists
# ---------------
# docker/proxy/Caddyfile and docker/dashboard/config/services.yaml are two lists
# of the same thing, maintained by hand, in different files. That is the exact
# shape of drift this repo keeps running into: the dashboard does not break when
# it falls behind, it just quietly stops being the complete picture — which is
# worse, because you go on trusting it.
#
# So CI compares them. Add a site block, forget the tile, and the build goes red
# while you still remember why you added it.
#
# Run by hand from the repo root:
#   ./scripts/check-dashboard.sh
#
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
CADDYFILE="$REPO/docker/proxy/Caddyfile"
SERVICES="$REPO/docker/dashboard/config/services.yaml"

# Sites that intentionally have no tile. Each needs a reason, not just a name.
#
#   mcp   an API endpoint, not a page. It answers MCP over HTTP and returns 401
#         to a browser without a bearer token, so a tile would be a link to an
#         error.
EXEMPT="mcp"

[ -f "$CADDYFILE" ] || { echo "check-dashboard: $CADDYFILE not found" >&2; exit 1; }
[ -f "$SERVICES" ]  || { echo "check-dashboard: $SERVICES not found" >&2; exit 1; }

# Subdomain of every `<name>.{$HOMELAB_DOMAIN} {` site block. The bare
# `{$HOMELAB_DOMAIN} {` block is the dashboard itself and is not matched here —
# it would only ever link to itself.
sites=$(grep -oE '^[a-z0-9-]+\.\{\$HOMELAB_DOMAIN\} \{' "$CADDYFILE" | cut -d. -f1 | sort -u)

missing=""
for site in $sites; do
  skip=""
  for e in $EXEMPT; do
    [ "$e" = "$site" ] && skip=yes
  done
  [ -z "$skip" ] || continue

  # A tile is present if something links to https://<site>.<domain>.
  if grep -q "https://${site}\.{{HOMEPAGE_VAR_DOMAIN}}" "$SERVICES"; then
    echo "ok       $site"
  else
    echo "MISSING  $site"
    missing="$missing $site"
  fi
done

# The reverse direction matters too: a tile pointing at a site block that no
# longer exists is a link to nothing, and nobody notices until they click it.
stale=""
for href in $(grep -oE 'href: https://[a-z0-9-]+\.\{\{HOMEPAGE_VAR_DOMAIN\}\}' "$SERVICES" \
              | sed 's|href: https://||' | cut -d. -f1 | sort -u); do
  if ! printf '%s\n' $sites | grep -qx "$href"; then
    echo "STALE    $href — tile links to a site with no Caddyfile block"
    stale="$stale $href"
  fi
done

# Homepage rejects any Host header it was not told to expect, so the address
# Caddy serves the dashboard at and HOMEPAGE_ALLOWED_HOSTS must be the same
# string. They live in different files, and a mismatch does not fail the
# container — it renders "Invalid Host header", which reads exactly like a proxy
# fault. This shipped broken once for precisely that reason.
COMPOSE="$REPO/docker/dashboard/docker-compose.yml"
mismatch=""
if [ -f "$COMPOSE" ]; then
  # The site block whose body reverse-proxies to the dashboard container.
  caddy_host=$(awk '
    /^[^ \t].*\{$/ { site = $1 }
    /reverse_proxy dashboard:/ { print site; exit }
  ' "$CADDYFILE")
  allowed=$(sed -n 's/^[[:space:]]*HOMEPAGE_ALLOWED_HOSTS:[[:space:]]*//p' "$COMPOSE" \
            | head -n1 | tr -d ' ')
  # Normalise: the Caddyfile writes {$VAR}, compose writes ${VAR:?required}.
  norm_caddy=$(printf '%s' "$caddy_host" | sed 's/{\$HOMELAB_DOMAIN}/D/g')
  norm_allowed=$(printf '%s' "$allowed" | sed 's/\${HOMELAB_DOMAIN[^}]*}/D/g')
  if [ "$norm_caddy" != "$norm_allowed" ]; then
    echo "MISMATCH host: Caddy serves '$caddy_host' but HOMEPAGE_ALLOWED_HOSTS is '$allowed'"
    mismatch=yes
  else
    echo "ok       host allowlist matches the Caddyfile"
  fi
fi

if [ -n "$missing" ] || [ -n "$stale" ] || [ -n "$mismatch" ]; then
  echo >&2
  [ -z "$missing" ] || echo "Add a tile to docker/dashboard/config/services.yaml for:$missing" >&2
  [ -z "$stale" ]   || echo "Remove or fix tiles pointing at:$stale" >&2
  [ -z "$mismatch" ] || echo "HOMEPAGE_ALLOWED_HOSTS must equal the site address Caddy serves the dashboard at." >&2
  echo "(If a site genuinely should have no tile, add it to EXEMPT in $0 with a reason.)" >&2
  exit 1
fi

echo "dashboard covers every proxied site"
