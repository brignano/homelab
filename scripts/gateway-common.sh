# shellcheck shell=sh
#
# What gateway-watch.sh and access-log-poll.sh share. Sourced, not run.
#
# Both are thin: the reading and deciding is docker/gateway/tools/gatewaywatch.py
# (tested offline in CI). This file is the part every scheduled job here has to
# get right the same way: one run at a time, a dead man's switch that proves it
# is armed (healthchecks.sh), metrics left behind (metrics.sh), and alerts to
# #alerts through the webhook, never through the assistant bot (the `alerting`
# skill says why).
#
# Not set up = not running, and not paging: until docker/gateway/.env exists
# each job exits 0 and publishes homelab_gateway_configured 0.
#
# Callers set REPO and source metrics.sh, healthchecks.sh and deploy-common.sh
# first.

ENV_FILE="$REPO/docker/monitoring/.env"
GW_ENV="${HL_GATEWAY_ENV:-$REPO/docker/gateway/.env}"
GW_STATE="${HL_GATEWAY_STATE:-/var/lib/homelab/gateway}"
GW_TOOL="$REPO/docker/gateway/tools/gatewaywatch.py"
RED=14711391    # --danger    #e07a5f

gw_configured() { # <job>
  metrics_open "$(printf '%s' "$1" | tr -c 'a-zA-Z0-9_' '_')_configured"
  metric_help homelab_gateway_configured gauge \
    "1 if docker/gateway/.env exists, so the gateway's scheduled jobs are live"
  if [ -f "$GW_ENV" ]; then metric homelab_gateway_configured 1 "$(metric_kv check "$1")"
  else metric homelab_gateway_configured 0 "$(metric_kv check "$1")"; fi
  metrics_close
  [ -f "$GW_ENV" ]
}

# gw_post <title> <file of Markdown lines>: one embed per run, however many
# lines, so a burst of refusals is one message rather than a page per request.
gw_post() {
  _gw_body=$(head -c 3800 "$2")
  printf '%s\n%s\n' "$1" "$_gw_body"
  _gw_wh=$(discord_webhook "$RED")
  if [ -z "$_gw_wh" ]; then
    echo "$GW_JOB: DISCORD_ALERT_WEBHOOK unset in $ENV_FILE — not posted" >&2
    return 1
  fi
  curl -fsS -m 20 --retry 3 --retry-delay 5 -H 'Content-Type: application/json' \
    -d "{\"embeds\":[{\"title\":\"$(json_escape_line "$1")\",\"description\":\"$(json_escape "$_gw_body")\",\"color\":$RED}]}" \
    "$_gw_wh" >/dev/null || { echo "$GW_JOB: Discord post failed" >&2; return 1; }
}

# gw_run <job> <watch|poll> <HEALTHCHECKS_*_URL var> <alert title> [extra args...]
gw_run() {
  GW_JOB=$1; _gw_cmd=$2; _gw_hc=$3; _gw_title=$4; shift 4

  exec 8>"/run/lock/homelab-$GW_JOB.lock"
  flock -n 8 || { echo "$GW_JOB: previous run still going" >&2; exit 0; }

  gw_configured "$GW_JOB" || exit 0

  _gw_url=""
  if hc_url "$_gw_hc" "$ENV_FILE"; then _gw_url=$HC_VALUE
  else echo "$GW_JOB: $HC_REASON" >&2; hc_unarmed "$GW_JOB"; fi

  _gw_tmp=$(mktemp -d)
  # shellcheck disable=SC2064
  trap "rm -rf '$_gw_tmp'" EXIT
  _gw_rc=0
  python3 -I "$GW_TOOL" "$_gw_cmd" --env "$GW_ENV" --state "$GW_STATE" \
    --alerts "$_gw_tmp/alerts" --metrics "$_gw_tmp/metrics" "$@" \
    2>"$_gw_tmp/err" || _gw_rc=$?
  cat "$_gw_tmp/err" >&2

  if [ "$_gw_rc" -eq 0 ]; then
    metrics_open "$(printf '%s' "$GW_JOB" | tr -c 'a-zA-Z0-9_' '_')"
    metric_raw "$(cat "$_gw_tmp/metrics")"
    metrics_close
    if [ -s "$_gw_tmp/alerts" ]; then gw_post "$_gw_title" "$_gw_tmp/alerts" || _gw_rc=1; fi
  fi

  # The ping says whether the job ran, not whether anything was found: a run
  # that read the logs and posted its alerts is a healthy run.
  if [ -n "$_gw_url" ]; then
    if [ "$_gw_rc" -eq 0 ]; then hc_ping "$_gw_url" "$GW_JOB" || true
    else hc_ping "$_gw_url/fail" "$GW_JOB" "$(tail -c 2000 "$_gw_tmp/err")" || true; fi
  fi
  return "$_gw_rc"
}
