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
# deliberately moved a job to a different hour, or pointed it at a different
# log, this leaves it where you put it.
set -eu

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"

# Each job appends to its own log. Cron mails a job's output to the local user,
# which on this box is a mailbox nobody reads and no MTA delivers — so anything
# a script says before it can report through its own channel is lost. That is
# precisely the failure worth keeping: repo-sync.sh reports to Discord, but a
# run that cannot *reach* Discord (unreadable .env, curl failing, the webhook
# rejected) says so on stderr and nowhere else.
#
# The existing pg-backup entry on CT 100 already did this by hand; this makes it
# the default rather than a thing each line remembers. All three are quiet on a
# healthy run — one line a day from repo-sync, nothing at all from heartbeat —
# so the files stay small enough not to need rotating.
LOG_DIR="${HL_LOG_DIR:-/var/log}"

# Where the jobs write their Prometheus metrics (scripts/metrics.sh), and what
# node-exporter bind-mounts read-only. Created here rather than left to Docker:
# a bind mount to a missing path makes Docker create it root-owned, which works
# by luck because cron runs as root. Doing it explicitly means the one place
# that sets the box up is the place that says so.
TEXTFILE_DIR="${HL_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"

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

if [ -d "$TEXTFILE_DIR" ]; then
  echo "ok       metrics directory exists: $TEXTFILE_DIR"
elif [ -n "$check" ]; then
  echo "MISSING  $TEXTFILE_DIR does not exist — the jobs will run but publish no metrics"
else
  mkdir -p "$TEXTFILE_DIR" && chmod 755 "$TEXTFILE_DIR"
  echo "created  $TEXTFILE_DIR"
fi

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

  entry="$schedule $path >> $LOG_DIR/${script%.sh}.log 2>&1"
  printf '%s\n' "$entry" >> "$tmp"
  echo "added    $entry"
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
