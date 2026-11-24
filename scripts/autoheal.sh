#!/usr/bin/env sh
#
# Restart containers that are running but hung: Docker marks them `unhealthy`
# (their `healthcheck:` failed retries times) and nothing else acts on it.
# `restart: unless-stopped` only helps a container that exited. Cron, every five
# minutes. Design: docs/design/tsd-self-healing-remediation.md (#252).
#
# Guardrails
#   - HL_NO_AUTOHEAL stacks (proxy, matterbridge) are never touched, and neither
#     is gluetun: restarting it alone orphans every container in its network
#     namespace (AGENTS.md). Those are only reported, as metrics.
#   - At most HL_AUTOHEAL_MAX restarts (default 3) per container in
#     HL_AUTOHEAL_WINDOW seconds (default 6h). Past that it gives up, stops
#     restarting, and `hl-autoheal-gave-up` alerts: a flapping container pages
#     instead of looping.
#   - HL_AUTOHEAL=no is report-only, the same kill switch as repo-sync.sh.
#
# Metrics (autoheal.prom): homelab_autoheal_restarts_in_window{container},
# homelab_autoheal_gave_up{container}, homelab_autoheal_last_run_timestamp_seconds.
set -eu

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"

NO_AUTOHEAL="${HL_NO_AUTOHEAL:-proxy matterbridge}"
NEVER_RESTART="${HL_AUTOHEAL_NEVER:-gluetun}"
AUTOHEAL="${HL_AUTOHEAL:-yes}"
MAX="${HL_AUTOHEAL_MAX:-3}"
WINDOW="${HL_AUTOHEAL_WINDOW:-21600}"
STATE_DIR="${HL_AUTOHEAL_STATE_DIR:-/var/lib/homelab/autoheal}"

now=$(date +%s)
mkdir -p "$STATE_DIR" 2>/dev/null || { echo "autoheal: cannot create $STATE_DIR" >&2; exit 1; }

# Drop restarts older than the window; print how many are left.
recent() { # <container>
  _f="$STATE_DIR/$1"
  [ -f "$_f" ] || { echo 0; return; }
  _cut=$((now - WINDOW))
  awk -v c="$_cut" '$1 > c' "$_f" > "$_f.new" && mv "$_f.new" "$_f"
  wc -l < "$_f" | tr -d ' '
}

in_list() { # <word> <list>
  for _w in $2; do [ "$_w" = "$1" ] && return 0; done
  return 1
}

unhealthy=$(docker ps --filter health=unhealthy --format '{{.Names}}' 2>/dev/null) || {
  echo "autoheal: docker ps failed" >&2; exit 1; }

metrics_open autoheal
metric_help homelab_autoheal_restarts_in_window gauge "Automatic restarts of this container within the window"
metric_help homelab_autoheal_gave_up gauge "1 if this container is unhealthy and autoheal has stopped restarting it"

for name in $unhealthy; do
  stack=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$name" 2>/dev/null || true)
  if in_list "$stack" "$NO_AUTOHEAL" || in_list "$name" "$NEVER_RESTART"; then
    echo "autoheal: $name is unhealthy; not restarted (excluded)"
    continue
  fi
  if [ "$AUTOHEAL" = "no" ]; then
    echo "autoheal: $name is unhealthy; HL_AUTOHEAL=no, report only"
    continue
  fi
  n=$(recent "$name")
  if [ "$n" -ge "$MAX" ]; then
    echo "autoheal: $name is still unhealthy after $n restarts; giving up"
    continue
  fi
  echo "autoheal: restarting unhealthy $name"
  if docker restart "$name" >/dev/null 2>&1; then
    echo "$now" >> "$STATE_DIR/$name"
  else
    echo "autoheal: restart of $name failed" >&2
  fi
done

# Every container with restarts still in the window gets a series, healthy or
# not, so a flapper stays visible between its restarts.
for f in "$STATE_DIR"/*; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  case "$name" in *.new) continue ;; esac
  n=$(recent "$name")
  [ "$n" -gt 0 ] || { rm -f "$f"; continue; }
  gave_up=0
  if [ "$n" -ge "$MAX" ] && printf '%s\n' "$unhealthy" | grep -qx "$name"; then gave_up=1; fi
  metric homelab_autoheal_restarts_in_window "$n" "$(metric_kv container "$name")"
  metric homelab_autoheal_gave_up "$gave_up" "$(metric_kv container "$name")"
done
metric_help homelab_autoheal_last_run_timestamp_seconds gauge "When autoheal last ran"
metric homelab_autoheal_last_run_timestamp_seconds "$now"
metrics_close
