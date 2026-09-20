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
#       start time. These read their config from the repo via bind mounts, so
#       replacing the process is what picks a change up.
#
# What it does about a stale stack is `--force-recreate`, on both branches, and
# that flag is load-bearing rather than belt-and-braces: every stack here keeps
# its config in a bind mount, and a change inside a mount is invisible to the
# hash Compose decides recreates by. Without it `up -d` exits 0 having replaced
# nothing. See the note at the staleness loop — the version of this script that
# shipped without the flag reported a heal on every run and performed none.
#
# It is a heuristic, not proof: a stack rebuilt for an unrelated reason reads as
# fresh. It is right about the case that actually happens — you pulled, and
# forgot to restart.
#
# The second axis: is the image itself old
# ----------------------------------------
# Everything above measures the deployment against the REPO, which says nothing
# about whether the repo is asking for a current image. A stack in perfect sync
# with git, running something upstream built a year ago, passes every check
# above. That is how every image in this lab reached 3-23 months old without a
# word being said — see the block above the staleness loop for the full story,
# and docs/design/tsd-dependency-updates.md for the design.
#
# So a second pass reports any running image older than HL_MAX_IMAGE_AGE_DAYS
# (default 90).
#
# Who fixes it, per stack
# -----------------------
# Reporting is the floor, not the whole answer. Where a bad version is cheap,
# this job now also PULLS — see the image-pull section. Where it is not
# (HL_NO_AUTOPULL: proxy, core, monitoring) the report is all you get, and a
# person decides; `proxy` additionally has Renovate opening PRs for it, see
# renovate.json5. Every automatic pull writes the digest it is replacing to
# HL_DIGEST_LOG first, because a floating tag cannot be rolled back to.
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
# The report goes out as an embed, whose description caps at 4096 characters —
# content would have capped at 2000. Leave room for the code fences.
MAX_CHARS=3800

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$(dirname "$0")/healthchecks.sh"

# Stacks never restarted automatically — see "Self-healing" above.
NO_AUTOHEAL="${HL_NO_AUTOHEAL:-proxy}"
# Set HL_AUTOHEAL=no for report-only behaviour.
AUTOHEAL="${HL_AUTOHEAL:-yes}"
# Verification after a restart: attempts x delay. Compose needs a few seconds to
# settle, and a container that crashes on boot usually does so within one cycle.
VERIFY_TRIES="${HL_VERIFY_TRIES:-3}"
VERIFY_DELAY="${HL_VERIFY_DELAY:-10}"

# Reclaiming what the pulls and rebuilds below replace — see "Reclaim".
PRUNE="${HL_PRUNE:-yes}"
BUILD_CACHE_KEEP_HOURS="${HL_BUILD_CACHE_KEEP_HOURS:-168}"

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
# A placeholder counts as unset, and HC_REASON says which it was — the report
# below repeats it, so the fix is named in the channel rather than inferred.
if hc_url HEALTHCHECKS_REPO_SYNC_URL "$ENV_FILE"; then HC_URL=$HC_VALUE; else HC_URL=""; fi

