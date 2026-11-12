# shellcheck shell=sh
#
# What repo-sync.sh and auto-deploy.sh both need to agree on. Sourced, not run.
#
# Two scripts restart stacks unattended: repo-sync nightly, auto-deploy after a
# merge. If each kept its own idea of "the stack came back", one of them would
# eventually call a deploy healthy that the other calls broken. So the
# definition lives here, once. The same goes for the lock: there is one, and
# everything that deploys takes it (see /deploy step 1 for the manual side).
#
# Callers set REPO before calling job_only. VERIFY_TRIES and VERIFY_DELAY can be
# set before sourcing; the defaults are repo-sync's.

VERIFY_TRIES="${VERIFY_TRIES:-${HL_VERIFY_TRIES:-3}}"
VERIFY_DELAY="${VERIFY_DELAY:-${HL_VERIFY_DELAY:-10}}"

# One lock for every deploy: auto-deploy.sh, repo-sync.sh, and /deploy by hand.
# Without it, a merge landing at 04:00 means two `git pull`s and two
# `docker compose up -d` racing on the same tree and the same stacks.
DEPLOY_LOCK="${HL_DEPLOY_LOCK:-/run/lock/homelab-deploy.lock}"

# Containers compose is currently running for this stack directory.
running_ids() {
  docker ps --filter "label=com.docker.compose.project.working_dir=$1" \
            --filter "status=running" --format '{{.ID}}' 2>/dev/null || true
}

# Did the stack come back? Three questions, because each can fail alone: are at
# least as many containers running as before, is anything stuck in a restart
# loop (which `status=running` would otherwise happily count), and does any
# container's own healthcheck say it is unhealthy. The last only applies to
# services with a `healthcheck:`; "starting" is not a failure, since a slow boot
# is not a broken one.
verify_stack() {
  _abs=$1; _want=$2; _try=0
  while [ "$_try" -lt "$VERIFY_TRIES" ]; do
    sleep "$VERIFY_DELAY"
    _try=$((_try + 1))
    _now=$(running_ids "$_abs" | wc -l | tr -d ' ')
    _bad=$(docker ps --filter "label=com.docker.compose.project.working_dir=$_abs" \
                     --filter "status=restarting" --format '{{.ID}}' 2>/dev/null | wc -l | tr -d ' ')
    _sick=$(docker ps --filter "label=com.docker.compose.project.working_dir=$_abs" \
                      --filter "health=unhealthy" --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')
    if [ "$_now" -ge "$_want" ] && [ "$_bad" -eq 0 ] && [ -z "$_sick" ]; then
      return 0
    fi
  done
  if [ -n "$_sick" ]; then
    echo "$_now/$_want running, $_bad restarting, unhealthy: ${_sick% }"
  else
    echo "$_now/$_want running, $_bad restarting"
  fi
  return 1
}

# job_only <compose>: every service carries a profile, so `up -d` starts
# nothing and cron runs it with `docker compose run` (docker/photos/). Its
# one-off container can be up when this runs, which must not read as a stack to
# pull, heal or call stale. Asked of Compose rather than listed here, and only
# an answer that parsed counts: a compose file that cannot be read is not one.
job_only() {
  _jo=$(cd "$REPO" && docker compose -f "$1" config --services 2>/dev/null) || return 1
  [ -z "$_jo" ]
}

in_list() {
  for _w in $2; do [ "$_w" = "$1" ] && return 0; done
  return 1
}

# Which Discord webhook a report goes to, by its embed colour. Red and amber
# (something broke, something wants a person) go to #alerts through
# DISCORD_ALERT_WEBHOOK. Green (it worked, nothing to do) goes to #deploys
# through DISCORD_REPORT_WEBHOOK, so #alerts stays quiet unless it matters.
# With no report webhook set, green falls back to #alerts as before. Reads only
# the two keys it needs from ENV_FILE, never sources it. Prints the URL, or
# nothing when neither is set.
discord_webhook() { # <embed colour>
  if [ "${1:-}" = 7323541 ]; then   # --success #6fbf95
    _dw=$(sed -n 's/^DISCORD_REPORT_WEBHOOK=//p' "$ENV_FILE" 2>/dev/null | tail -n1 | tr -d '"'"'"' \r' || true)
    if [ -n "$_dw" ]; then printf '%s' "$_dw"; return 0; fi
  fi
  sed -n 's/^DISCORD_ALERT_WEBHOOK=//p' "$ENV_FILE" 2>/dev/null | tail -n1 | tr -d '"'"'"' \r' || true
}

# Escape for JSON by hand rather than depending on jq or python being installed:
# backslashes first, then quotes, then fold newlines into \n.
json_escape() {
  printf '%s' "${1:-}" \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
    | awk 'NR > 1 { printf "\\n" } { printf "%s", $0 }'
}
json_escape_line() {
  printf '%s' "${1:-}" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}
