#!/usr/bin/env sh
#
# Daily repo sync + deployment drift report. Run from cron on the Docker LXC:
#
#   0 4 * * * /root/homelab/scripts/repo-sync.sh
#
# Why this exists
# ---------------
# Two separate gaps, and only fixing one of them makes things worse.
#
# The first is obvious: the working tree falls behind GitHub because pulling is
# a manual step that's easy to forget. `git pull --ff-only` daily bounds that to
# 24 hours.
#
# The second is the one an auto-pull *creates*. Nothing on this box runs from
# the working tree — every service runs from a built image, or read its config
# when its container started. So a silent pull leaves the repo ahead of what is
# actually running, and now `git log` says you're current when you aren't. That
# turns a visible gap into an invisible one. Pulling without reporting would be
# a downgrade, so this does both or neither.
#
# What "stale" means, per stack
# -----------------------------
# The right question differs by how the stack gets its code, so the check does
# too — decided by whether the compose file has a `build:` key:
#
#   builds locally (assistant, proxy)  ->  compare against the IMAGE's creation
#       time. A restart does not rebuild, so container start time would report
#       fresh while the image is stale — exactly the case worth catching, since
#       a host reboot restarts everything without rebuilding anything.
#
#   pulls upstream images (everything else)  ->  compare against the CONTAINER's
#       start time. These read their config from the repo via bind mounts, and a
#       plain restart is enough to pick up a change.
#
# It is a heuristic, not proof: a stack rebuilt for an unrelated reason reads as
# fresh. It is right about the case that actually happens — you pulled, and
# forgot to restart.
#
# Reports only when there is something to do. Silence means "up to date and
# everything is running current code", the same discipline as heartbeat.sh.
#
# Self-healing, and where it stops
# -------------------------------
# A stale stack is restarted automatically, because the fix was always the same
# command and running it by hand added nothing. CI gates main, and a build
# failure is inherently safe: `up -d --build` builds *before* it recreates, so a
# broken build leaves the old container serving.
#
# The dangerous case is a build that succeeds and then crashes, which is why
# every restart is *verified* rather than fired and forgotten. Restarting
# without checking is not self-healing, it is auto-breaking faster.
#
# HL_NO_AUTOHEAL lists stacks that are only ever reported, never restarted.
# `proxy` is there by default and should stay: it contains AdGuard, which is the
# household's DNS. A 4am restart that does not come back takes the network down
# until someone notices, and every tool you would reach for to diagnose it
# resolves names through the thing that is down. CI validating the Caddyfile
# reduces that risk; it cannot know whether AdGuard comes back.
#
# Auto-healing is also skipped entirely when the pull failed — a tree in an
# unknown state is not one to deploy from.
#
set -eu

REPO="${HL_REPO:-/root/homelab}"
ENV_FILE="$REPO/docker/monitoring/.env"
# Discord's hard cap is 2000 characters; leave room for the code fences.
MAX_CHARS=1800

# Stacks never restarted automatically — see "Self-healing" above.
NO_AUTOHEAL="${HL_NO_AUTOHEAL:-proxy}"
# Set HL_AUTOHEAL=no for report-only behaviour.
AUTOHEAL="${HL_AUTOHEAL:-yes}"
# Verification after a restart: attempts x delay. Compose needs a few seconds to
# settle, and a container that crashes on boot usually does so within one cycle.
VERIFY_TRIES="${HL_VERIFY_TRIES:-3}"
VERIFY_DELAY="${HL_VERIFY_DELAY:-10}"

cd "$REPO" || { echo "repo-sync: $REPO not found" >&2; exit 1; }

# --- pull ---------------------------------------------------------------------

BEFORE=$(git rev-parse HEAD)
PULL_ERROR=""

if ! FETCH_ERR=$(git fetch --quiet origin 2>&1); then
  PULL_ERROR="git fetch failed: $FETCH_ERR"
elif ! PULL_ERR=$(git pull --ff-only --quiet 2>&1); then
  # --ff-only refuses to merge or rebase, so a diverged tree or a local edit
  # stops here rather than being silently resolved. Reported, not swallowed:
  # a cron job that fails quietly is worse than no cron job.
  PULL_ERROR="git pull --ff-only failed: $PULL_ERR"
fi

AFTER=$(git rev-parse HEAD)
PULLED=""
if [ "$BEFORE" != "$AFTER" ]; then
  COUNT=$(git rev-list --count "$BEFORE..$AFTER")
  PULLED="Pulled $COUNT commit(s): $(git rev-parse --short "$BEFORE") -> $(git rev-parse --short "$AFTER")"
