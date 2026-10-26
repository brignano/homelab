#!/usr/bin/env bash
#
# Nightly encrypted offsite copy of the lab's irreplaceable data, with restic,
# to Backblaze B2. Run from cron on the Docker LXC:
#
#   30 3 * * * /root/homelab/scripts/offsite-backup.sh >> /var/log/offsite-backup.log 2>&1
#
# Design: docs/design/tsd-backups-and-monitoring.md, Phase 2.
# Setup and restore: docs/runbook-backups.md.
#
# Why this exists
# ---------------
# The nightly vzdump image (Phase 1) restores the whole lab in one step, but it
# lives in the same house as the box. A fire, a flood or a burglary takes the
# lab and its only backup together. What cannot be re-downloaded is small — a
# few GB of secrets, SQLite databases and configuration — so it goes offsite,
# encrypted on this box before it leaves (restic always encrypts).
#
# What it copies
# --------------
#   - every docker/*/.env (the lab's secrets; not in git by design)
#   - the newest Postgres dump
#   - the named volumes in VOLUMES below, minus caches
#
# SQLite databases are NOT read live. A file-level copy of a database that is
# being written can restore as corrupt, so each one is copied with
# `sqlite3 .backup` into a staging directory first, and the live file (and its
# -wal/-shm) is excluded. Databases are found by their header, not their name,
# so a new one in a backed-up volume is handled without editing this file.
#
# Not configured = not running, and not paging
# --------------------------------------------
# Until /root/.config/homelab/restic.env exists this exits 0 and reports
# homelab_offsite_configured 0. That is a setup step you have not done yet,
# shown on the dashboard, rather than an alert you cannot act on. The moment the
# file exists the job is real: it needs HEALTHCHECKS_OFFSITE_BACKUP_URL, and a
# failure alerts as hl-offsite-failed.
#
# Sundays it also runs `restic check --read-data-subset=5%` (restore-test tier
# B′): every stored byte is downloaded and verified about once every five
# months.
#
set -euo pipefail
umask 077                      # staging holds secrets and password hashes

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$REPO/docker/monitoring/.env"
RESTIC_ENV="${HL_RESTIC_ENV:-/root/.config/homelab/restic.env}"
STAGING="${HL_OFFSITE_STAGING:-/var/lib/homelab/offsite-staging}"
STATE_DIR="${HL_OFFSITE_STATE_DIR:-/var/lib/homelab/offsite}"
DUMPS="${HL_BACKUP_DIR:-/opt/backups/postgres}"

# <docker volume> <comma-separated excludes, relative to the volume root>.
# Volumes that do not exist on this box (yet) are skipped, not failed.
VOLUMES="${HL_OFFSITE_VOLUMES:-ai_open_webui_data cache
monitoring_grafana_data plugins,png,csv
proxy_adguard_conf -
homeassistant_homeassistant_config tts,backups
desktops_kali_config .cache,.local/share/Trash
core_portainer_data -
jellyfin_config metadata,cache,log,data/transcodes,data/subtitles,data/keyframes,data/attachments
downloads_sonarr MediaCover,logs,Backups
downloads_radarr MediaCover,logs,Backups
downloads_prowlarr logs,Backups
downloads_qbittorrent qBittorrent/logs
matterbridge_data .npm,.cache}"

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$(dirname "$0")/healthchecks.sh"

publish_unconfigured() {
  metrics_open offsite_backup
  metric_help homelab_offsite_configured gauge \
    "1 once restic.env exists and the offsite backup is expected to run"
  metric homelab_offsite_configured 0
  metrics_close
}

if [ ! -f "$RESTIC_ENV" ]; then
  echo "offsite-backup: $RESTIC_ENV does not exist — not set up yet (docs/runbook-backups.md)" >&2
  publish_unconfigured
  exit 0
fi

if hc_url HEALTHCHECKS_OFFSITE_BACKUP_URL "$ENV_FILE"; then HC_URL=$HC_VALUE; else HC_URL=""; fi

START=$(date +%s)
ADDED=""
SNAPSHOTS=""
CHECKED=""
CHECK_OK=""

