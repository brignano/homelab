#!/usr/bin/env sh
#
# What the blackbox probes are doing *right now*, on the box, as opposed to what
# the repo says they should be doing.
#
# Run on CT 100, from the repo root:
#   ./scripts/probe-status.sh
#
# Why this exists
# ---------------
# Twice now a probe has been paging `#alerts` about a service that was healthy,
# and both times the hard part was not the fix — it was answering "is the fix
# actually running?". The Caddyfile and prometheus.yml are bind-mounted, so a
# `git pull` changes the files on disk while the running containers keep serving
# the old config until Caddy is restarted and Prometheus reloaded. The repo then
# looks correct, CI is green, and Discord keeps firing, which sends you back to
# re-diagnose a bug you already fixed.
#
# So this asks the running system three questions, in the order that tells them
# apart:
#
#   1. Is Prometheus scraping the targets this checkout declares? If not, it has
#      not reloaded and nothing else here matters — that is the whole bug.
#   2. What does each target answer when probed *now*? A 404 or a 0 says the
#      probe is asking the wrong question; a 200 with probe_success 0 says
#      something subtler (timeout, redirect, TLS).
#   3. How often has each probe changed state in the last 6h? A stuck alert and a
#      flapping one produce a similar amount of Discord traffic and want
#      completely different fixes.
#
# scripts/check-probes.sh is the static sibling of this and runs in CI: it reads
# the two files and proves they agree. This one needs the stack up and proves
# the stack agrees with the files.
#
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
PROM_FILE="$REPO/docker/monitoring/prometheus/prometheus.yml"
PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
BLACKBOX="${BLACKBOX_CONTAINER:-blackbox-exporter}"

[ -f "$PROM_FILE" ] || { echo "probe-status: $PROM_FILE not found" >&2; exit 1; }

# The blackbox job's targets, in file order. Stops at the next job so the
# relabel_configs below the list — and every other job — stay out of it.
targets=$(awk '
  /^  - job_name: blackbox$/ { in_job = 1; next }
  in_job && /^  - job_name:/ { exit }
  in_job && $1 == "-" && $2 ~ /^https?:\/\// { print $2 }
' "$PROM_FILE")

[ -n "$targets" ] || { echo "probe-status: no blackbox targets found in $PROM_FILE" >&2; exit 1; }

# ── 1. Is the running Prometheus serving this checkout's config? ─────────────
echo "== Prometheus target list ($PROM_URL) =="
stale=0
if ! live=$(curl -fsS -m 10 "$PROM_URL/api/v1/targets" 2>/dev/null); then
  echo "  UNREACHABLE  $PROM_URL — is the monitoring stack up?"
  echo
  live=""
else
  for t in $targets; do
    # A literal substring match against the API response. Prometheus echoes the
    # target verbatim in the scrape's labels, and Go does not escape slashes in
    # JSON, so this needs no parser and cannot half-match a different URL.
    if printf '%s' "$live" | grep -qF "\"$t\""; then
      echo "  scraping     $t"
    else
      echo "  NOT SCRAPED  $t"
      stale=1
    fi
  done
  if [ "$stale" -eq 1 ]; then
    echo
    echo "  Prometheus is running an older config than this checkout."
    echo "  Reload it:  curl -X POST $PROM_URL/-/reload"
    echo "  Until then the alerts you are getting describe the old targets."
  fi
  echo
fi

# ── 2. What does each target answer when probed right now? ───────────────────
echo "== Live probe, via $BLACKBOX =="
if ! docker ps --filter "name=^${BLACKBOX}$" --filter "status=running" --format '{{.Names}}' \
     2>/dev/null | grep -qx "$BLACKBOX"; then
  echo "  container '$BLACKBOX' is not running — skipping live probes"
else
  for t in $targets; do
    # /probe returns Prometheus text format, so no JSON parsing is needed. On a
    # failure blackbox still emits the metrics, with the status code as 0 when
    # it never got a response at all.
    out=$(docker exec "$BLACKBOX" \
            wget -qO- "http://localhost:9115/probe?target=${t}&module=http_2xx" \
          2>/dev/null || true)

    if [ -z "$out" ]; then
      printf '  %-10s %s (exporter returned nothing)\n' "ERROR" "$t"
      continue
    fi

    ok=$(printf '%s\n' "$out"   | awk '$1 == "probe_success" { print $2 }')
    code=$(printf '%s\n' "$out" | awk '$1 == "probe_http_status_code" { print $2 }')

    case "$ok" in
      1) printf '  %-10s %s (HTTP %s)\n' "up" "$t" "${code%%.*}" ;;
      *) printf '  %-10s %s (HTTP %s)\n' "DOWN" "$t" "${code%%.*}" ;;
    esac
  done
fi
echo

# ── 3. Stuck, or flapping? ───────────────────────────────────────────────────
# Same amount of Discord noise, opposite fixes: a stuck probe means the probe is
# wrong, a flapping one means the service is. `changes()` tells them apart.
echo "== State changes per probe, last 6h =="
if [ -z "$live" ]; then
  echo "  skipped — Prometheus unreachable"
else
  resp=$(curl -fsS -m 10 -G "$PROM_URL/api/v1/query" \
           --data-urlencode 'query=changes(probe_success[6h])' 2>/dev/null || true)
  # Shallow parse, on purpose: split the result array so each series is on its
  # own line, then pull two fields out of each. The response shape here is fixed
  # and flat enough that a JSON dependency would cost more than it buys.
  # Whitespace-tolerant because the split is what keeps one series' instance
  # from being paired with another's value, and that must not hinge on
  # Prometheus emitting compact JSON.
  parsed=$(printf '%s' "$resp" \
    | sed 's/}[[:space:]]*,[[:space:]]*{[[:space:]]*"metric"/\n{"metric"/g' \
    | awk '
        /"instance"[[:space:]]*:[[:space:]]*"/ {
          inst = $0
          sub(/.*"instance"[[:space:]]*:[[:space:]]*"/, "", inst); sub(/".*/, "", inst)
          val = $0
          sub(/.*"value"[[:space:]]*:[[:space:]]*\[[^,]*,[[:space:]]*"/, "", val)
          sub(/".*/, "", val)
          printf "  %-4s changes  %s\n", val, inst
        }')
  if [ -n "$parsed" ]; then
    printf '%s\n' "$parsed"
    echo
    echo "  0 changes + DOWN above = stuck: the probe is asking the wrong question."
    echo "  Many changes           = flapping: the service really is bouncing."
  else
    echo "  no probe_success data returned"
  fi
fi