fi

# --- drift --------------------------------------------------------------------

# RFC3339 -> epoch seconds. Empty on anything unparseable, so a weird value
# degrades to "cannot tell" rather than to a bogus verdict.
epoch() {
  [ -n "${1:-}" ] || return 0
  date -d "$1" +%s 2>/dev/null || true
}

human() {
  awk -v s="$1" 'BEGIN {
    if      (s < 3600)  printf "%dm", s / 60
    else if (s < 86400) printf "%dh", s / 3600
    else                printf "%dd", s / 86400
  }'
}

# Containers compose is currently running for this stack directory.
running_ids() {
  docker ps --filter "label=com.docker.compose.project.working_dir=$1" \
            --filter "status=running" --format '{{.ID}}' 2>/dev/null || true
}

# Did the stack come back? Two questions, because either can fail alone: are at
# least as many containers running as before, and is anything stuck in a restart
# loop (which `status=running` would otherwise happily count).
verify_stack() {
  _abs=$1; _want=$2; _try=0
  while [ "$_try" -lt "$VERIFY_TRIES" ]; do
    sleep "$VERIFY_DELAY"
    _try=$((_try + 1))
    _now=$(running_ids "$_abs" | wc -l | tr -d ' ')
    _bad=$(docker ps --filter "label=com.docker.compose.project.working_dir=$_abs" \
                     --filter "status=restarting" --format '{{.ID}}' 2>/dev/null | wc -l | tr -d ' ')
    if [ "$_now" -ge "$_want" ] && [ "$_bad" -eq 0 ]; then
      return 0
    fi
  done
  echo "$_now/$_want running, $_bad restarting"
  return 1
}

in_list() {
  for _w in $2; do [ "$_w" = "$1" ] && return 0; done
  return 1
}

HEALED=""
FAILED=""
MANUAL=""
MANUAL_CMDS=""
NOT_RUNNING=""