finish() {
  _code=$?

  # Staging holds plaintext copies of databases and secrets; never leave them
  # lying around.
  [ ! -d "$STAGING" ] || rm -rf "${STAGING:?}"/* "${STAGING:?}"/.[!.]*

  if [ "$_code" -eq 0 ]; then
    mkdir -p "$STATE_DIR" 2>/dev/null && touch "$STATE_DIR/last-success" 2>/dev/null || true
  fi

  metrics_open offsite_backup

  metric_help homelab_offsite_configured gauge \
    "1 once restic.env exists and the offsite backup is expected to run"
  metric homelab_offsite_configured 1

  metric_help homelab_offsite_success gauge \
    "1 if the last offsite backup (and, on Sundays, its check) succeeded"
  if [ "$_code" -eq 0 ]; then
    metric homelab_offsite_success 1
  else
    metric homelab_offsite_success 0
  fi

  metric_help homelab_offsite_duration_seconds gauge \
    "How long the last offsite backup took"
  metric homelab_offsite_duration_seconds "$(( $(date +%s) - START ))"

  # From a marker file, not "now", so a failing run cannot hide the age of the
  # last good one (same reasoning as pg-backup.sh).
  if [ -f "$STATE_DIR/last-success" ]; then
    metric_help homelab_offsite_timestamp_seconds gauge \
      "When an offsite backup last succeeded"
    metric homelab_offsite_timestamp_seconds \
      "$(stat -c %Y "$STATE_DIR/last-success" 2>/dev/null || echo 0)"
  fi

  if [ -n "$ADDED" ]; then
    metric_help homelab_offsite_bytes_added gauge \
      "New data the last offsite backup uploaded (before compression)"
    metric homelab_offsite_bytes_added "$ADDED"
  fi
  if [ -n "$SNAPSHOTS" ]; then
    metric_help homelab_offsite_snapshots gauge \
      "Snapshots kept in the offsite repository after pruning"
    metric homelab_offsite_snapshots "$SNAPSHOTS"
  fi
  if [ -n "$CHECKED" ]; then
    metric_help homelab_offsite_check_success gauge \
      "1 if the last weekly restic check (5% of data read back) passed"
    metric homelab_offsite_check_success "$CHECK_OK"
  fi

  metrics_close

  if [ -n "$HC_URL" ]; then
    hc_ping "$HC_URL/$_code" offsite-backup || true
  fi
}
trap finish EXIT

if [ -z "$HC_URL" ]; then
  echo "offsite-backup: $HC_REASON — nothing notices if this stops running" >&2
  hc_unarmed offsite-backup
fi

fail() { echo "offsite-backup: FAIL — $*" >&2; exit 1; }

command -v restic  >/dev/null || fail "restic is not installed (apt install restic)"
command -v sqlite3 >/dev/null || fail "sqlite3 is not installed (apt install sqlite3)"

# Only the restic variables, exported for restic and nothing else.
set -a
# shellcheck disable=SC1090
. "$RESTIC_ENV"
set +a
[ -n "${RESTIC_REPOSITORY:-}" ] || fail "RESTIC_REPOSITORY is unset in $RESTIC_ENV"
[ -n "${RESTIC_PASSWORD:-}${RESTIC_PASSWORD_FILE:-}" ] || fail "RESTIC_PASSWORD is unset in $RESTIC_ENV"

# Never two at once: restic would serialise on its lock anyway, but a stuck run
# should fail tonight's ping, not queue behind itself.
exec 9>"/run/offsite-backup.lock"
flock -n 9 || fail "another offsite-backup is still running"

mkdir -p "$STAGING"
chmod 700 "$STAGING"
rm -rf "${STAGING:?}"/* "${STAGING:?}"/.[!.]*
EXCLUDES="$STAGING/.excludes"
: > "$EXCLUDES"

# 1. Secrets. Copied into staging so the snapshot does not depend on where the
# checkout lives.
mkdir -p "$STAGING/env"
for _f in "$REPO"/docker/*/.env; do
  [ -f "$_f" ] || continue
  _stack=$(basename "$(dirname "$_f")")
  cp -p "$_f" "$STAGING/env/$_stack.env"
done

# 2. The newest Postgres dump.
_dump=$(ls -1t "$DUMPS"/pgdumpall_*.sql.gz 2>/dev/null | head -n1 || true)
if [ -n "$_dump" ]; then
  mkdir -p "$STAGING/postgres"
  cp -p "$_dump" "$STAGING/postgres/"
else
  echo "offsite-backup: no Postgres dump in $DUMPS — continuing without one" >&2
fi

# 3. Volumes, with SQLite databases copied consistently. Loops read from files
# rather than pipes, so a failure inside one stops the script instead of dying
# in a subshell while the next volume carries on.
# tr drops NUL bytes: a binary *.db that is not SQLite would otherwise make bash
# print "ignored null byte" into the log for every such file, every night.
is_sqlite() { [ "$(head -c 15 "$1" 2>/dev/null | tr -d '\000')" = "SQLite format 3" ]; }

: > "$STAGING/.paths"
printf '%s\n' "$VOLUMES" > "$STAGING/.volumes"
while read -r _vol _ex; do
  [ -n "$_vol" ] || continue
  _dir=$(docker volume inspect -f '{{.Mountpoint}}' "$_vol" 2>/dev/null || true)
  if [ -z "$_dir" ] || [ ! -d "$_dir" ]; then
    echo "offsite-backup: volume $_vol not on this box — skipped" >&2
    continue
  fi
  echo "$_dir" >> "$STAGING/.paths"

  if [ "$_ex" != "-" ]; then
    for _e in $(printf '%s' "$_ex" | tr ',' ' '); do
      echo "$_dir/$_e" >> "$EXCLUDES"
    done
  fi

  find "$_dir" -type f -size +0 \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) \
    -not -path '*/cache/*' -not -path '*/metadata/*' > "$STAGING/.dbs" 2>/dev/null || true
  while read -r _db; do
    is_sqlite "$_db" || continue
    _rel=${_db#"$_dir"/}
    _out="$STAGING/sqlite/$_vol/$_rel"
    mkdir -p "$(dirname "$_out")"
    # .backup takes a consistent copy while the owning app keeps writing; the
    # timeout rides out a write in progress instead of failing on it.
    sqlite3 "$_db" ".timeout 30000" ".backup '$_out'" \
      || fail "sqlite backup of $_db failed"
    printf '%s\n%s-wal\n%s-shm\n%s-journal\n' "$_db" "$_db" "$_db" "$_db" >> "$EXCLUDES"
  done < "$STAGING/.dbs"
done < "$STAGING/.volumes"

echo "$STAGING" >> "$STAGING/.paths"

# 4. Back up. Niced: this shares CT 100's six cores with Ollama and Jellyfin.
# Exit 3 means "snapshot made, some files unreadable" — a backup with holes in
# it, so it counts as a failure here rather than a success with a footnote.
_summary=$(nice -n 19 ionice -c3 restic backup --quiet --json \
  --host homelab --tag nightly \
  --files-from "$STAGING/.paths" --exclude-file "$EXCLUDES" \
  --exclude "$STAGING/.*" \
  | grep '"message_type":"summary"' | tail -n1) \
  || fail "restic backup failed (exit $?)"
ADDED=$(printf '%s' "$_summary" | sed -n 's/.*"data_added":\([0-9]*\).*/\1/p')

# 5. Retention, as the TSD sets it.
nice -n 19 restic forget --quiet --host homelab \
  --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune \
  || fail "restic forget/prune failed"
SNAPSHOTS=$(restic snapshots --quiet --json --host homelab | grep -o '"short_id"' | wc -l | tr -d ' ')

# 6. Sundays: read back a random 5% of the stored data.
if [ "$(date +%u)" = 7 ]; then
  CHECKED=1
  if nice -n 19 restic check --quiet --read-data-subset=5%; then
    CHECK_OK=1
  else
    CHECK_OK=0
    fail "restic check found a problem in the offsite repository"
  fi
fi

echo "$(date -Is) ok: $(( ${ADDED:-0} / 1048576 )) MiB added, $SNAPSHOTS snapshot(s) kept${CHECKED:+, check passed} in $(( $(date +%s) - START ))s"
