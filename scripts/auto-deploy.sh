#!/usr/bin/env sh
#
# Deploy what merged to main, a few minutes after it merged. Run from cron on
# the Docker LXC:
#
#   */5 * * * * /root/homelab/scripts/auto-deploy.sh >> /var/log/auto-deploy.log 2>&1
#
# Design: docs/design/tsd-auto-deploy.md (decision record decisions/0001).
#
# Why this exists
# ---------------
# Cloud Claude sessions cannot reach this box, so every merged PR waited for a
# person to run /deploy, or for repo-sync at 04:00. This closes that gap without
# opening the lab: it POLLS GitHub. Nothing gets inbound access — no runner, no
# webhook, no deploy key that can push.
#
# It is a driver, not a second deploy. What to do comes from
# `deploy-plan.sh --machine` (the same rules /deploy reads), "did it come back"
# from verify_stack in deploy-common.sh (the same check repo-sync heals with).
#
# What it will not do
# -------------------
# - Touch a stack in HL_NO_AUTOHEAL (proxy and matterbridge by default). DNS and
#   Alexa are only ever restarted by a person watching. It posts the line to run.
# - Start a stack that was not running. Stopped may be deliberate.
# - Deploy a stack whose .env is missing a key its .env.example now has, or that
#   has no .env at all. It names the key, never a value.
# - Anything on the Proxmox host (scripts/host/).
# - Roll back. A failed verify alerts once; a revert PR is the rollback, and it
#   arrives here like any other merge.
#
# Quiet by design: a run where main has not moved does a `git fetch` and nothing
# else, and posts nothing. Discord hears about a run only when it changed
# something, needs a person, or failed.
#
# Pausing
# -------
#   touch /var/lib/homelab/auto-deploy.pause    # until removed
#   HL_AUTOHEAL=no                              # the same switch repo-sync obeys
# /deploy also writes a marker this honours for 30 minutes, so a manual deploy
# is never raced (see /deploy step 1). Clearing it is part of /deploy's last step,
# and if that is forgotten it simply expires.
#
# State, in /var/lib/homelab/auto-deploy/
# --------------------------------------
#   last-good     the last commit fully deployed. The next plan covers
#                 last-good..HEAD, so a stack that failed is retried with the
#                 next merge instead of being forgotten.
#   last-failed   the commit that last failed. Not retried, and not re-posted,
#                 until main moves or a manual /deploy clears it.
#   manual-deploy epoch seconds when /deploy started.
set -eu

REPO="${HL_REPO:-/root/homelab}"
ENV_FILE="$REPO/docker/monitoring/.env"
STATE_DIR="${HL_AUTO_DEPLOY_STATE:-/var/lib/homelab/auto-deploy}"
PAUSE_FILE="${HL_AUTO_DEPLOY_PAUSE:-/var/lib/homelab/auto-deploy.pause}"
MANUAL_HOLD="${HL_AUTO_DEPLOY_MANUAL_HOLD:-1800}"
BRANCH="${HL_AUTO_DEPLOY_BRANCH:-main}"
NO_AUTOHEAL="${HL_NO_AUTOHEAL:-proxy matterbridge}"
AUTOHEAL="${HL_AUTOHEAL:-yes}"
MAX_CHARS=3800

SCRIPTS=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=scripts/metrics.sh
. "$SCRIPTS/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$SCRIPTS/healthchecks.sh"
# shellcheck source=scripts/deploy-common.sh
. "$SCRIPTS/deploy-common.sh"

log() { printf '%s auto-deploy: %s\n' "$(date '+%F %T')" "$*"; }

# --- dead man's switch ----------------------------------------------------------
# Pinged on every run that got far enough to decide something, including runs
# that found nothing to do and runs that reported a failure to Discord: what
# Healthchecks watches is that this job is alive and deciding. Not pinged when
# `git fetch` fails, so a network that stays down past the grace period pages
# from outside, where a dead network cannot stop it.
if hc_url HEALTHCHECKS_AUTO_DEPLOY_URL "$ENV_FILE"; then HC_URL=$HC_VALUE; else HC_URL=""; fi
ping_hc() {
  if [ -n "$HC_URL" ]; then
    hc_ping "$HC_URL/${1:-0}" auto-deploy || true
  else
    hc_unarmed auto-deploy
  fi
}

