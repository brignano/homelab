#!/usr/bin/env bash
#
# Nightly logical backup of every Postgres database. Run from cron on the
# Docker LXC:
#
#   0 2 * * * /root/homelab/scripts/pg-backup.sh >> /var/log/pg-backup.log 2>&1
#
# Why the dead man's switch and the metrics are here
# --------------------------------------------------
# AGENTS.md states the rule plainly: anything scheduled needs a dead man's
# switch, including the scheduler. This job did not have one. heartbeat.sh and
# repo-sync.sh both ping Healthchecks; the nightly dump — the job that
# tsd-backups-and-monitoring.md marks 🔴 Critical, and the only protection the
# lab has against an accidental DROP — pinged nothing. A cron entry silently
# removed, a container rename, a full disk: all of them look exactly like a
# quiet, healthy month.
#
# So it now does both halves, for the same reason repo-sync.sh does:
#
#   Healthchecks answers "did it run?" from off-box, where silence is the
#   signal. Set HEALTHCHECKS_PG_BACKUP_URL in docker/monitoring/.env.
#
#   The textfile metrics answer "is the backup I have any good?" — age, size and
#   retained count — which is a question you ask by looking at a panel, not by
#   waiting to be paged. A dump that shrinks to 4 KB because the DSN broke still
#   *runs* every night, and only the size series would ever have told you.
#
set -euo pipefail
umask 077                      # dumps contain role password hashes — keep them private

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$REPO/docker/monitoring/.env"

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$(dirname "$0")/healthchecks.sh"

DEST="${HL_BACKUP_DIR:-/opt/backups/postgres}"
RETAIN_DAYS="${HL_RETAIN_DAYS:-14}"

# `|| HC_URL=""` is load-bearing, unlike in the other two scripts. This one runs
# under `pipefail` and `set -e`, and a missing .env or an unusable value must
# not kill the backup before it starts — a job destroyed by the monitoring
# bolted onto it is the one outcome none of this is allowed to have. Not having
# a ping URL is a reason to warn, never a reason not to back up.
#
# hc_url rejects a placeholder as firmly as an empty value; HC_REASON says
# which it was. That distinction is why this file changed: the placeholder was
# here, silently, from the day the switch was added.
if hc_url HEALTHCHECKS_PG_BACKUP_URL "$ENV_FILE"; then HC_URL=$HC_VALUE; else HC_URL=""; fi

START=$(date +%s)
TMP=""
OUT=""

# One EXIT handler for all three jobs — clean up the partial dump, publish the
# outcome, and tell Healthchecks. It has to see the real exit code, so `$?` is
# the first thing read.
finish() {
  _code=$?

  [ -z "$TMP" ] || rm -f "$TMP"

  # Everything except the pass/fail flag is measured from the dumps on disk
  # rather than from this run, and that is the important design choice here.
  #
  # The obvious version — stamp `now` on success and leave the metric alone on
  # failure — does not work, because the whole .prom file is rewritten every
  # night. A failed run would emit a file with no timestamp in it at all, the
  # series would go stale and then absent, and the "last good backup is 26h old"
  # alert would resolve itself into NoData at exactly the moment it mattered.
  #
  # The newest file's mtime cannot have that problem. It is the real answer to
  # the real question, it survives a failed run, a reboot and a lost metrics
  # directory, and it needs no state carried between runs.
  _newest=$(ls -1t "$DEST"/pgdumpall_*.sql.gz 2>/dev/null | head -n1 || true)

  metrics_open pg_backup

  metric_help homelab_pg_backup_success gauge \
    "1 if the last pg_dumpall run succeeded"
  if [ "$_code" -eq 0 ]; then
    metric homelab_pg_backup_success 1
  else
    metric homelab_pg_backup_success 0
  fi

  metric_help homelab_pg_backup_duration_seconds gauge \
    "How long the last pg_dumpall took"
  metric homelab_pg_backup_duration_seconds "$(( $(date +%s) - START ))"

  if [ -n "$_newest" ]; then
    metric_help homelab_pg_backup_timestamp_seconds gauge \
      "Modification time of the newest dump on disk"
    metric homelab_pg_backup_timestamp_seconds \
      "$(stat -c %Y "$_newest" 2>/dev/null || echo 0)"

    metric_help homelab_pg_backup_size_bytes gauge \
      "Size of the newest dump on disk"
    metric homelab_pg_backup_size_bytes \
      "$(stat -c %s "$_newest" 2>/dev/null || echo 0)"
  fi

  metric_help homelab_pg_backup_files gauge \
    "Number of dumps currently retained"
  metric homelab_pg_backup_files \
    "$(ls -1 "$DEST"/pgdumpall_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"

  metrics_close

  if [ -n "$HC_URL" ]; then
    # /<exit code>: 0 records a success and anything else a failure, so "ran and
    # failed" stays distinguishable from "never ran".
    #
    # `|| true` because this runs in an EXIT trap: the backup's own verdict is
    # already decided, and a failed ping must not overwrite it. hc_ping reports
    # the failure itself, on stderr and as a metric.
    hc_ping "$HC_URL/$_code" pg-backup || true
  fi
}
trap finish EXIT

if [ -z "$HC_URL" ]; then
  # Said on every run, on stderr, which cron appends to the log. Deliberate:
  # an unconfigured dead man's switch is precisely the silence this exists to
  # break, and the message stops the moment the variable is set.
  #
  # And recorded as a metric, because this message was already being printed to
  # a log nobody reads — the placeholder that prompted all this would not have
  # been found by writing it more loudly.
  echo "pg-backup: $HC_REASON — nothing notices if this stops running" >&2
  hc_unarmed pg-backup
fi

CONTAINER=$(docker ps --format '{{.Names}} {{.Image}}' \
  | awk '$2 ~ /postgres/ && $2 !~ /exporter/ {print $1; exit}')
[ -n "$CONTAINER" ] || { echo "no postgres DB container found"; exit 1; }
echo "using container: $CONTAINER" >&2
mkdir -p "$DEST"; chmod 700 "$DEST"
TS=$(date +%Y-%m-%d_%H%M%S)
TMP="$DEST/.pgdumpall_$TS.sql.gz"; OUT="$DEST/pgdumpall_$TS.sql.gz"
docker exec -u postgres "$CONTAINER" pg_dumpall | gzip > "$TMP"
mv "$TMP" "$OUT"
TMP=""
find "$DEST" -name 'pgdumpall_*.sql.gz' -mtime +$RETAIN_DAYS -delete
echo "$(date -Is) ok $OUT ($(du -h "$OUT" | cut -f1))"