ping_healthchecks() {
  _code=$?
  if [ -n "$HC_URL" ]; then
    # /<exit code>: 0 records a success and anything else a failure, so "ran and
    # failed" stays distinguishable from "never ran".
    #
    # `|| true` because this runs in an EXIT trap and the sync's own verdict is
    # already decided; hc_ping reports a failed ping itself.
    hc_ping "$HC_URL/$_code" repo-sync || true
  else
    hc_unarmed repo-sync
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
# Kept as two values rather than one sentence: the count goes in the report's
# headline, the revisions in the same line as a short range, and the count is
# what decides whether the line appears at all.
PULL_COUNT=""
PULL_RANGE=""
if [ "$BEFORE" != "$AFTER" ]; then
  PULL_COUNT=$(git rev-list --count "$BEFORE..$AFTER")
  PULL_RANGE="$(git rev-parse --short "$BEFORE") → $(git rev-parse --short "$AFTER")"
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

# --- image pull ---------------------------------------------------------------
#
# Currency without a human in the loop, for the stacks where a bad version is
# cheap. `docker compose up -d` does not pull — Compose's default pull policy is
# `missing`, so it finds the tag on disk and stops — which is why `:latest` in
# this lab produced the appearance of currency and three to nine months of
# drift. This asks for the new image explicitly.
#
# Which stacks, and why the list is what it is
# --------------------------------------------
# HL_NO_AUTOPULL is the same judgement as HL_NO_AUTOHEAL, applied to versions
# rather than restarts, and it starts wider than the TSD first proposed:
#
#   proxy       AdGuard is the household's resolver and Caddy holds every
#               certificate. Already never auto-restarted; it is not going to be
#               auto-upgraded either. Renovate opens PRs for it instead
#               (renovate.json5) and a person runs the rebuild.
#
#   core        Portainer's database migrations are one-way. Once a newer
#               version has opened that volume, the digest you wrote down above
#               will not take you back, so "revert the tag" is not a rollback
#               here. Postgres is in this stack too, and a patch bump means a
#               database restart.
#
#   monitoring  The one the evidence changed. The TSD argued these stacks "fail
#               visibly and recover cheaply" — and this repo already contains
#               the counter-example. Grafana 11 disabled Angular panels and
#               Grafana 12 removed them, which blanked 10 of 11 panels on one
#               dashboard and 32 of 35 on another, for months, on some ordinary
#               restart of a `:latest` image. Nothing errored. A blank dashboard
#               reads like a quiet lab. That is invisible breakage, which is the
#               failure this whole design exists to prevent, so monitoring does
#               not get automatic major versions. Pinning Grafana to a major
#               would let it back in; see the TSD.
#
# Everything else — ai, mcp, dashboard, desktops — pulls. Two of those are
# version-pinned already, so a pull is a no-op for them until the pin moves,
# which is the point: this is a mechanism, not a gamble.
#
# Stacks that build their own image are skipped entirely: there is no upstream
# image to fetch, and `--pull` on their rebuild (below) refreshes the base.
NO_AUTOPULL="${HL_NO_AUTOPULL:-proxy core monitoring}"
AUTOPULL="${HL_AUTOPULL:-yes}"

# Rollback insurance. Before anything is replaced, what is running is written
# down as a pullable digest — because a floating tag cannot be rolled back to,
# only forward. Degrades to a no-op on an unwritable path, the same discipline
# metrics.sh uses: failing to record a digest must never fail the sync.
DIGEST_LOG="${HL_DIGEST_LOG:-/var/lib/homelab/image-digests.log}"

# What each tag USED BY a running container resolves to right now. Compared
# either side of the pull: the container keeps running the old image until it is
# recreated, so the container's own image ID cannot tell you a new one arrived —
# only the tag's can.
tag_ids() {
  for _c in $(running_ids "$1"); do
    _n=$(docker inspect -f '{{.Config.Image}}' "$_c" 2>/dev/null || true)
    [ -n "$_n" ] || continue
    printf '%s %s\n' "$_n" "$(docker image inspect -f '{{.Id}}' "$_n" 2>/dev/null || true)"
  done | sort -u
}

record_digests() {
  [ -n "$DIGEST_LOG" ] || return 0
  _d=$(dirname "$DIGEST_LOG")
  [ -d "$_d" ] || mkdir -p "$_d" 2>/dev/null || return 0
  _ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  for _c in $(running_ids "$1"); do
    _n=$(docker inspect -f '{{.Config.Image}}' "$_c" 2>/dev/null || true)
    [ -n "$_n" ] || continue
    # RepoDigests is what `docker pull` can take back; a locally-built image has
    # none, so its ID is recorded instead and the note says rebuild, not pull.
    _rd=$(docker image inspect \
      -f '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}} (local build){{end}}' \
      "$_n" 2>/dev/null || true)
    printf '%s\t%s\t%s\t%s\n' "$_ts" "$2" "$_n" "$_rd" >> "$DIGEST_LOG" 2>/dev/null || true
  done
}

PULLED_NEW=""
PULL_STACK_FAILED=""

for dir in docker/*/; do
  stack=$(basename "$dir")
  compose="${dir}docker-compose.yml"
  [ -f "$compose" ] || continue

  # `if`, not `&&`: under `set -e` an AND-list whose test fails takes the whole
  # script down, and the test failing is the normal case. Same reason the report
  # section below is built the way it is.
  if [ "$AUTOPULL" != "yes" ] || [ -n "$PULL_ERROR" ]; then continue; fi
  if in_list "$stack" "$NO_AUTOPULL"; then continue; fi
  if grep -qE '^[[:space:]]+build:' "$compose"; then continue; fi

  abs=$(cd "$dir" && pwd)
  cids=$(running_ids "$abs")
  [ -n "$cids" ] || continue

  before=$(tag_ids "$abs")
  record_digests "$abs" "$stack"

  if ! err=$(cd "$REPO" && docker compose -f "$compose" pull -q 2>&1); then
    PULL_STACK_FAILED="$PULL_STACK_FAILED
  $stack — pull failed, still on the old image
    $(printf '%s' "$err" | tail -n 2 | tr '\n' ' ')"
    continue
  fi

  after=$(tag_ids "$abs")
  [ "$before" != "$after" ] || continue

  # Only the tags that actually moved, so the report names images rather than
  # just stacks. Done with a loop rather than `comm <(...)`: process
  # substitution is a bashism and this runs under /bin/sh.
  changed=""
  for _t in $(printf '%s\n' "$after" | awk '{print $1}'); do
    _a=$(printf '%s\n' "$after"  | awk -v t="$_t" '$1 == t { print $2 }')
    _b=$(printf '%s\n' "$before" | awk -v t="$_t" '$1 == t { print $2 }')
    # `if`, not `&&`, for the set -e reason above: a loop whose last iteration
    # ends in a failed test exits the script.
    if [ "$_a" != "$_b" ]; then changed="$changed $_t"; fi
  done
  [ -n "$changed" ] || continue

  want=$(printf '%s\n' "$cids" | wc -l | tr -d ' ')
  if ! err=$(cd "$REPO" && docker compose -f "$compose" up -d 2>&1); then
    PULL_STACK_FAILED="$PULL_STACK_FAILED
  $stack — new image pulled but recreate failed; previous containers likely still up
    $(printf '%s' "$err" | tail -n 2 | tr '\n' ' ')"
    continue
  fi
  if ! why=$(verify_stack "$abs" "$want"); then
    PULL_STACK_FAILED="$PULL_STACK_FAILED
  $stack — updated but did NOT come back: $why
    roll back with the last digests in $DIGEST_LOG"
    continue
  fi
  PULLED_NEW="$PULLED_NEW
  $stack: $changed"
done

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

# Drift is recorded at every exit from the staleness loop rather than at the
# point of measurement, because the measurement is not the answer yet — a
# verified heal a few lines later resets the clock the number describes.
#
# Publishing it where it was measured is what kept `hl-stack-drift` firing over
# stacks that had already been fixed. The loop measured 52 days, healed the
# stack, said so in the report, and then wrote the 52 days it had just
# invalidated. Textfile metrics persist until their writer runs again and this
# writer is daily, so that dead number stood for another 24 hours — long enough
# to fire a `for: 30m` alert, be read on a phone, and send someone looking for a
# fault that no longer existed. A stale-until-tomorrow gauge next to a nightly
# "healed" line is the metric contradicting the report, and the metric was the
# one that got believed.
record_drift() {
  M_STACK_DRIFT="$M_STACK_DRIFT
homelab_stack_deploy_drift_seconds{$(metric_kv stack "$1"),$(metric_kv basis "$2")} $3"
}

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
  #
  # `--pull always` because `docker build` caches base images exactly the way
  # `up -d` caches service images: `FROM caddy:2-alpine` is served from disk
  # unless told otherwise. On 2026-09-19 caddy-sablier:local was 22 minutes old
  # on a three-month-old Caddy, and this script called it fresh — it measures
  # built stacks by image creation time, so a rebuild resets the clock while the
  # base underneath keeps ageing. Without this the staleness check below is blind
  # in the same place, since rebuilding is what it asks for.
  #
  # `always`, and the argument is not optional: `docker compose build --pull` is
  # a boolean, but `docker compose up --pull` takes always|missing|never. Bare
  # `--pull` on `up` fails with "flag needs an argument" — which this script
  # shipped, and which would have failed every auto-rebuild of `assistant` (the
  # other stack with a `build:` key, and unlike `proxy` not on HL_NO_AUTOHEAL).
  #
  # `--force-recreate` on BOTH, and without it this whole loop was a no-op that
  # reported success. Compose decides whether to replace a container by hashing
  # the SERVICE definition — image, env, ports, the mount's source and target.
  # The bytes *behind* a mount are not in that hash and cannot be. Every stack
  # here keeps its config in a bind mount, so the single most common change is
  # the one Compose is structurally unable to notice:
  #
  #   dashboard   ./config, ./icons, ./assets — image pinned, so nothing in a
  #               deploy ever changes the service definition.
  #   proxy       ./Caddyfile, which the Dockerfile does not COPY. A rebuild
  #               with every layer cached produces the same image id, so
  #               `--build` does not rescue it either.
  #
  # `up -d` then finds nothing to do and exits 0 with the old process still
  # running. `verify_stack` counts containers that never stopped and calls it
  # healed, `StartedAt` never moves, the stack is flagged stale again tomorrow,
  # and `homelab_stack_deploy_drift_seconds` climbs next to a success line. A
  # heal loop that had never once healed anything.
  #
  # That is not merely untidy. Homepage enumerates /app/public only at startup
  # (see docker/dashboard/docker-compose.yml), so "the container was never
  # replaced" is the difference between the dashboard's mark being served and
  # being a 404 — which it silently was, from the day it was drawn until this
  # was found. The same applies to the report-only path: `proxy` is on
  # HL_NO_AUTOHEAL, so the command printed for a human to run has to work too.
  #
  # Recreating when nothing needed it is not a cost worth guarding against:
  # this line is only reached for a stack already measured as stale.
  if grep -qE '^[[:space:]]+build:' "$compose"; then
    basis="image"; hint="up -d --build --pull always --force-recreate"
  else
    basis="start"; hint="up -d --force-recreate"
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

  # Recorded for every stack, including the ones that are in sync — a gauge that
  # only appears when something is wrong cannot be graphed, and "0 for the last
  # 30 days" is the reassurance the panel exists to give.
  if [ "$commit_ts" -le "$oldest" ]; then
    record_drift "$stack" "$basis" 0
    continue
  fi
  drift=$((commit_ts - oldest))
  age=$(human "$drift")
  cmd="docker compose -f $compose $hint"

  # Report-only: globally disabled, on the never-touch list, or the tree is in
  # an unknown state because the pull failed.
  if [ "$AUTOHEAL" != "yes" ] || in_list "$stack" "$NO_AUTOHEAL" || [ -n "$PULL_ERROR" ]; then
    record_drift "$stack" "$basis" "$drift"
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
    record_drift "$stack" "$basis" "$drift"
    FAILED="$FAILED
  $stack ($basis, $age) — command failed, previous containers likely still up
    $(printf '%s' "$err" | tail -n 2 | tr '\n' ' ')"
    continue
  fi

  if ! why=$(verify_stack "$abs" "$want"); then
    record_drift "$stack" "$basis" "$drift"
    FAILED="$FAILED
  $stack ($basis, $age) — restarted but did NOT come back: $why
    $cmd"
    continue
  fi
  # Zero, and not a re-measure: `verify_stack` has just confirmed the containers
  # this drift was measured against are gone and their replacements are up, and
  # a container that started moments ago cannot be behind a commit that is
  # already in the tree. Asking Docker again would cost a round-trip per service
  # to arrive at the same 0 the next run computes.
  record_drift "$stack" "$basis" 0
  HEALED="$HEALED
  $stack ($basis, $age)"
done

# --- reclaim ------------------------------------------------------------------
#
# Every pull above and every rebuild above leaves the image it replaced on disk.
# A floating tag that moves does not delete what it moved off — the old layers
# stay, untagged and unreferenced, forever. Nothing in this repo has ever
# removed one.
#
# That was harmless while nothing pulled. Auto-pull landed on 2026-09-19 and
# turned it into a nightly ratchet: four stacks pull, two rebuild with
# `--pull always`, and the superseded copy of each is left behind every time.
# The images involved are not small (the Kali webtop and Ollama are GB-scale),
# and CT 100's rootfs is 400 GB on a thin pool that tops out around 348 GiB and
# cannot auto-extend — so the guest's own free-space number is optimistic about
# the only ceiling that matters. `hl-disk-filling` firing four days out is the
# alert that catches this; reclaiming is what stops it recurring.
#
# `image prune` WITHOUT `-a`, and the distinction is the whole safety argument.
# Bare `prune` removes dangling images only: untagged, and referenced by no
# container, running or stopped. That is exactly the set a `pull` creates and
# nothing else. `-a` would also take any tagged image with no container on it —
# which here means the Kali webtop, since Sablier's entire job is to scale it to
# zero. It would be deleted every night and re-pulled on the next visit, turning
# an on-demand desktop into a multi-GB download.
#
# Build cache is kept for a week rather than dropped: `assistant` and `proxy`
# rebuild here, and a cold cache turns a 20-second rebuild into a full one on
# the nightly run that is meant to be cheap.
#
# Both are best-effort. A prune that fails must not fail the sync — the stacks
# are already deployed by this point, and disk that was not reclaimed tonight is
# a thing the next run and the disk alerts both still catch.
RECLAIMED=""

# `docker prune` ends with "Total reclaimed space: 1.234GB". Empty on anything
# unexpected, which reads as "reclaimed nothing" and says nothing — the right
# failure for a cleanup step that is not allowed to be load-bearing.
reclaimed_from() {
  printf '%s\n' "$1" | awk '
    /Total reclaimed space:/ {
      sub(/^.*Total reclaimed space:[[:space:]]*/, "")
      print; exit
    }'
}

