#!/usr/bin/env sh
#
# What this pull actually needs, worked out rather than remembered.
#
# Why this exists
# ---------------
# `/deploy` gets the treatment right by reasoning about each changed path: a
# stack whose source is baked into an image needs `--build`, Grafana's
# provisioning needs a restart, the dashboard's icons need a *recreate*, proxy
# needs a human. That reasoning is correct and it is also the fourth
# hand-maintained list of something the repo already knows — the same shape
# check-dashboard.sh, check-probes.sh and check-observability.sh exist to guard.
# Done by hand at the end of a deploy it is done tired, and the failure mode is
# silent: the container keeps serving the old config and everything reports
# healthy.
#
# So derive it. Two modes, because there are two ways to ask the question:
#
#   ./scripts/deploy-plan.sh              what is RUNNING vs what is on disk
#   ./scripts/deploy-plan.sh <rev>        what changed between <rev> and HEAD
#
# Range mode is the one `/deploy` step 2a uses, with the pre-pull revision.
#
# State mode is for the case that actually happens: you pulled, you did not
# capture $BEFORE, and now nothing knows what changed. It asks Docker when each
# container last started and compares that against the mtime of the files that
# stack owns — git rewrites a changed file on pull, so a file newer than the
# container that reads it is a container running old config. That answer does
# not depend on the reflog, on remembering, or on git at all.
#
# With no argument and no reachable Docker, it falls back to ORIG_HEAD, which
# `git pull` writes for exactly this purpose.
#
# What this does NOT replace
# --------------------------
# Step 4's content-based drift detection. This script compares *timestamps*,
# which answers "did this container start before the file changed" — right for
# config read once at boot, and for a filename that has to become visible. It
# cannot see a single-file bind mount that went stale on an inode, because
# `docker restart` updates StartedAt without re-resolving the mount. The two
# checks are complementary and step 4 is still the one that catches that.
#
# Run by hand from the repo root:
#   ./scripts/deploy-plan.sh
#
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

# Files that never change what a container serves. Kept narrow on purpose: it is
# better to suggest one unnecessary `up -d` than to stay quiet about a real one.
IGNORE='\.(md|example)$|(^|/)\.gitignore$'

say() { printf '%s\n' "$*"; }

# --- the rules, in one place -------------------------------------------------
# Input: a newline-separated list of repo-relative paths. Output: the commands.
# Both modes feed this, so they cannot drift apart.
plan_for() {
  paths=$1
  [ -n "$paths" ] || { say "nothing to do — running containers are current"; return 0; }

  say "--- stacks to bring up ---"
  for s in $(printf '%s\n' "$paths" | sed -n 's#^docker/\([^/]*\)/.*#\1#p' | sort -u); do
    # proxy is DNS and the reverse proxy for everything. Never unattended —
    # see /deploy "Safety". Reported at the bottom instead.
    if [ "$s" = "proxy" ]; then continue; fi
    [ -f "docker/$s/docker-compose.yml" ] || continue
    # Source baked into an image is invisible to `up -d`: the service definition
    # is unchanged, so Compose does nothing and the container keeps the old
    # code. This is the assistant's whole failure mode.
    if printf '%s\n' "$paths" | grep -qE "^docker/$s/(app/|tests/|Dockerfile|requirements\.txt)"; then
      say "docker compose -f docker/$s/docker-compose.yml up -d --build"
    else
      say "docker compose -f docker/$s/docker-compose.yml up -d"
    fi
  done

  say "--- config read only at startup ---"
  quiet=1
  if printf '%s\n' "$paths" | grep -qE '^docker/monitoring/grafana/provisioning/'; then
    say "docker compose -f docker/monitoring/docker-compose.yml restart grafana"
    say "    # a directory mount shows the files immediately; Grafana reads them once, at boot"
    quiet=0
  fi
  if printf '%s\n' "$paths" | grep -qE '^docker/dashboard/(icons|assets)/'; then
    say "docker compose -f docker/dashboard/docker-compose.yml up -d --force-recreate"
    say "    # RECREATE, not restart: Next.js enumerates /app/public once, so a NEW"
    say "    # FILENAME 404s until the container is replaced — and a 404 favicon is"
    say "    # not an error anywhere, the browser just draws a globe"
    quiet=0
  fi
  if printf '%s\n' "$paths" | grep -qE '^scripts/'; then
    say "./scripts/install-cron.sh   # scripts changed; idempotent, never rewrites an existing entry"
    quiet=0
  fi
  if [ "$quiet" = 1 ]; then say "(none)"; fi

  say "--- needs you, not the runbook ---"
  quiet=1
  if printf '%s\n' "$paths" | grep -qE '^docker/proxy/'; then
    say "docker/proxy changed — AdGuard (the household's DNS) and Caddy."
    say "    Prove something is broken first:  dig +short stats.home @10.0.0.201"
    say "    If it answers, a recreate can only make things worse. See /deploy Safety."
    quiet=0
  fi
  if [ "$quiet" = 1 ]; then say "(nothing)"; fi
  # Explicit: a trailing conditional that happens to be false would otherwise
  # make this function "fail" and take the script down under `set -e`. It did.
  return 0
}

