#!/usr/bin/env sh
#
# Daily repo sync + deployment drift report. Run from cron on the Docker LXC:
#
#   0 4 * * * /root/homelab/scripts/repo-sync.sh >> /var/log/repo-sync.log 2>&1
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

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"

# Stacks never restarted automatically — see "Self-healing" above.
NO_AUTOHEAL="${HL_NO_AUTOHEAL:-proxy}"
# Set HL_AUTOHEAL=no for report-only behaviour.
AUTOHEAL="${HL_AUTOHEAL:-yes}"
# Verification after a restart: attempts x delay. Compose needs a few seconds to
# settle, and a container that crashes on boot usually does so within one cycle.
VERIFY_TRIES="${HL_VERIFY_TRIES:-3}"
VERIFY_DELAY="${HL_VERIFY_DELAY:-10}"

# --- this script's own dead man's switch --------------------------------------
#
# heartbeat.sh proves the box is alive. Nothing proved *this* was still running,
# and it only speaks when there is something to say — so a cron entry that was
# never installed looks exactly like a run of quiet, healthy days. It stayed
# that way for three weeks (2026-09-19), while a merged change sat undeployed
# and `git log` on GitHub said everything had shipped.
#
# A job cannot report its own absence, so Healthchecks does it from outside:
# every run pings, and silence past the grace period pages. Same shape as
# heartbeat.sh, one level up — that one watches the box, this one watches the
# thing that watches the box.
HC_URL=$(sed -n 's/^HEALTHCHECKS_REPO_SYNC_URL=//p' "$ENV_FILE" 2>/dev/null | tail -n1 | tr -d '"'"'"' \r')

ping_healthchecks() {
  _code=$?
  if [ -n "$HC_URL" ]; then
    # /<exit code>: 0 records a success and anything else a failure, so "ran and
    # failed" stays distinguishable from "never ran".
    curl -fsS -m 20 --retry 3 --retry-delay 5 "$HC_URL/$_code" >/dev/null 2>&1 || true
  fi
}
trap ping_healthchecks EXIT

cd "$REPO" || { echo "repo-sync: $REPO not found" >&2; exit 1; }

# --- pull ---------------------------------------------------------------------

BEFORE=$(git rev-parse HEAD)
PULL_ERROR=""

CLEARED=""

if ! FETCH_ERR=$(git fetch --quiet origin 2>&1); then
  PULL_ERROR="git fetch failed: $FETCH_ERR"
else
  # A container that owns a bind-mounted config directory writes its own
  # skeletons into it. Homepage does exactly this: on start it drops an empty
  # custom.css and custom.js into docker/dashboard/config/ when they are
  # missing. The day the repo starts tracking such a file, every pull stops —
  # "untracked working tree files would be overwritten" — for every stack at
  # once, over a file that has nothing in it.
  #
  # So for each file an incoming commit ADDS, an untracked *empty* copy in the
  # working tree is cleared out of the way. Deliberately narrow: anything with a
  # byte in it is somebody's work and still stops the pull, to be looked at.
  for f in $(git diff --name-only --diff-filter=A HEAD..@{u} 2>/dev/null || true); do
    [ -e "$f" ] || continue
    if [ -s "$f" ]; then
      continue
    fi
    if git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      continue
    fi
    rm -f "$f"
    CLEARED="$CLEARED
  $f"
  done

  if ! PULL_ERR=$(git pull --ff-only --quiet 2>&1); then
    # --ff-only refuses to merge or rebase, so a diverged tree or a local edit
    # stops here rather than being silently resolved. Reported, not swallowed:
    # a cron job that fails quietly is worse than no cron job.
    PULL_ERROR="git pull --ff-only failed: $PULL_ERR"
  fi
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

# --- metrics ------------------------------------------------------------------
#
# Everything below is already computed to build the Discord report; this writes
# it down as well. The distinction is worth stating because it decides what goes
# where: Discord answers "does this need me *now*", and is read once. A time
# series answers "is this still true", "how long has it been true", and "did the
# restart actually fix it" — which is what you want at 9am when the 4am message
# has scrolled away, and what nothing in this lab could answer before.
#
# The drift number in particular is the one that was missing on 2026-09-19. It
# existed in the report the moment the job ran; it just never existed anywhere
# you could look.
#
# Collected during the loop below and written out afterwards, grouped by family
# — the text format needs each family's samples contiguous under its own
# HELP/TYPE, and this loop produces two families at once. See metric_raw().
M_STACK_RUNNING=""
M_STACK_DRIFT=""

metrics_open repo_sync

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
    M_STACK_RUNNING="$M_STACK_RUNNING
homelab_stack_running{$(metric_kv stack "$stack")} 0"
    continue
  fi
  M_STACK_RUNNING="$M_STACK_RUNNING
