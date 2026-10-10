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
# The second half covers probes that reach Caddy under another Host on purpose.
# blackbox-ha-caddy sends `Host: ha.caddy` so it lands in the `ha.` site block
# (a second address of that block, because committed config carries no domain).
# That only proves the route if the alias stays in the SAME address list as the
# real name: split it into its own block and the probe tests a copy of the
# route, green while the real one 502s. So the alias line is checked verbatim.
#
# Run by hand from the repo root:
#   ./scripts/check-probes.sh
#
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
PROM="$REPO/docker/monitoring/prometheus/prometheus.yml"
CADDYFILE="$REPO/docker/proxy/Caddyfile"
BLACKBOX="$REPO/docker/monitoring/blackbox/blackbox.yml"

[ -f "$PROM" ]      || { echo "check-probes: $PROM not found" >&2; exit 1; }
[ -f "$CADDYFILE" ] || { echo "check-probes: $CADDYFILE not found" >&2; exit 1; }
[ -f "$BLACKBOX" ]  || { echo "check-probes: $BLACKBOX not found" >&2; exit 1; }

# ── Host aliases ─────────────────────────────────────────────────────────────
# Every `Host: <name>.caddy` a blackbox module sends must be the second address
# of the `<name>.{$HOMELAB_DOMAIN}` block, on that block's own line.
alias_bad=""
for host in $(grep -oE '^ +Host: +[a-z0-9-]+\.caddy *$' "$BLACKBOX" | awk '{ print $2 }' | sort -u); do
  want="${host%.caddy}.{\$HOMELAB_DOMAIN}, http://$host {"
  if grep -qxF "$want" "$CADDYFILE"; then
    echo "ok       Host $host is an address of the ${host%.caddy}. site block"
  else
    echo "NO SITE  Host $host — Caddyfile has no line '$want'"
    alias_bad="$alias_bad $host"
  fi
done

if [ -n "$alias_bad" ]; then
  echo >&2
  echo "A probe sends a Host that is not an alias of the real site block, so it" >&2
  echo "either gets no answer or tests a copy of the route. Keep the alias in" >&2
  echo "the same address list as the real name in $CADDYFILE." >&2
  exit 1
fi

# ── Probes that arrive as Host `caddy` ───────────────────────────────────────
# Probe targets pointing at the caddy container, e.g. `http://caddy/health` or
# `http://caddy:80/health`, in jobs on the plain http_2xx module. A job whose
# module sets its own Host is the alias case above, not this one. Anything else
# in the blackbox job is somebody else's service and its own health endpoint's
# problem.
targets=$(awk '
  function flush() { if (mod == "http_2xx") printf "%s", buf; buf = ""; mod = "" }
  /^  - job_name:/ { flush(); next }
  /^ +module: *\[/ { m = $0; sub(/.*\[ */, "", m); sub(/ *\].*/, "", m); mod = m; next }
  /^ *- +http:\/\/caddy(:80)?(\/[^ ]*)?$/ { t = $0; sub(/^ *- */, "", t); buf = buf t "\n" }
  END { flush() }
' "$PROM" | sort -u)

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