# --- metrics --------------------------------------------------------------------
# behind: HEAD is not origin/main after this run (a dirty tree or a
# non-fast-forward, already reported once to Discord). Information for the
# dashboard, not an alert: the alert is hl-auto-deploy-stale, on the timestamp,
# which stops moving when no run reaches a decision at all.
write_metrics() { # <behind 0|1> <paused 0|1> <failed 0|1>
  metrics_open auto_deploy
  metric_help homelab_auto_deploy_timestamp_seconds gauge \
    "When auto-deploy.sh last ran to a decision"
  metric homelab_auto_deploy_timestamp_seconds "$(date +%s)"
  metric_help homelab_auto_deploy_behind gauge \
    "1 if the box is not at origin/main after auto-deploy's last run"
  metric homelab_auto_deploy_behind "$1"
  metric_help homelab_auto_deploy_paused gauge \
    "1 if auto-deploy is paused (pause file, HL_AUTOHEAL=no, or a manual /deploy)"
  metric homelab_auto_deploy_paused "$2"
  metric_help homelab_auto_deploy_failed gauge \
    "1 if the last auto-deploy failed and has not been cleared"
  metric homelab_auto_deploy_failed "$3"
  if [ -f "$STATE_DIR/last-good-ts" ]; then
    metric_help homelab_auto_deploy_last_success_seconds gauge \
      "When auto-deploy last deployed a commit cleanly"
    metric homelab_auto_deploy_last_success_seconds "$(cat "$STATE_DIR/last-good-ts")"
  fi
  metrics_close
}

failed_flag() { if [ -f "$STATE_DIR/last-failed" ]; then echo 1; else echo 0; fi; }

# --- Discord --------------------------------------------------------------------
post() { # <title> <body> <color>
  # The log keeps the whole report: Discord is where it is read, this is where
  # it is found again.
  printf '%s\n%s\n' "$1" "$2"
  _wh=$(discord_webhook "$3")
  if [ -z "$_wh" ]; then
    log "DISCORD_ALERT_WEBHOOK unset in $ENV_FILE — not posted"
    return 0
  fi
  _body=$(printf '%s' "$2" | cut -c1-"$MAX_CHARS")
  curl -fsS -m 20 --retry 3 --retry-delay 5 \
    -H 'Content-Type: application/json' \
    -d "{\"embeds\":[{\"title\":\"$(json_escape_line "$1")\",\"description\":\"$(json_escape "$_body")\",\"color\":$3}]}" \
    "$_wh" >/dev/null || log "Discord post failed: $1"
}
RED=14711391    # --danger    #e07a5f
AMBER=14722127  # --attention #e0a44f
GREEN=7323541   # --success   #6fbf95

manual_line() { # <stack or empty>
  if [ -n "${1:-}" ]; then
    printf 'hl-ssh -t "cd /root/homelab && claude '"'"'/deploy %s'"'"'"' "$1"
  else
    printf 'hl-ssh -t "cd /root/homelab && claude '"'"'/deploy'"'"'"'
  fi
}

