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
# An entry already in the crontab is left where you put it — a different hour, a
# different log file, whatever you chose. The one exception is an entry with no
# redirection *at all*, which is not a choice but the absence of one: those
# predate this script and hand their output to a mailbox nobody reads. Those get
# the redirection appended, and nothing else about the line is touched.
# `--check` reports them rather than fixing them.
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
relogged=""
missing=""
dupes=""

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

  # How many uncommented lines schedule this script. More than one and it runs
  # that many times CONCURRENTLY — for repo-sync that is two `git pull`s and two
  # `docker compose up -d` racing on the same tree and the same stacks, two
  # Healthchecks pings, and two Discord reports.
  #
  # Structurally invisible until now: every lookup below takes `head -n1`, so a
  # second copy was never printed and `--check` said ok. Found on CT 100 on
  # 2026-09-19, and only because appending the redirection made the two lines
  # identical in `crontab -l` — the check itself still passed.
  #
  # Reported, never removed. This script only ever ADDS to a crontab, which is
  # worth keeping: it exists because a job was missing, and a tool that can
  # delete a schedule can cause the exact failure it was written to prevent. The
  # one-line fix is printed at the end instead.
  count=$(printf '%s\n' "$current" | grep -v '^[[:space:]]*#' | grep -c "scripts/$script" || true)
  if [ "${count:-0}" -gt 1 ]; then
    echo "DUPLICATE $script has $count crontab entries — it will run $count times at once"
    dupes="$dupes $script"
    continue
  fi

  # Commented-out lines do not count as scheduled, and the distinction matters:
  # a job someone disabled with a `#` looks exactly like a job that is running,
  # and reporting it ok is the precise failure this script was written for. It
  # is treated as absent, so it gets reinstalled and `--check` reports it.
  if printf '%s\n' "$current" | grep -v '^[[:space:]]*#' | grep -q "scripts/$script"; then
    existing=$(printf '%s\n' "$current" | grep -v '^[[:space:]]*#' | grep "scripts/$script" | head -n1)

    # An entry that redirects anywhere is left exactly as it is — that is the
    # "you pointed it at a different log" case the header promises not to touch.
    #
    # An entry with NO redirection at all is a different thing, and the
    # distinction is the whole point: it is not a choice someone made, it is the
    # absence of one. Those entries predate this script, and cron mails their
    # output to a local mailbox nobody reads and no MTA delivers. That silently
    # discards the only channel a job has left when its own reporting is what
    # broke — repo-sync.sh says so on stderr and nowhere else when it cannot
    # read .env or reach the Discord webhook.
    #
    # Found on CT 100 on 2026-09-19: heartbeat and repo-sync had no redirection,
    # pg-backup did, and `--check` called all three ok.
    case "$existing" in
      *'>'*)
        echo "ok       $script is already scheduled: $existing"
        continue
        ;;
    esac

    if [ -n "$check" ]; then
      echo "MISSING  $script is scheduled but discards its output: $existing"
      missing="$missing $script"
      continue
    fi

    # Narrow on purpose: only lines that name this script, are not commented
    # out, and contain no redirection at all.
    awk -v s="scripts/$script" -v r=" >> $LOG_DIR/${script%.sh}.log 2>&1" \
      'index($0, s) && $0 !~ /^[[:space:]]*#/ && $0 !~ />/ { $0 = $0 r } { print }' \
      "$tmp" > "$tmp.new" && mv "$tmp.new" "$tmp"
    echo "fixed    $script now logs to $LOG_DIR/${script%.sh}.log (was discarding output)"
    relogged="$relogged $script"
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

# Printed once rather than after every offending job — they all take the same
# fix. `!seen[$0]++` keeps the first occurrence of each identical line and drops
# the rest, which is the accidental-duplicate case; entries that differ (a job
# genuinely scheduled twice at different hours) survive and are still reported,
# to be looked at by hand.
report_dupes() {
  [ -n "$dupes" ] || return 0
  echo
  echo "Duplicate entries:$dupes — each will run concurrently with itself." >&2
  echo "This script never deletes a crontab line. To drop exact duplicates:" >&2
  echo "  crontab -l > ~/crontab.before && crontab -l | awk '!seen[\$0]++' | crontab - && crontab -l" >&2
}

if [ -n "$check" ]; then
  report_dupes
  [ -z "$missing" ] || { echo; echo "Not scheduled:$missing — run $0 to install." >&2; }
  if [ -n "$missing" ] || [ -n "$dupes" ]; then exit 1; fi
  echo "every job this repo expects is scheduled"
  exit 0
fi

if [ -n "$added" ] || [ -n "$relogged" ]; then
  crontab "$tmp"
  echo "crontab updated"
else
  echo "nothing to do"
fi

report_dupes
[ -z "$missing" ] || { echo; echo "Could not schedule:$missing" >&2; }
if [ -n "$missing" ] || [ -n "$dupes" ]; then exit 1; fi