# --- provisioning deletions --------------------------------------------------
# File provisioning UPSERTS. A contact point, receiver or alert rule deleted
# from a file is not deleted from Grafana's database — it lingers, marked
# "Unused", and the UI will not remove a provisioned resource either. That is
# how a removed ntfy receiver kept firing at a host that no longer existed.
# Only answerable in range mode: it is a question about a diff.
check_deletions() {
  before=$1
  for f in $(git diff --name-only "$before" HEAD -- 'docker/monitoring/grafana/provisioning/alerting/*' || true); do
    [ -n "$f" ] || continue
    # Every uid or name the diff took out of this file.
    removed=$(git diff -U0 "$before" HEAD -- "$f" \
      | sed -n 's/^-[[:space:]]*-\{0,1\}[[:space:]]*\(uid\|name\): *\(.*\)/\2/p' | sort -u)
    [ -n "$removed" ] || continue
    for id in $removed; do
      # Still named in the file? Then provisioning still knows about it — which
      # is what a `deleteRules:` / `deleteContactPoints:` entry looks like, and
      # what a uid moved between sections looks like too. Either way, handled.
      if [ -f "$f" ] && grep -qE "(uid|name): *$id *$" "$f"; then continue; fi
      say "$f drops '$id' with nothing left naming it —"
      say "    provisioning UPSERTS, so it lingers in Grafana marked Unused and the UI"
      say "    will not delete a provisioned resource either. It needs an explicit"
      say "    deleteRules:/deleteContactPoints: entry for that uid. (This is how a"
      say "    removed ntfy receiver kept firing at a host that no longer existed.)"
    done
  done
  return 0
}

# --- mode selection ----------------------------------------------------------
if [ $# -gt 0 ]; then
  BEFORE=$1
  git rev-parse --verify --quiet "$BEFORE^{commit}" >/dev/null \
    || { say "deploy-plan: $BEFORE is not a commit this repo has" >&2; exit 1; }
  say "# range mode: $(git rev-parse --short "$BEFORE")..$(git rev-parse --short HEAD)"
  say ""
  changed=$(git diff --name-only "$BEFORE" HEAD | grep -vE "$IGNORE" || true)
  plan_for "$changed"
  say ""
  say "--- provisioning deletions ---"
  out=$(check_deletions "$BEFORE")
  if [ -n "$out" ]; then say "$out"; else say "(none)"; fi
  exit 0
fi

# No revision given. Ask the running system.
if ! docker info >/dev/null 2>&1; then
  say "# docker is not reachable from here, falling back to git" >&2
  if git rev-parse --verify --quiet ORIG_HEAD >/dev/null; then
    # Single-quoted deliberately: backticks inside double quotes are command
    # substitution, and this one would have run `git pull` on the box.
    say '# ORIG_HEAD mode (the revision git pull left behind)' >&2
    exec "$0" ORIG_HEAD
  fi
  say "deploy-plan: no docker and no ORIG_HEAD — pass a revision, e.g. HEAD@{1}" >&2
  exit 1
fi

say "# state mode: each container's start time vs the files it reads"
say ""

# Only files git manages can change on a pull. Without this the list is mostly
# __pycache__ and other build litter, which buries the two lines that matter.
TRACKED=$(mktemp)
trap 'rm -f "$TRACKED"' EXIT INT TERM
git ls-files > "$TRACKED"

stale=""
for c in $(docker ps --format '{{.Names}}'); do
  dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c" 2>/dev/null || true)
  [ -n "$dir" ] || continue
  case "$dir" in "$REPO"/*) ;; *) continue ;; esac   # only stacks this repo owns
  started=$(docker inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null || true)
  [ -n "$started" ] || continue
  # RFC3339 with nanoseconds; `date -d` handles it, and find wants an epoch.
  epoch=$(date -d "$started" +%s 2>/dev/null || true)
  [ -n "$epoch" ] || continue
  newer=$(find "$dir" -type f -newermt "@$epoch" 2>/dev/null \
          | sed "s#^$REPO/##" | grep -Fxf "$TRACKED" || true)
  [ -n "$newer" ] || continue
  stale="$stale
$newer"
done

stale=$(printf '%s\n' "$stale" | grep -v '^$' | grep -vE "$IGNORE" | sort -u || true)
if [ -n "$stale" ]; then
  n=$(printf '%s\n' "$stale" | wc -l)
  say "files newer than the container that reads them ($n):"
  printf '%s\n' "$stale" | head -15 | sed 's/^/    /'
  if [ "$n" -gt 15 ]; then say "    ... and $((n - 15)) more"; fi
  say ""
fi
plan_for "$stale"
say ""
say "Then run /deploy step 4's drift detection: this compares timestamps, which"
say "cannot see a single-file bind mount that went stale on an inode."