homelab_stack_running{$(metric_kv stack "$stack")} 1"

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

  # Emitted for every stack, including the ones that are in sync — a gauge that
  # only appears when something is wrong cannot be graphed, and "0 for the last
  # 30 days" is the reassurance the panel exists to give.
  if [ "$commit_ts" -gt "$oldest" ]; then
    drift=$((commit_ts - oldest))
  else
    drift=0
  fi
  M_STACK_DRIFT="$M_STACK_DRIFT
homelab_stack_deploy_drift_seconds{$(metric_kv stack "$stack"),$(metric_kv basis "$basis")} $drift"

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

# --- stack metrics ------------------------------------------------------------
metric_help homelab_stack_running gauge \
  "1 if any container is running for a stack declared in docker/"
metric_raw "$M_STACK_RUNNING"
metric_help homelab_stack_deploy_drift_seconds gauge \
  "Seconds between a stack's newest deployable commit and what is running"
metric_raw "$M_STACK_DRIFT"

# --- config drift -------------------------------------------------------------
#
# The six-day bug, as a metric. `prometheus.yml`, the Caddyfile and friends are
# bind-mounted as single FILES, and Docker resolves a file mount to an inode
# when the container is created. git replaces files rather than editing them, so
# a pull gives the path a new inode and leaves the container reading the old,
# now-unlinked copy — indefinitely, while `git pull` says "Already up to date",
# `/-/reload` returns 200 and the repo looks correct. It cost three sessions of
# re-diagnosing an alert that had already been fixed.
#
# probe-status.sh answers this on demand, on the box, for Prometheus. This
# answers it for every container in the lab, every night, without being asked.
#
# Compared by copying the container's copy out with `docker cp` rather than by
# checksumming inside it: half these images have no shell, let alone md5sum, and
# a check that silently skips the containers it cannot run a binary in is the
# kind of check that reports all-clear forever.
#
# Directory mounts are deliberately not checked — they do not have this problem,
# which is exactly why the README recommends them for new config.
metric_help homelab_config_drift gauge \
  "1 if a container's bind-mounted config file differs from the repo's copy"
_cfg_tmp=$(mktemp)
for _c in $(docker ps --format '{{.Names}}' 2>/dev/null || true); do
  docker inspect \
    -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}{{end}}' \
    "$_c" 2>/dev/null \
  | while IFS='|' read -r _src _dest; do
      [ -n "${_src:-}" ] && [ -n "${_dest:-}" ] || continue
      # Only files this repo owns. A container's own data volume bind is not
      # drift, it is storage.
      case "$_src" in "$REPO"/*) ;; *) continue ;; esac
      [ -f "$_src" ] || continue
      docker cp "$_c:$_dest" "$_cfg_tmp" >/dev/null 2>&1 || continue
      if cmp -s "$_cfg_tmp" "$_src"; then _drift=0; else _drift=1; fi
      metric homelab_config_drift "$_drift" \
        "$(metric_kv container "$_c")" \
        "$(metric_kv path "${_src#"$REPO"/}")"
    done
done
rm -f "$_cfg_tmp"

# --- run metrics --------------------------------------------------------------
metric_help homelab_repo_sync_timestamp_seconds gauge \
  "Unix time of the last repo-sync run"
metric homelab_repo_sync_timestamp_seconds "$(date +%s)"

metric_help homelab_repo_sync_success gauge \
  "1 if the last repo-sync pulled cleanly and every restart it attempted came back"
if [ -n "$PULL_ERROR" ] || [ -n "$FAILED" ]; then
  metric homelab_repo_sync_success 0
else
  metric homelab_repo_sync_success 1
fi

# Non-zero here means the box is knowingly behind GitHub — the pull failed, or
# was never attempted. After a healthy run it is 0, which is the point: the
# three-week gap would have shown as a line climbing off the top of a panel.
metric_help homelab_repo_commits_behind gauge \
  "Commits the working tree is behind its upstream branch"
metric homelab_repo_commits_behind "$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)"
metrics_close

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
if [ -n "$CLEARED" ]; then
  MSG="$MSG
**Cleared empty files a container had written, so the pull could apply:**
\`\`\`$CLEARED
\`\`\`"
fi
if [ -n "$PULLED" ]; then
  MSG="$MSG
$PULLED"
fi
if [ -z "$HC_URL" ]; then
  # Reported every run, deliberately: an unconfigured dead man's switch is
  # exactly the silence this section exists to break, and it stops the moment
  # the variable is set.
  MSG="$MSG
**This sync has no dead man's switch.** Set \`HEALTHCHECKS_REPO_SYNC_URL\` in
\`docker/monitoring/.env\` — without it, nothing notices when this stops running."
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
