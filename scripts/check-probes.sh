#!/usr/bin/env sh
#
# Every blackbox probe aimed at Caddy must hit a path Caddy answers 200.
#
# Why this exists
# ---------------
# blackbox-exporter reaches Caddy over the Docker network, so it sends the
# container name as the Host header. Caddy routes by Host, and `caddy` matches
# none of the real service names — so a probe of `http://caddy/` got a 404, and
# blackbox's http_2xx module scores anything but a 2xx as down. The result was a
# "Service endpoint unreachable" alert firing at Discord around the clock while
# the proxy was healthy the entire time. It cost more trust than it cost uptime:
# an alert that is always wrong teaches you to ignore the channel.
#
# The two halves of the fix live in different files — the probe target in
# prometheus.yml, the site block that answers it in the Caddyfile — which is the
# same shape of drift check-dashboard.sh exists for. So check it the same way:
# delete the health block, or move the probe to another path, and CI goes red
# instead of Discord.
#
# Run by hand from the repo root:
#   ./scripts/check-probes.sh
#
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
PROM="$REPO/docker/monitoring/prometheus/prometheus.yml"
CADDYFILE="$REPO/docker/proxy/Caddyfile"

[ -f "$PROM" ]      || { echo "check-probes: $PROM not found" >&2; exit 1; }
[ -f "$CADDYFILE" ] || { echo "check-probes: $CADDYFILE not found" >&2; exit 1; }

# Probe targets pointing at the caddy container, e.g. `http://caddy/health` or
# `http://caddy:80/health`. Anything else in the blackbox job is somebody else's
# service and its own health endpoint's problem.
targets=$(grep -oE '^ *- +http://caddy(:80)?(/[^ ]*)?$' "$PROM" \
          | sed 's|^ *- *||' | sort -u)

if [ -z "$targets" ]; then
  echo "ok       no blackbox probe targets caddy"
  exit 0
fi

# The site block Caddy serves those requests from. Without it every probe below
# is a 404 no matter which path it asks for.
if ! grep -qE '^http://caddy(:80)? \{' "$CADDYFILE"; then
  echo "MISSING  no 'http://caddy' site block in the Caddyfile" >&2
  echo >&2
  echo "blackbox probes caddy by container name, so Caddy needs a site block" >&2
  echo "matching Host 'caddy' or every probe below is a 404:" >&2
  printf '  %s\n' $targets >&2
  exit 1
fi

bad=""
for t in $targets; do
  # Strip scheme, host and optional port; a bare target means path "/".
  path=$(printf '%s' "$t" | sed 's|^http://caddy\(:80\)\?||')
  [ -n "$path" ] || path="/"

  # A `handle <path>` whose body responds 200 is what makes the probe pass.
  # `handle` blocks are mutually exclusive and matched in written order, so
  # reading them in order is reading exactly what Caddy will do.
  if awk -v want="$path" '
        $0 ~ /^http:\/\/caddy(:80)? \{/ { in_site = 1; next }
        in_site && /^\}/               { exit }
        in_site && $1 == "handle" && $2 == want { in_handle = 1; next }
        in_handle && /^\t\}/          { in_handle = 0 }
        in_handle && $1 == "respond" && $NF == "200" { found = 1 }
        END { exit !found }
      ' "$CADDYFILE"; then
    echo "ok       $t"
  else
    echo "NO 200   $t — Caddyfile has no 'handle $path' responding 200"
    bad="$bad $t"
  fi
done

if [ -n "$bad" ]; then
  echo >&2
  echo "These probes would score the proxy as down while it is healthy." >&2
  echo "Either serve the path 200 in $CADDYFILE, or point the probe at one" >&2
  echo "that already is." >&2
  exit 1
fi

echo "every caddy probe hits a path Caddy answers 200"
