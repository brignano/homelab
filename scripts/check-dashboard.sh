#!/usr/bin/env sh
#
# Every service reachable through Caddy must have a tile on the dashboard, and
# every local file the dashboard's config names must exist and be mounted.
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

# Everything the dashboard serves out of its own directories: the tab icon
# named in settings.yaml, and every file config/custom.css imports. Each one is
# three files agreeing — the config names a path under /<dir>, the compose file
# mounts that directory at /app/public/<dir>, and the file is in the repo. Break
# any one and Homepage does not break with it: a missing icon silently becomes
# its default logo, and a missing stylesheet silently becomes its default theme.
# Both look like nothing changed rather than like something is wrong.
CONFIG_DIR="$REPO/docker/dashboard/config"
CUSTOM_CSS="$CONFIG_DIR/custom.css"
CUSTOM_JS="$CONFIG_DIR/custom.js"
asset_err=""

check_public_ref() {
  ref=$1
  source=$2
  # Anything that is not an absolute path is Homepage's own to resolve: a full
  # URL, or a name from its bundled icon set.
  case "$ref" in
    /*) ;;
    *) echo "ok       $ref is not a local file ($source)"; return 0 ;;
  esac

  dir=$(printf '%s' "${ref#/}" | cut -d/ -f1)
  rest=$(printf '%s' "${ref#/}" | cut -d/ -f2-)

  if [ ! -f "$REPO/docker/dashboard/$dir/$rest" ]; then
    echo "MISSING  $source references $ref, which is not in docker/dashboard/$dir"
    asset_err=yes
  elif ! grep -q "\./$dir:/app/public/$dir" "$COMPOSE" 2>/dev/null; then
    echo "MISSING  $ref cannot be served — docker-compose.yml does not mount ./$dir at /app/public/$dir"
    asset_err=yes
  else
    echo "ok       $ref ($source)"
  fi
}

# Every absolute path the YAML names: the page's `favicon:`, and each tile's
# `icon:`. A bare `grafana.png` is not one of these — that is Homepage's own
# CDN lookup, which is exactly what ./scripts/update-tile-icons.sh replaces.
for f in "$CONFIG_DIR"/*.yaml; do
  [ -f "$f" ] || continue
  for ref in $(grep -oE '(icon|favicon):[[:space:]]+/[A-Za-z0-9._/-]+' "$f" | sed 's|.*:[[:space:]]*||' | sort -u); do
    check_public_ref "$ref" "$(basename "$f")"
  done
done

if [ -f "$CUSTOM_CSS" ]; then
  for ref in $(grep -oE 'url\("[^"]+"\)' "$CUSTOM_CSS" | sed 's/^url("//; s/")$//'); do
    check_public_ref "$ref" "custom.css"
  done
fi

# custom.js names the icon files Homepage will not declare on its own, and the
# Caddyfile rewrites the root paths Safari probes onto the same directory. Both
# are one edit away from pointing at a file nobody generated — and a favicon
# that 404s is not an error anywhere, it is a browser quietly drawing a globe.
if [ -f "$CUSTOM_JS" ]; then
  for ref in $(grep -oE '"/(icons|assets)/[A-Za-z0-9._/-]+"' "$CUSTOM_JS" | tr -d '"' | sort -u); do
    check_public_ref "$ref" "custom.js"
  done
fi

for ref in $(grep -oE '^[[:space:]]*rewrite[[:space:]]+\S+[[:space:]]+/(icons|assets)/\S+' "$CADDYFILE" \
             | awk '{print $NF}' | sort -u); do
  check_public_ref "$ref" "Caddyfile"
done

if [ -n "$missing" ] || [ -n "$stale" ] || [ -n "$mismatch" ] || [ -n "$asset_err" ]; then
  echo >&2
  [ -z "$missing" ] || echo "Add a tile to docker/dashboard/config/services.yaml for:$missing" >&2
  [ -z "$stale" ]   || echo "Remove or fix tiles pointing at:$stale" >&2
  [ -z "$mismatch" ] || echo "HOMEPAGE_ALLOWED_HOSTS must equal the site address Caddy serves the dashboard at." >&2
  [ -z "$asset_err" ] || echo "Every /<dir>/... path in settings.yaml or custom.css must exist under docker/dashboard/<dir>, and that directory must be mounted at /app/public/<dir>." >&2
  echo "(If a site genuinely should have no tile, add it to EXEMPT in $0 with a reason.)" >&2
  exit 1
fi

echo "dashboard covers every proxied site"
