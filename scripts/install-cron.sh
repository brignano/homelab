#!/usr/bin/env sh
#
# Install this repo's scheduled jobs. Idempotent — safe to run any time.
#
# Why this exists
# ---------------
# Both scheduled scripts documented their own cron line in a header comment, in
# a file, on a box someone had to edit by hand. On 2026-09-19 it turned out
# that the sync job had never been installed on CT 100: the working tree was
# three weeks behind main, a merged change looked deployed because `git log` on
# GitHub said so, and nothing anywhere said otherwise.
#
# The thing that would have told you was the job that was not running, so the
# fix has two halves: this, which makes installing them a command instead of a
# ritual, and the Healthchecks ping in repo-sync.sh, which notices when they
# stop.
#
# Usage, on the Docker LXC:
#   ./scripts/install-cron.sh           # install anything missing
#   ./scripts/install-cron.sh --check   # report only; non-zero if any are missing
#
# An entry already in the crontab is never rewritten, only reported. If you have
# deliberately moved a job to a different hour, this leaves it where you put it.
set -eu

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"

# <schedule>|<script>|<what it is>. The schedule here is the one each script's
# own header documents; they are the same file, so keep them that way.
JOBS="*/5 * * * *|heartbeat.sh|dead man's switch -> Healthchecks
0 4 * * *|repo-sync.sh|pull, restart stale stacks, report
0 2 * * *|pg-backup.sh|nightly pg_dumpall, 14-day retention"

check=""
case "${1:-}" in
  "")       ;;
  --check)  check=yes ;;
  *)        echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

command -v crontab >/dev/null || { echo "install-cron: crontab not found" >&2; exit 1; }

current=$(crontab -l 2>/dev/null || true)
added=""
missing=""

# Build the new crontab in a temp file rather than by string-appending: a
# crontab is line-oriented and a lost newline silently drops the last job.
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
printf '%s' "$current" > "$tmp"
# A crontab is line-oriented, and an existing file that does not end in a
# newline would swallow the first job appended to it.
if [ -s "$tmp" ] && [ -n "$(tail -c1 "$tmp")" ]; then
  printf '\n' >> "$tmp"
fi

for script in heartbeat.sh repo-sync.sh pg-backup.sh; do
  line=$(echo "$JOBS" | grep "|$script|" | head -n1)
  schedule=${line%%|*}
  path="$REPO/scripts/$script"

  if [ ! -x "$path" ]; then
    echo "MISSING  $script is not executable at $path"
    missing="$missing $script"
    continue
  fi

  if printf '%s\n' "$current" | grep -q "scripts/$script"; then
    echo "ok       $script is already scheduled: $(printf '%s\n' "$current" | grep "scripts/$script" | head -n1)"
    continue
  fi

  if [ -n "$check" ]; then
    echo "MISSING  $script is not in the crontab"
    missing="$missing $script"
    continue
  fi

  printf '%s %s\n' "$schedule" "$path" >> "$tmp"
  echo "added    $schedule $path"
  added="$added $script"
done

if [ -n "$check" ]; then
  [ -z "$missing" ] || { echo; echo "Not scheduled:$missing — run $0 to install." >&2; exit 1; }
  echo "every job this repo expects is scheduled"
  exit 0
fi

if [ -n "$added" ]; then
  crontab "$tmp"
  echo "crontab updated"
else
  echo "nothing to do"
fi

[ -z "$missing" ] || { echo; echo "Could not schedule:$missing" >&2; exit 1; }