# Stop before deploying: record the commit so the next tick stays quiet, alert
# once, and leave the tree exactly as it was.
refuse() { # <target sha> <reason>
  printf '%s\n' "$1" > "$STATE_DIR/last-failed"
  log "not deploying $(git rev-parse --short "$1"): $2"
  post "❌ Auto-deploy stopped · $(hostname)" \
"Not deploying \`$(git rev-parse --short "$1")\` $(git log -1 --format=%s "$1" 2>/dev/null || true)

$2

Nothing was changed. Fix it on the box, then run:
\`\`\`
$(manual_line)
\`\`\`" "$RED"
  write_metrics 1 0 1
  ping_hc 0
  exit 0
}

mkdir -p "$STATE_DIR"

# ================================================================================
# Phase 1: decide whether there is anything to do, and pull it.
# ================================================================================
if [ "${1:-}" != "--after-pull" ]; then

  # --- paused? ------------------------------------------------------------------
  if [ -e "$PAUSE_FILE" ] || [ "$AUTOHEAL" != "yes" ]; then
    write_metrics 0 1 "$(failed_flag)"
    ping_hc 0
    exit 0
  fi
  # --- the lock -----------------------------------------------------------------
  # Busy means repo-sync or a manual deploy is running. The next tick is five
  # minutes away, so skip quietly rather than queue. fd 9 stays open across the
  # exec below, so the lock is held until the deploy finishes.
  exec 9>"$DEPLOY_LOCK"
  if ! flock -n 9; then
    exit 0
  fi

  # A manual /deploy in the last 30 minutes. Checked under the lock: /deploy
  # writes the marker after waiting on the same lock, so a tick that got the
  # lock first finishes before /deploy starts, and one that comes after sees it.
  if [ -f "$STATE_DIR/manual-deploy" ]; then
    _since=$(cat "$STATE_DIR/manual-deploy" 2>/dev/null || echo 0)
    case "$_since" in ''|*[!0-9]*) _since=0 ;; esac
    if [ $(( $(date +%s) - _since )) -lt "$MANUAL_HOLD" ]; then
      write_metrics 0 1 "$(failed_flag)"
      ping_hc 0
      exit 0
    fi
  fi

  cd "$REPO" || { log "$REPO not found"; exit 1; }

  if ! FETCH_ERR=$(git fetch --quiet origin "$BRANCH" 2>&1); then
    log "git fetch failed: $FETCH_ERR"
    exit 1
  fi

  HEAD_SHA=$(git rev-parse HEAD)
  TARGET=$(git rev-parse "origin/$BRANCH")

  if [ "$HEAD_SHA" = "$TARGET" ]; then
    # Up to date with GitHub, but was the last pull actually deployed? Phase 2
    # runs after the merge, so a run killed part-way (a reboot, a hung compose,
    # a bug) leaves HEAD current and nothing recording the work left. last-good
    # behind HEAD with no failure on record is exactly that: finish it now.
    _good=$(cat "$STATE_DIR/last-good" 2>/dev/null || true)
    _failed=$(cat "$STATE_DIR/last-failed" 2>/dev/null || true)
    if [ -n "$_good" ] && [ "$_good" != "$HEAD_SHA" ] && [ "$_failed" != "$HEAD_SHA" ] \
       && git merge-base --is-ancestor "$_good" HEAD 2>/dev/null; then
      # Once per commit. A Phase 2 that dies the same way every time would
      # otherwise restart the same stacks every five minutes, silently.
      if [ "$(cat "$STATE_DIR/resumed" 2>/dev/null || true)" = "$HEAD_SHA" ]; then
        rm -f "$STATE_DIR/resumed"
        refuse "$HEAD_SHA" "A deploy of this commit was started twice and did not finish either time (a crash or a hung compose). See /var/log/auto-deploy.log."
      fi
      printf '%s\n' "$HEAD_SHA" > "$STATE_DIR/resumed"
      log "resuming an unfinished deploy of $(git rev-parse --short "$_good")..$(git rev-parse --short HEAD)"
      exec "$REPO/scripts/auto-deploy.sh" --after-pull "$_good"
    fi
    write_metrics 0 0 "$(failed_flag)"
    ping_hc 0
    exit 0
  fi

  # Already failed on this exact commit and said so. Stay quiet until main moves.
  if [ "$(cat "$STATE_DIR/last-failed" 2>/dev/null || true)" = "$TARGET" ]; then
    write_metrics 1 0 1
    ping_hc 0
    exit 0
  fi

  # --- is the tree one to deploy from? -------------------------------------------
  _branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo "(detached)")
  [ "$_branch" = "$BRANCH" ] \
    || refuse "$TARGET" "The box is on \`$_branch\`, not \`$BRANCH\`."
  _dirty=$(git status --porcelain --untracked-files=no 2>/dev/null || true)
  [ -z "$_dirty" ] \
    || refuse "$TARGET" "The working tree has uncommitted changes:
\`\`\`
$(printf '%s\n' "$_dirty" | head -n 10)
\`\`\`
Something was edited on the box that should have been a commit."
  git merge-base --is-ancestor HEAD "$TARGET" \
    || refuse "$TARGET" "\`$BRANCH\` on the box has commits GitHub does not, so this is not a fast-forward."

  # An untracked EMPTY file in the way of a file the merge adds is a container's
  # skeleton (Homepage writes custom.css and custom.js on start), not someone's
  # work. Cleared, exactly as repo-sync does; anything with a byte in it still
  # stops the pull.
  for f in $(git diff --name-only --diff-filter=A HEAD "$TARGET" 2>/dev/null || true); do
    [ -e "$f" ] && [ ! -s "$f" ] || continue
    git ls-files --error-unmatch -- "$f" >/dev/null 2>&1 && continue
    rm -f "$f"
    log "cleared empty untracked $f (a container's skeleton, in the way of the merge)"
  done

  if ! MERGE_ERR=$(git merge --ff-only --quiet "$TARGET" 2>&1); then
    refuse "$TARGET" "\`git merge --ff-only\` failed:
\`\`\`
$(printf '%s\n' "$MERGE_ERR" | tail -n 5)
\`\`\`"
  fi

  # The plan covers everything since the last commit that deployed cleanly, so a
  # stack that failed last time is retried now. With no record yet, it is the
  # commit the box was on before this pull.
  BASE=$(cat "$STATE_DIR/last-good" 2>/dev/null || true)
  if [ -z "$BASE" ] || ! git merge-base --is-ancestor "$BASE" HEAD 2>/dev/null; then
    BASE=$HEAD_SHA
  fi

  # Run the copy of this script that was just pulled, so a merge that changes
  # the deploy logic is deployed BY the new logic, with the new deploy-plan.sh
  # and deploy-common.sh it was written against.
  exec "$REPO/scripts/auto-deploy.sh" --after-pull "$BASE"
fi

# ================================================================================
# Phase 2: deploy BASE..HEAD. Runs as the freshly pulled script, lock still held.
# ================================================================================
BASE=${2:?usage: auto-deploy.sh --after-pull <base>}
cd "$REPO"
HEAD_SHA=$(git rev-parse HEAD)
SHORT=$(git rev-parse --short HEAD)

if ! PLAN=$("$REPO/scripts/deploy-plan.sh" --machine "$BASE" 2>&1); then
  printf '%s\n' "$HEAD_SHA" > "$STATE_DIR/last-failed"
  rm -f "$STATE_DIR/resumed"
  post "❌ Auto-deploy failed · $(hostname)" \
"\`$SHORT\` was pulled, but \`deploy-plan.sh\` failed, so nothing was restarted.
\`\`\`
$(printf '%s\n' "$PLAN" | tail -n 5)
\`\`\`
\`\`\`
$(manual_line)
\`\`\`" "$RED"
  write_metrics 0 0 1
  ping_hc 0
  exit 1
fi

DEPLOYED=""   # what ran and verified
MANUAL=""     # needs a person, with the line to run
FAILED=""     # ran and did not come back, or did not run
TOUCHED=""    # stacks this run restarted, for the stale-mount sweep and verify
WANT=""       # "<stack>=<containers running before>" for verify

add() { # <var> <entry>: one bullet, which may run over several lines
  _entry="• $2"
  eval "_cur=\${$1}"
  if [ -n "$_cur" ]; then eval "$1=\"\${$1}
\$_entry\""; else eval "$1=\$_entry"; fi
}

# missing_env_keys <stack>: keys this range ADDED to .env.example that .env
# lacks. Names only, never values. Only new keys: plenty of keys in an
# .env.example are optional and blank on purpose (a Healthchecks URL not created
# yet), and flagging those would skip the stack on every merge forever.
#
# A key that blocked a stack once stays owed in env-pending/<stack> until .env
# has it. Without that, the next merge's range would no longer contain the line
# that added it, and the stack would deploy without a key it needs.
missing_env_keys() {
  _ex="docker/$1/.env.example"
  _owed="$STATE_DIR/env-pending/$1"
  [ -f "$_ex" ] || { rm -f "$_owed"; return 0; }
  [ -f "docker/$1/.env" ] || { echo "(no .env — not set up on this box)"; return 0; }
  _miss=""
  for _k in $( { git diff "$BASE" HEAD -- "$_ex" 2>/dev/null \
                   | sed -n 's/^+[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'
                 cat "$_owed" 2>/dev/null || true; } | sort -u); do
    # A key since removed from .env.example is no longer owed.
    grep -qE "^[[:space:]]*$_k=" "$_ex" || continue
    grep -qE "^[[:space:]]*(export[[:space:]]+)?$_k=" "docker/$1/.env" || _miss="$_miss $_k"
  done
  if [ -n "$_miss" ]; then
    mkdir -p "$STATE_DIR/env-pending"
    printf '%s\n' $_miss > "$_owed"
    printf '%s ' $_miss
  else
    rm -f "$_owed"
  fi
}

# may_deploy <stack> <what>: the policy, in one place. Prints nothing and
# returns 0 if the stack may be touched unattended; otherwise records why under
# MANUAL and returns 1.
may_deploy() {
  _s=$1; _what=$2
  if in_list "$_s" "$NO_AUTOHEAL"; then
    add MANUAL "**$_s** ($_what) is never restarted unattended:
\`$(manual_line "$_s")\`"
    return 1
  fi
  _abs="$REPO/docker/$_s"
  if job_only "docker/$_s/docker-compose.yml"; then return 1; fi
  if [ -z "$(running_ids "$_abs")" ]; then
    add MANUAL "**$_s** ($_what) is not running here, so it was left stopped:
\`$(manual_line "$_s")\`"
    return 1
  fi
  _miss=$(missing_env_keys "$_s")
  if [ -n "$_miss" ]; then
    add MANUAL "**$_s** ($_what) needs \`.env\` first: ${_miss% }
then \`$(manual_line "$_s")\`"
    return 1
  fi
  return 0
}

compose_run() { # <stack> <args...>
  _s=$1; shift
  # </dev/null: this runs inside a `while read` over the plan, and anything that
  # reads stdin would swallow the rest of it.
  docker compose -f "docker/$_s/docker-compose.yml" "$@" </dev/null 2>&1
}

note_touched() { # <stack>
  in_list "$1" "$TOUCHED" && return 0
  TOUCHED="$TOUCHED $1"
  WANT="$WANT $1=$(running_ids "$REPO/docker/$1" | wc -l | tr -d ' ')"
}

# --- keys owed --------------------------------------------------------------------
# A merge that only adds a key to .env.example plans nothing (deploy-plan ignores
# *.example), so nothing above would ever look at it, and the NEXT merge to use
# that key would no longer contain the line that added it. Record what each such
# stack is owed now, and say so if the stack is not otherwise in this plan.
for _ex in $(git diff --name-only "$BASE" HEAD -- 'docker/*/.env.example' 2>/dev/null || true); do
  _s=$(basename "$(dirname "$_ex")")
  _miss=$(missing_env_keys "$_s")
  [ -n "$_miss" ] || continue
  printf '%s\n' "$PLAN" | grep -qE "^[a-z]+ $_s( |$)" && continue
  add MANUAL "**$_s** needs \`.env\` keys before its next deploy: ${_miss% }"
done

# --- act on the plan, in the plan's order ---------------------------------------
# deploy-plan emits stack bring-ups first, then startup-only restarts, then cron,
# then the lines for a person — the same order /deploy runs them in.
PLAN_FILE="$STATE_DIR/.plan.$$"
printf '%s\n' "$PLAN" > "$PLAN_FILE"
trap 'rm -f "$PLAN_FILE"' EXIT
while read -r action stack arg; do
  line="$action${stack:+ $stack}${arg:+ $arg}"
  case "$action" in
    up|build)
      may_deploy "$stack" "$action" || continue
      note_touched "$stack"
      if [ "$action" = build ]; then
        out=$(compose_run "$stack" up -d --build) || { add FAILED "**$stack**: \`docker compose up -d --build\` failed — the previous containers are likely still up
\`$(printf '%s' "$out" | tail -n 2 | tr '\n' ' ')\`"; continue; }
      else
        out=$(compose_run "$stack" up -d) || { add FAILED "**$stack**: \`docker compose up -d\` failed — the previous containers are likely still up
\`$(printf '%s' "$out" | tail -n 2 | tr '\n' ' ')\`"; continue; }
      fi
      add DEPLOYED "$stack ($action)"
      ;;
    restart)
      may_deploy "$stack" "restart $arg" || continue
      note_touched "$stack"
      out=$(compose_run "$stack" restart "$arg") || { add FAILED "**$stack**: \`docker compose restart $arg\` failed
\`$(printf '%s' "$out" | tail -n 2 | tr '\n' ' ')\`"; continue; }
      add DEPLOYED "$stack (restart $arg)"
      ;;
    recreate)
      may_deploy "$stack" "recreate" || continue
      note_touched "$stack"
      out=$(compose_run "$stack" up -d --force-recreate) || { add FAILED "**$stack**: \`docker compose up -d --force-recreate\` failed
\`$(printf '%s' "$out" | tail -n 2 | tr '\n' ' ')\`"; continue; }
      add DEPLOYED "$stack (recreate)"
      ;;
    cron)
      if out=$("$REPO/scripts/install-cron.sh" </dev/null 2>&1); then
        _new=$(printf '%s\n' "$out" | grep -E '^(added|fixed|created) ' | sed 's/  */ /g' || true)
        if [ -n "$_new" ]; then add DEPLOYED "cron: $(printf '%s' "$_new" | tr '\n' ';')"; fi
      else
        add FAILED "**install-cron.sh** failed: \`$(printf '%s' "$out" | tail -n 2 | tr '\n' ' ')\`"
      fi
      ;;
    manual)
      case "$stack" in
        proxy) add MANUAL "**proxy** changed (AdGuard and Caddy) and is never deployed unattended. Prove something is broken first (/deploy \"Safety\"), then:
\`$(manual_line proxy)\`" ;;
        host)  add MANUAL "**scripts/host** changed, and the Proxmox host runs its own copy. From the Mac: \`cd ansible && ansible-playbook site.yml --limit proxmox\`" ;;
        *)     add MANUAL "$line" ;;
      esac
      ;;
    deletion)
      add MANUAL "\`$stack\` drops \`$arg\` with nothing left naming it. Grafana keeps it until a \`deleteRules:\`/\`deleteContactPoints:\` entry removes it"
      ;;
    "") ;;
    *)
      add FAILED "deploy-plan.sh printed a line this script does not know: \`$line\`. Nothing was done for it"
      ;;
  esac
done < "$PLAN_FILE"
rm -f "$PLAN_FILE"

# --- containers still reading a replaced file -------------------------------------
# /deploy step 4. git replaces files rather than editing them, so a single-file
# bind mount keeps serving the old, unlinked copy after a pull, and `up -d` sees
# nothing to do. Read through /proc/<pid>/root (never `docker cp`, which re-reads
# the host file and cannot fail) and recreate just that service. Only for stacks
# this run was allowed to touch; anything else is reported.
for c in $(docker ps --format '{{.Names}}' 2>/dev/null || true); do
  wd=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c" 2>/dev/null || true)
  case "$wd" in "$REPO"/docker/*) ;; *) continue ;; esac
  stack=$(basename "$wd")
  in_list "$stack" "$TOUCHED" || continue
  pid=$(docker inspect -f '{{.State.Pid}}' "$c" 2>/dev/null || echo 0)
  [ "$pid" -gt 0 ] 2>/dev/null || continue
  stale=""
  for m in $(docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}{{end}}' "$c" 2>/dev/null || true); do
    src=${m%%|*}; dest=${m#*|}
    case "$src" in "$REPO"/*) ;; *) continue ;; esac
    [ -f "$src" ] || continue
    seen="/proc/$pid/root$dest"
    [ -r "$seen" ] || continue
    cmp -s "$src" "$seen" || stale="$stale ${src#"$REPO"/}"
  done
  [ -n "$stale" ] || continue
  svc=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$c" 2>/dev/null || true)
  # Gone since `docker ps` (Sablier scaled it down, or a recreate above replaced it).
  [ -n "$svc" ] || continue
  if out=$(compose_run "$stack" up -d --force-recreate "$svc" </dev/null); then
    add DEPLOYED "$stack: recreated $svc for stale${stale}"
  else
    add FAILED "**$stack**: recreating $svc for stale${stale} failed
\`$(printf '%s' "$out" | tail -n 2 | tr '\n' ' ')\`"
  fi
done

# --- verify ---------------------------------------------------------------------
for s in $TOUCHED; do
  want=0
  for kv in $WANT; do [ "${kv%%=*}" = "$s" ] && want=${kv#*=}; done
  if ! why=$(verify_stack "$REPO/docker/$s" "$want"); then
    add FAILED "**$s** did not come back: $why
\`docker compose -f docker/$s/docker-compose.yml ps\`, then \`$(manual_line "$s")\`"
  fi
done

# --- record and report -----------------------------------------------------------
# Reaching here means Phase 2 finished, whatever it found: the one resume a commit
# is allowed (Phase 1) is not needed again.
rm -f "$STATE_DIR/resumed"
if [ -n "$FAILED" ]; then
  printf '%s\n' "$HEAD_SHA" > "$STATE_DIR/last-failed"
else
  rm -f "$STATE_DIR/last-failed"
  printf '%s\n' "$HEAD_SHA" > "$STATE_DIR/last-good"
  date +%s > "$STATE_DIR/last-good-ts"
fi

log "planned $(git rev-parse --short "$BASE")..$SHORT — restarted:${TOUCHED:- nothing}"
[ -z "$FAILED" ] || log "failed: $(printf '%s' "$FAILED" | tr '\n' ' ')"

# Nothing ran and nothing needs anyone: a docs-only merge. Say nothing.
if [ -z "$DEPLOYED" ] && [ -z "$MANUAL" ] && [ -z "$FAILED" ]; then
  write_metrics 0 0 0
  ping_hc 0
  exit 0
fi

COMMITS=$(git log --format='`%h` %s' "$BASE..HEAD" 2>/dev/null | head -n 10)
MSG="$COMMITS"
[ -z "$FAILED" ]   || MSG="$MSG

**Failed**
$FAILED"
[ -z "$DEPLOYED" ] || MSG="$MSG

**Deployed**
$DEPLOYED"
[ -z "$MANUAL" ]   || MSG="$MSG

**Needs you**
$MANUAL"

if [ -n "$FAILED" ]; then
  post "❌ Auto-deploy $SHORT failed verify · $(hostname)" "$MSG

Not retried until the next merge or a manual /deploy." "$RED"
  write_metrics 0 0 1
  ping_hc 0
  exit 1
elif [ -n "$MANUAL" ]; then
  post "⚠️ Auto-deploy $SHORT · needs you · $(hostname)" "$MSG" "$AMBER"
else
  post "✅ Auto-deploy $SHORT · verified · $(hostname)" "$MSG" "$GREEN"
fi
write_metrics 0 0 0
ping_hc 0
