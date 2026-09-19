#!/usr/bin/env sh
#
# Dead man's switch. Run from cron on the Docker LXC:
#
#   */5 * * * * /root/homelab/scripts/heartbeat.sh >> /var/log/heartbeat.log 2>&1
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
# What it also publishes
# ----------------------
# This is the only job in the lab that runs every five minutes, which makes it
# the right place to answer two questions that were previously answerable only
# by SSHing in:
#
#   Is every container this repo declares actually running? cAdvisor reports on
#   containers that exist. It has nothing to say about one that should exist and
#   does not — which is the shape of an assistant bot that died, or a stack
#   somebody brought down to debug and never brought back. Blackbox covers seven
#   endpoints; this covers all of them, including the ones with no port to probe.
#
#   Is the scheduler itself intact? The three-week gap on 2026-09-19 was a cron
#   entry that had never been installed, and nothing anywhere could see that.
#   install-cron.sh --check answers it on demand; this answers it continuously.
#
# Both go out as textfile metrics, so they become panels and alerts rather than
# another thing to remember to run. See scripts/metrics.sh.
#
set -eu

REPO="${HL_REPO:-/root/homelab}"
ENV_FILE="$REPO/docker/monitoring/.env"

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"

[ -f "$ENV_FILE" ] || { echo "heartbeat: $ENV_FILE not found" >&2; exit 1; }

# Read only the key we need; avoids sourcing a file full of other secrets.
URL=$(sed -n 's/^HEALTHCHECKS_PING_URL=//p' "$ENV_FILE" | tail -n1 | tr -d '"'"'"' \r')
[ -n "$URL" ] || { echo "heartbeat: HEALTHCHECKS_PING_URL is unset in $ENV_FILE" >&2; exit 1; }

# The services whose death would otherwise go unreported.
REQUIRED="grafana prometheus"

# Containers this repo declares but does not expect to be running. `kali-linux`
# is scaled to zero by Sablier until someone opens kali.home — that is the
# feature, not a fault — and `cloudflared` has never been deployed. Anything
# else missing is worth an alert.
OPTIONAL_CONTAINERS="${HL_OPTIONAL_CONTAINERS:-kali-linux cloudflared}"

# Kept identical to the job list in install-cron.sh; CI fails if they drift
# (scripts/check-observability.sh). Two lists of the same thing is exactly how
# the 2026-09-19 gap stayed invisible, so they get checked rather than trusted.
CRON_JOBS="${HL_CRON_JOBS:-heartbeat.sh repo-sync.sh pg-backup.sh}"

running() {
  docker ps --filter "name=^${1}$" --filter "status=running" --format '{{.Names}}' 2>/dev/null \
    | grep -qx "$1"
}

in_words() {
  for _w in $2; do [ "$_w" = "$1" ] && return 0; done
  return 1
}

missing=""
for svc in $REQUIRED; do
  running "$svc" || missing="$missing $svc"
done

# --- publish ------------------------------------------------------------------
#
# Before the ping, deliberately. A run that is about to signal failure is the
# run whose metrics are most worth having — node_exporter can still be scraped
# when Grafana is the thing that died, and the container inventory below is what
# says *which* container it was.
publish_metrics() {
  metrics_open heartbeat

  metric_help homelab_heartbeat_timestamp_seconds gauge \
    "Unix time of the last heartbeat run"
  metric homelab_heartbeat_timestamp_seconds "$(date +%s)"

  metric_help homelab_monitoring_stack_healthy gauge \
    "1 when every container heartbeat.sh requires is running"
  if [ -n "$missing" ]; then
    metric homelab_monitoring_stack_healthy 0
  else
    metric homelab_monitoring_stack_healthy 1
  fi

  # Every container declared anywhere in docker/, and whether it is up. Parsed
  # from the compose files rather than from a list kept here, so a new service
  # is monitored the moment it is committed — no second place to remember.
  metric_help homelab_container_running gauge \
    "1 if a container declared in docker/*/docker-compose.yml is running"
  _names_seen=""
  for _compose in "$REPO"/docker/*/docker-compose.yml; do
    [ -f "$_compose" ] || continue
    _stack=$(basename "$(dirname "$_compose")")
    _declared=$(sed -n \
      's/^[[:space:]]*container_name:[[:space:]]*"\{0,1\}\([A-Za-z0-9_.-]\{1,\}\)"\{0,1\}[[:space:]]*$/\1/p' \
      "$_compose" 2>/dev/null || true)
    for _name in $_declared; do
      if in_words "$_name" "$OPTIONAL_CONTAINERS"; then _req=no; else _req=yes; fi
      if in_words "$_name" "$_running_now"; then _up=1; else _up=0; fi
      metric homelab_container_running "$_up" \
        "$(metric_kv container "$_name")" \
        "$(metric_kv stack "$_stack")" \
        "$(metric_kv required "$_req")"
      _names_seen="$_names_seen $_name"
    done
  done

  # The scheduler watching itself. A job whose cron entry is missing reports 0
  # here forever, which is the signal that was absent for three weeks.
  metric_help homelab_cron_job_installed gauge \
    "1 if this repo's scheduled job has a crontab entry"
  _crontab_now=$(crontab -l 2>/dev/null || true)
  for _job in $CRON_JOBS; do
    _installed=0
    case "$_crontab_now" in
      *"scripts/$_job"*) _installed=1 ;;
    esac
    metric homelab_cron_job_installed "$_installed" "$(metric_kv job "$_job")"
  done

  metrics_close
}

# One docker call for the whole inventory; `docker ps` per container would be a
# dozen forks every five minutes for the same answer.
_running_now=$(docker ps --filter "status=running" --format '{{.Names}}' 2>/dev/null || true)
publish_metrics

# --retry rides out a brief network blip so a flaky uplink doesn't page you;
# -m caps the whole attempt so this can never pile up under cron.
if [ -n "$missing" ]; then
  echo "heartbeat: not running:$missing — signalling failure" >&2
  curl -fsS -m 20 --retry 3 --retry-delay 5 \
    --data-raw "monitoring stack down:$missing" "$URL/fail" >/dev/null || true
  exit 1
fi

curl -fsS -m 20 --retry 3 --retry-delay 5 "$URL" >/dev/null