for dir in docker/*/; do
  stack=$(basename "$dir")
  compose="${dir}docker-compose.yml"
  [ -f "$compose" ] || continue

  # Newest commit touching this stack that could actually change what runs.
  #
  # Documentation, tests and .env.example live inside the stack directories but
  # are never deployed — the assistant Dockerfile, for instance, copies only
  # requirements.txt, app/ and guild.yml. Counting them meant a README-only
  # commit reported the stack as stale, which on the first real run flagged two
  # of four stacks for changes that could not possibly affect them. A drift
  # report with a 50% false-positive rate is one you learn to ignore, which is
  # worse than not having it.
  #
  # `:(exclude,glob)` and not plain `:(exclude)`: without glob magic `**` is not
  # expanded and the exclusion silently does nothing.
  #
  # Empty result (a stack whose only commits are docs) -> nothing deployable has
  # ever changed, so there is nothing to be stale against. Skip it.
  commit_ts=$(git log -1 --format=%ct -- "$dir" \
    ":(exclude,glob)${dir}**/*.md" \
    ":(exclude,glob)${dir}tests/**" \
    ":(exclude)${dir}.env.example" 2>/dev/null || true)
  [ -n "$commit_ts" ] || continue

  # Absolute path is what compose stamps on its containers.
  abs=$(cd "$dir" && pwd)
  cids=$(running_ids "$abs")
  if [ -z "$cids" ]; then
    NOT_RUNNING="$NOT_RUNNING $stack"
    continue
  fi

  # A stack that builds its own image must be compared against the image.
  if grep -qE '^[[:space:]]+build:' "$compose"; then
    basis="image"; hint="up -d --build"
  else
    basis="start"; hint="up -d"
  fi

  # The oldest container is what limits the stack's freshness.
  oldest=""
  for cid in $cids; do
    if [ "$basis" = "image" ]; then
      img=$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true)
      [ -n "$img" ] || continue
      ts=$(epoch "$(docker image inspect -f '{{.Created}}' "$img" 2>/dev/null || true)")
    else
      ts=$(epoch "$(docker inspect -f '{{.State.StartedAt}}' "$cid" 2>/dev/null || true)")
    fi
    [ -n "$ts" ] || continue
    if [ -z "$oldest" ] || [ "$ts" -lt "$oldest" ]; then oldest=$ts; fi
  done
  [ -n "$oldest" ] || continue

  [ "$commit_ts" -gt "$oldest" ] || continue
  age=$(human $((commit_ts - oldest)))
  cmd="docker compose -f $compose $hint"

  # Report-only: globally disabled, on the never-touch list, or the tree is in
  # an unknown state because the pull failed.
  if [ "$AUTOHEAL" != "yes" ] || in_list "$stack" "$NO_AUTOHEAL" || [ -n "$PULL_ERROR" ]; then
    MANUAL="$MANUAL
  $stack ($basis, $age)"
    MANUAL_CMDS="$MANUAL_CMDS
$cmd"
    continue
  fi

  want=$(printf '%s\n' "$cids" | wc -l | tr -d ' ')
  if ! err=$(cd "$REPO" && $cmd 2>&1); then
    # The build or the pull failed. `up -d --build` builds before recreating, so
    # the previous containers are almost certainly still serving — say so rather
    # than implying the stack is down.
    FAILED="$FAILED
  $stack ($basis, $age) — command failed, previous containers likely still up
    $(printf '%s' "$err" | tail -n 2 | tr '\n' ' ')"
    continue
  fi

  if ! why=$(verify_stack "$abs" "$want"); then
    FAILED="$FAILED
  $stack ($basis, $age) — restarted but did NOT come back: $why
    $cmd"
    continue
  fi
  HEALED="$HEALED
  $stack ($basis, $age)"
done

# --- report -------------------------------------------------------------------

# Built with `if` rather than `[ x ] && MSG=...`: under `set -e` an AND-list
# whose test fails takes the whole script down with it, and the test failing is
# the *normal* case here.
#
# One section per outcome, and the commands you still have to run collected into
# a single block at the end rather than repeated after every line — they are all
# the same shape, and a block is one paste instead of three.
MSG=""
if [ -n "$PULL_ERROR" ]; then
  MSG="$MSG
**Repo sync failed on $(hostname)** — nothing was restarted
\`\`\`
$PULL_ERROR
\`\`\`"
fi
if [ -n "$PULLED" ]; then
  MSG="$MSG
$PULLED"
fi
if [ -n "$HEALED" ]; then
  MSG="$MSG
**Restarted, now running current code:**
\`\`\`$HEALED
\`\`\`"
fi
if [ -n "$FAILED" ]; then
  MSG="$MSG
**RESTART FAILED — still on old code:**
\`\`\`$FAILED
\`\`\`"
fi
if [ -n "$MANUAL" ]; then
  MSG="$MSG
**Needs you:**
\`\`\`$MANUAL
\`\`\`
\`\`\`bash
cd $REPO$MANUAL_CMDS
\`\`\`"
fi

# Nothing worth saying. Silence now means "nothing changed and nothing needs
# you" — a successful restart IS a change and is always reported, so the channel
# doubles as a deployment log.
if [ -z "$MSG" ]; then
  if [ -n "$NOT_RUNNING" ]; then
    echo "repo-sync: ok (stacks not running:$NOT_RUNNING)"
  else
    echo "repo-sync: ok"
  fi
  exit 0
fi

MSG=$(printf '%s' "$MSG" | cut -c1-"$MAX_CHARS")
echo "$MSG"

[ -f "$ENV_FILE" ] || { echo "repo-sync: $ENV_FILE not found — cannot report" >&2; exit 1; }
# Read only the key we need; avoids sourcing a file full of other secrets.
WEBHOOK=$(sed -n 's/^DISCORD_ALERT_WEBHOOK=//p' "$ENV_FILE" | tail -n1 | tr -d '"'"'"' \r')
[ -n "$WEBHOOK" ] || { echo "repo-sync: DISCORD_ALERT_WEBHOOK unset in $ENV_FILE" >&2; exit 1; }

# Escape for JSON by hand rather than depending on jq or python being installed:
# backslashes first, then quotes, then fold newlines into \n.
PAYLOAD=$(printf '%s' "$MSG" \
  | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
  | awk '{printf "%s\\n", $0}')

curl -fsS -m 20 --retry 3 --retry-delay 5 \
  -H 'Content-Type: application/json' \
  -d "{\"content\":\"$PAYLOAD\"}" "$WEBHOOK" >/dev/null

# Exit non-zero if anything actually went wrong, so cron surfaces it even when
# Discord is unreachable. A stack merely *needing* a human is not an error.
[ -z "$PULL_ERROR" ] && [ -z "$FAILED" ]