if [ "$PRUNE" = "yes" ]; then
  _img=$(reclaimed_from "$(docker image prune -f 2>/dev/null || true)")
  _bld=$(reclaimed_from "$(docker builder prune -f --filter "until=${BUILD_CACHE_KEEP_HOURS}h" 2>/dev/null || true)")
  # "0B" is the ordinary result on a night with no updates, and a report line
  # saying so every day is how a channel teaches you to stop reading it.
  case "$_img" in ""|0B|0b) _img="" ;; esac
  case "$_bld" in ""|0B|0b) _bld="" ;; esac
  if [ -n "$_img" ]; then
    RECLAIMED="$RECLAIMED
  $_img — image layers the pulls replaced"
  fi
  if [ -n "$_bld" ]; then
    RECLAIMED="$RECLAIMED
  $_bld — build cache older than ${BUILD_CACHE_KEEP_HOURS}h"
  fi
fi

# --- image staleness ----------------------------------------------------------
#
# The second drift axis, and the one the first could never have caught.
#
# Everything above asks "is the running container older than the commit". A
# stack that matches git perfectly, running an image upstream built in January,
# is reported as healthy by every check in this script — correctly, by its own
# definition, and uselessly. On 2026-09-19 every image in this lab was between
# 3 and 23 months old and nothing had ever mentioned it.
#
# The two worst were the two that were PINNED: sablier at 1.8.1 (23 months) and
# homepage at v1.5.0 (12 months). Floating tags drifted because Compose's
# default pull policy is `missing` and nothing here runs `docker compose pull`;
# pinned tags drifted because nothing opened the PRs. Opposite mechanisms, same
# root — no signal existed that would ever have said so. Same failure as the
# cron job that was never installed, and it gets the same fix: make silence the
# alarm.
#
# Age, not availability, deliberately. Asking a registry what is current needs
# network, credentials for some of them, and fails per-registry; creation time
# is already in the local metadata, always answers, and still works on the day
# the internet is what broke. The cost is a false positive on a project that
# genuinely has not shipped in 90 days. The error in the other direction is the
# one that actually happened.
#
# Locally-built images are included rather than exempted: their age IS the build
# time, so a stack nobody has rebuilt in 90 days is exactly what wants saying,
# and `--pull` above makes the rebuild it asks for refresh the base too.
#
# REPORT ONLY, and that is a boundary rather than an unfinished edge. Restarting
# a stale stack is safe — the image is on disk and CI gated the config. Pulling
# a new one is a version change nobody reviewed, landing at 4am on the box that
# serves the household's DNS. That decision belongs in a PR.
# See docs/design/tsd-dependency-updates.md.
MAX_IMAGE_AGE_DAYS="${HL_MAX_IMAGE_AGE_DAYS:-90}"
STALE=""
M_IMAGE_AGE=""
STALE_CMDS=""
NOW=$(date +%s)

