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
# NOTE: this never restarts anything. Deciding *when* to restart the box that
# serves your DNS is a human's call. CI now gates main
# (.github/workflows/ci.yml), which removes the strongest objection to
# automating that step later — but a green check on a compose file is not the
# same as knowing the DNS resolver survives the restart, so the decision stays
# with a person until something proves otherwise.
#
set -eu

REPO="${HL_REPO:-/root/homelab}"
ENV_FILE="$REPO/docker/monitoring/.env"
# Discord's hard cap is 2000 characters; leave room for the code fence.
MAX_CHARS=1800

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

STALE=""
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
  cids=$(docker ps --filter "label=com.docker.compose.project.working_dir=$abs" \
                   --format '{{.ID}}' 2>/dev/null || true)
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

  if [ "$commit_ts" -gt "$oldest" ]; then
    age=$(human $((commit_ts - oldest)))
    STALE="$STALE
  $stack — code is ${age} newer than its $basis  ->  docker compose -f $compose $hint"
  fi
done

# --- report -------------------------------------------------------------------

# Built with `if` rather than `[ x ] && MSG=...`: under `set -e` an AND-list
# whose test fails takes the whole script down with it, and the test failing is
# the *normal* case here.
MSG=""
if [ -n "$PULL_ERROR" ]; then
  MSG="$MSG
**Repo sync failed on $(hostname)**
$PULL_ERROR"
fi
if [ -n "$PULLED" ] && [ -n "$STALE" ]; then
  MSG="$MSG
$PULLED"
fi
if [ -n "$STALE" ]; then
  MSG="$MSG
**Stacks running older code than the repo:**
\`\`\`$STALE
\`\`\`"
fi

# Nothing worth saying. Note the asymmetry: a clean pull with everything current
# is silent, but a pull that changed nothing while a stack is stale still
# reports — the stack is stale either way.
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

# Exit non-zero on a pull failure so cron surfaces it even if Discord is down.
[ -z "$PULL_ERROR" ]