for dir in docker/*/; do
  stack=$(basename "$dir")
  compose="${dir}docker-compose.yml"
  [ -f "$compose" ] || continue

  abs=$(cd "$dir" && pwd)
  cids=$(running_ids "$abs")
  # Nothing running is already reported by the drift loop as NOT_RUNNING; a
  # stopped stack has no image age worth acting on.
  [ -n "$cids" ] || continue

  # Report per distinct image, not per container: one line per thing to update.
  seen=""
  hits=""
  # The compose SERVICES behind those images, which is what the command needs —
  # collected per container rather than per image, because two services can run
  # the same image and updating only the first leaves the other behind.
  stale_svcs=""
  for cid in $cids; do
    name=$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true)
    [ -n "$name" ] || continue

    # The image the container is ACTUALLY running, by ID. Going via the tag
    # would read whatever that tag points at now, which for `:latest` is a
    # different image the moment anything pulls.
    img=$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true)
    [ -n "$img" ] || continue
    ts=$(epoch "$(docker image inspect -f '{{.Created}}' "$img" 2>/dev/null || true)")
    [ -n "$ts" ] || continue
    age_days=$(( (NOW - ts) / 86400 ))

    # POSIX-safe dedupe — no arrays, and the spaces make it a whole-word match.
    case " $seen " in
      *" $name "*) ;;
      *)
        seen="$seen $name"
        # Emitted for every image, not just the stale ones: a gauge that appears
        # only when something is wrong cannot be graphed, and the flat line is
        # the reassurance. Same reasoning as the deploy-drift gauge above.
        M_IMAGE_AGE="$M_IMAGE_AGE
homelab_image_age_seconds{$(metric_kv stack "$stack"),$(metric_kv image "$name")} $((NOW - ts))"
        if [ "$age_days" -ge "$MAX_IMAGE_AGE_DAYS" ]; then
          hits="$hits
    $name ($(human $((NOW - ts))))"
        fi
        ;;
    esac

    [ "$age_days" -ge "$MAX_IMAGE_AGE_DAYS" ] || continue
    svc=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' \
      "$cid" 2>/dev/null || true)
    [ -n "$svc" ] || continue
    case " $stale_svcs " in *" $svc "*) continue ;; esac
    stale_svcs="$stale_svcs $svc"
  done

  if [ -n "$hits" ]; then
    # Scoped to the services actually named above, never the whole stack.
    #
    # `monitoring` is the case that forced this. The finding there is cadvisor
    # and blackbox-exporter, but a bare `docker compose pull` on that stack takes
    # grafana and prometheus with it — both on `:latest`, and both on
    # HL_NO_AUTOPULL precisely because an unreviewed Grafana major once blanked
    # 10 of 11 panels on one dashboard and 32 of 35 on another for months. So
    # the report printed, under "review before running", the exact command the
    # rest of this script exists to stop running unattended.
    #
    # A command that does more than the finding it is printed under cannot be
    # pasted without re-deriving its blast radius, which is the work the report
    # was supposed to have done. Naming the services keeps the two in step: what
    # it says is stale is what the command updates.
    #
    # Falls back to the whole stack when no service label came back — containers
    # this script did not see Compose create. `running_ids` filters on
    # `com.docker.compose.project.working_dir`, so that should be unreachable;
    # it degrades to the previous behaviour rather than emitting a command with
    # no services on the end of it, which would mean something different.
    if grep -qE '^[[:space:]]+build:' "$compose"; then
      stale_cmd="docker compose -f $compose up -d --build --pull always$stale_svcs"
    else
      stale_cmd="docker compose -f $compose pull$stale_svcs && docker compose -f $compose up -d$stale_svcs"
    fi
    STALE="$STALE
  $stack:$hits"
    STALE_CMDS="$STALE_CMDS
$stale_cmd"
  fi
done

# --- stack metrics ------------------------------------------------------------
metric_help homelab_stack_running gauge \
  "1 if any container is running for a stack declared in docker/"
metric_raw "$M_STACK_RUNNING"
metric_help homelab_stack_deploy_drift_seconds gauge \
  "Seconds between a stack's newest deployable commit and what is running"
metric_raw "$M_STACK_DRIFT"
metric_help homelab_image_age_seconds gauge \
  "Age in seconds of the image a running container was created from"
metric_raw "$M_IMAGE_AGE"

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
if [ -n "$PULL_ERROR" ] || [ -n "$FAILED" ] || [ -n "$PULL_STACK_FAILED" ]; then
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

# The lists above are built two-space-indented, one entry per line, with
# four-space continuation lines for the "and here is why" detail. They used to
# be printed inside code fences, which preserved that shape and cost a lot to
# read: a fence is monospace, does not wrap, and on a phone turns a list of
# three stack names into a horizontally scrolling grey slab. Bullets wrap.
#
# Discord collapses leading whitespace in ordinary text, so the continuation
# lines get a mark rather than an indent.
as_bullets() {
  printf '%s' "${1:-}" \
    | sed -e '/^[[:space:]]*$/d' -e 's/^    \(.*\)$/↳ \1/' -e 's/^  \(.*\)$/• \1/'
}

# `grep -c` would do this, but it exits 1 when it counts nothing, and under
# `set -e` that takes the script down on the ordinary case.
count_entries() {
  printf '%s' "${1:-}" | awk '/^  [^ ]/ { n++ } END { print n + 0 }'
}

# "1 stack needs you" / "3 stacks need you". Worth the six lines: "1 stack(s)"
# is how a report tells you it was written by a machine that did not care.
plural() { # count singular plural
  if [ "$1" = 1 ]; then printf '%s %s' "$1" "$2"; else printf '%s %s' "$1" "$3"; fi
}

# Sections are joined with blank lines, so the first one arrives with a leading
# gap and the last may leave a trailing one. Discord renders both.
trim_blank_lines() {
  printf '%s' "${1:-}" | awk '
    { line[NR] = $0 }
    END {
      first = 1; while (first <= NR && line[first] ~ /^[[:space:]]*$/) first++
      last = NR;  while (last >= first && line[last] ~ /^[[:space:]]*$/) last--
      for (i = first; i <= last; i++) print line[i]
    }'
}

# The headline: what this run did, in one line, before any detail. Written as
# facts joined by `·` rather than a sentence, because it is read at a glance in
# a channel list.
SUMMARY=""
add_summary() {
  if [ -n "$SUMMARY" ]; then SUMMARY="$SUMMARY · $1"; else SUMMARY="$1"; fi
}

if [ -n "$PULL_ERROR" ]; then
  add_summary "pull failed"
elif [ -n "$PULL_COUNT" ]; then
  add_summary "pulled $(plural "$PULL_COUNT" commit commits) \`$PULL_RANGE\`"
fi
if [ -n "$CLEARED" ]; then
  add_summary "cleared $(plural "$(count_entries "$CLEARED")" "empty file" "empty files")"
fi
if [ -n "$PULLED_NEW" ]; then
  add_summary "$(plural "$(count_entries "$PULLED_NEW")" "image update" "image updates")"
fi
if [ -n "$HEALED" ]; then
  add_summary "restarted $(plural "$(count_entries "$HEALED")" stack stacks)"
fi
if [ -n "$FAILED" ]; then
  add_summary "$(plural "$(count_entries "$FAILED")" "restart failed" "restarts failed")"
fi
if [ -n "$PULL_STACK_FAILED" ]; then
  add_summary "$(plural "$(count_entries "$PULL_STACK_FAILED")" "update failed" "updates failed")"
fi
if [ -n "$MANUAL" ]; then
  add_summary "$(plural "$(count_entries "$MANUAL")" "stack needs you" "stacks need you")"
fi
if [ -n "$RECLAIMED" ]; then
  add_summary "reclaimed disk"
fi
if [ -n "$STALE" ]; then
  add_summary "$(plural "$(count_entries "$STALE")" "stale image" "stale images")"
fi
# The one run that says something without having done anything: the dead man's
# switch nag, which is a standing condition rather than tonight's news.
if [ -z "$SUMMARY" ]; then SUMMARY="nothing to deploy"; fi

# Built with `if` rather than `[ x ] && MSG=...`: under `set -e` an AND-list
# whose test fails takes the whole script down with it, and the test failing is
# the *normal* case here.
#
# One section per outcome, and the commands you still have to run collected into
# a single block at the end rather than repeated after every line — they are all
# the same shape, and a block is one paste instead of three. Fences are kept for
# exactly two things now: raw error output, and commands meant to be copied.
MSG=""
if [ -n "$PULL_ERROR" ]; then
  MSG="$MSG

**Pull failed** — nothing was restarted
\`\`\`
$PULL_ERROR
\`\`\`"
fi
if [ -n "$CLEARED" ]; then
  MSG="$MSG

**Cleared empty files a container had written**, so the pull could apply
$(as_bullets "$CLEARED")"
fi
if [ -n "$PULL_STACK_FAILED" ]; then
  MSG="$MSG

**Image update failed**
$(as_bullets "$PULL_STACK_FAILED")"
fi
if [ -n "$FAILED" ]; then
  MSG="$MSG

**Restart failed — still on old code**
$(as_bullets "$FAILED")"
fi
if [ -n "$PULLED_NEW" ]; then
  # Always reported, never silent: an automatic version change is the one thing
  # here that happened without anyone asking for it, so the channel is also the
  # record of what changed underneath you overnight.
  MSG="$MSG

**Updated to new upstream images**
$(as_bullets "$PULLED_NEW")
*previous digests: \`$DIGEST_LOG\`*"
fi
if [ -n "$HEALED" ]; then
  MSG="$MSG

**Restarted, now running current code**
$(as_bullets "$HEALED")"
fi
if [ -n "$RECLAIMED" ]; then
  # Only ever present when something was actually freed, so this is not a daily
  # line. It will occasionally be the *only* line — the build cache ages past
  # the cutoff on its own schedule, unrelated to whether anything deployed —
  # and that is fine: the channel doubles as the record of what the box did.
  MSG="$MSG

**Reclaimed disk**
$(as_bullets "$RECLAIMED")"
fi
if [ -n "$MANUAL" ]; then
  MSG="$MSG

**Needs you**
$(as_bullets "$MANUAL")
\`\`\`bash
cd $REPO$MANUAL_CMDS
\`\`\`"
fi
# Last on purpose. MAX_CHARS truncates the tail, and this is the only section
# that is slow-burn rather than about today — a 90-day-old image keeps until
# tomorrow, a failed restart does not.
if [ -n "$STALE" ]; then
  MSG="$MSG

**Images older than ${MAX_IMAGE_AGE_DAYS}d**
$(as_bullets "$STALE")
*review before running — this pulls versions nobody has looked at*
\`\`\`bash
cd $REPO$STALE_CMDS
\`\`\`
A pinned tag does not move on a pull, so for those this changes nothing — bump
the tag in the compose file instead. That is the usual case for the oldest
entries here: pinning is what let them get old. The pins are exempt, though:
Renovate opens their bumps as PRs (\`proxy\`, \`dashboard\`, \`desktops\` — see
renovate.json5), so an entry from one of those is waiting on a PR, not on you."
fi
if [ -z "$HC_URL" ]; then
  # Reported every run, deliberately: an unconfigured dead man's switch is
  # exactly the silence this section exists to break, and it stops the moment
  # the variable is set. Last and quiet, because it is a standing condition
  # rather than something that happened tonight.
  MSG="$MSG

*No dead man's switch on this sync — $HC_REASON. Nothing notices when it stops running.*"
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

# Status decides the colour and the leading glyph. Three states, in the order
# they matter: something broke, something wants a person, nothing did either but
# the run still changed the box.
#
# Colours are the design system's semantic tokens, dark-surface step, since a
# Discord embed is read on a dark card by default — see
# docker/dashboard/assets/design-tokens.css.
if [ -n "$PULL_ERROR" ] || [ -n "$FAILED" ] || [ -n "$PULL_STACK_FAILED" ]; then
  STATUS_GLYPH="❌"; COLOR=14711391  # --danger  #e07a5f
elif [ -n "$MANUAL" ] || [ -n "$STALE" ] || [ -z "$HC_URL" ]; then
  STATUS_GLYPH="⚠️"; COLOR=14722127  # --attention #e0a44f
else
  STATUS_GLYPH="✅"; COLOR=7323541   # --success #6fbf95
fi
TITLE="$STATUS_GLYPH Repo sync · $(hostname)"

# Headline first, then the sections. MSG already starts with a blank line, so
# the two join with the gap the layout wants.
MSG=$(trim_blank_lines "$SUMMARY$MSG")
MSG=$(printf '%s' "$MSG" | cut -c1-"$MAX_CHARS")
echo "$TITLE"
echo "$MSG"

[ -f "$ENV_FILE" ] || { echo "repo-sync: $ENV_FILE not found — cannot report" >&2; exit 1; }
# Read only the key we need; avoids sourcing a file full of other secrets.
WEBHOOK=$(sed -n 's/^DISCORD_ALERT_WEBHOOK=//p' "$ENV_FILE" | tail -n1 | tr -d '"'"'"' \r')
[ -n "$WEBHOOK" ] || { echo "repo-sync: DISCORD_ALERT_WEBHOOK unset in $ENV_FILE" >&2; exit 1; }

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

# An embed rather than plain content, for one reason: Discord renders a
# message's content ABOVE its embeds, so anything sent as content arrives before
# its own heading. Inside an embed the title comes first, which is the order
# this report is read in — what happened, then what it was.
#
# It also buys a 4096-character description where content caps at 2000, which is
# why MAX_CHARS could go up, and a colour bar that says which of the three
# states this run was without reading a word.
curl -fsS -m 20 --retry 3 --retry-delay 5 \
  -H 'Content-Type: application/json' \
  -d "{\"embeds\":[{\"title\":\"$(json_escape_line "$TITLE")\",\"description\":\"$(json_escape "$MSG")\",\"color\":$COLOR}]}" \
  "$WEBHOOK" >/dev/null

# Exit non-zero if anything actually went wrong, so cron surfaces it even when
# Discord is unreachable. A stack merely *needing* a human is not an error, and
# neither is an image merely being old — but an image update that left a stack
# down is exactly as wrong as a failed restart, so it counts here too.
[ -z "$PULL_ERROR" ] && [ -z "$FAILED" ] && [ -z "$PULL_STACK_FAILED" ]
