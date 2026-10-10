#!/usr/bin/env bash
#
# Weekly proof that the newest Postgres dump can actually be restored. Run from
# cron on the Docker LXC:
#
#   0 3 * * 0 /root/homelab/scripts/pg-restore-test.sh >> /var/log/pg-restore-test.log 2>&1
#
# Why this exists
# ---------------
# pg-backup.sh proves a dump was *written*: it ran, it is recent, it is not
# 4 KB. None of that says it restores. A dump cut short by a full disk, a
# version mismatch, or a role the restore cannot create all look like a healthy
# file of a healthy size, and the only way to find out is to restore it.
# docs/design/tsd-backups-and-monitoring.md (Phase 0, restore-test tier B)
# exists because "the backup ran" and "the backup restores" are different
# claims, and only the second one matters.
#
# What it does
# ------------
#   1. `gzip -t` the newest dump: the file is intact.
#   2. Start a throwaway Postgres from the SAME image the core stack runs, so a
#      dump is tested against the major version it would really be restored to.
#      No network, one CPU, 512 MB: it cannot reach the real database and
#      cannot starve Ollama or anything else sharing CT 100's six cores.
#   3. Replay the dump into it.
#   4. Count databases, roles and tables in the restored server and compare
#      each with what the dump says it contains.
#   5. Remove the container and its data volume, whatever happened.
#
# The one error a correct restore produces
# ----------------------------------------
# pg_dumpall emits `CREATE ROLE postgres;` for the bootstrap superuser, and a
# freshly initialised server already has that role, so replaying any
# pg_dumpall into a fresh server prints exactly one
# `role "postgres" already exists`. That one is expected. Every other ERROR
# fails the test. ON_ERROR_STOP is deliberately not used, because it would stop
# at that first, harmless error.
#
# Reporting follows pg-backup.sh: a Healthchecks ping carrying the exit code
# (HEALTHCHECKS_PG_RESTORE_TEST_URL; period 7d, grace 1d) answers "did it run",
# and textfile metrics answer "is it still passing". The last-success time is
# the mtime of a marker file rather than "now", so a failed run cannot hide the
# age of the last good one.
#
set -euo pipefail
umask 077                      # the restore output contains role password hashes
REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$REPO/docker/monitoring/.env"

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$(dirname "$0")/healthchecks.sh"

DUMPS="${HL_BACKUP_DIR:-/opt/backups/postgres}"
STATE_DIR="${HL_RESTORE_STATE_DIR:-/var/lib/homelab/restore-test}"
NAME="hl-pg-restore-test"
# The core stack's own image line, so the test always follows the version the
# lab runs. A stack that renames or re-pins Postgres moves this with it.
IMAGE="${HL_PG_IMAGE:-$(grep -m1 -oE 'image:[[:space:]]*postgres:[^[:space:]]+' \
  "$REPO/docker/core/docker-compose.yml" 2>/dev/null | awk '{print $2}')}"

# Same reasoning as pg-backup.sh: a missing or unusable ping URL is a reason to
# warn, never a reason not to test.
if hc_url HEALTHCHECKS_PG_RESTORE_TEST_URL "$ENV_FILE"; then HC_URL=$HC_VALUE; else HC_URL=""; fi

START=$(date +%s)
DUMP=""
DATABASES=""
ROLES=""
TABLES=""
LOG=""

finish() {
  _code=$?

  # -v as well as -f: the postgres image declares a VOLUME for its data
  # directory, so the container gets an anonymous volume holding the whole
  # restored cluster, password hashes included. `rm -f` alone keeps that
  # volume, and a weekly job would pile up a full copy every Sunday, forever,
  # on the thin pool.
  docker rm -f -v "$NAME" >/dev/null 2>&1 || true
  [ -z "$LOG" ] || rm -f "$LOG"

  if [ "$_code" -eq 0 ]; then
    mkdir -p "$STATE_DIR" 2>/dev/null && touch "$STATE_DIR/last-success" 2>/dev/null || true
  fi

  metrics_open pg_restore_test

  metric_help homelab_pg_restore_test_success gauge \
    "1 if the last restore test of the newest Postgres dump passed"
  if [ "$_code" -eq 0 ]; then
    metric homelab_pg_restore_test_success 1
  else
    metric homelab_pg_restore_test_success 0
  fi

  metric_help homelab_pg_restore_test_duration_seconds gauge \
    "How long the last restore test took"
  metric homelab_pg_restore_test_duration_seconds "$(( $(date +%s) - START ))"

  if [ -f "$STATE_DIR/last-success" ]; then
    metric_help homelab_pg_restore_test_timestamp_seconds gauge \
      "When a restore test last passed"
    metric homelab_pg_restore_test_timestamp_seconds \
      "$(stat -c %Y "$STATE_DIR/last-success" 2>/dev/null || echo 0)"
  fi

  if [ -n "$DUMP" ]; then
    metric_help homelab_pg_restore_test_dump_age_seconds gauge \
      "Age of the dump the last restore test used"
    metric homelab_pg_restore_test_dump_age_seconds \
      "$(( $(date +%s) - $(stat -c %Y "$DUMP" 2>/dev/null || date +%s) ))"
  fi

  # Only what was actually counted from a restored server. A zero here would
  # read as "restored, and empty", which is a different claim.
  if [ -n "$DATABASES" ]; then
    metric_help homelab_pg_restore_test_databases gauge \
      "Databases present after the last restore (excluding postgres and templates)"
    metric homelab_pg_restore_test_databases "$DATABASES"
    metric_help homelab_pg_restore_test_roles gauge \
      "Roles present after the last restore (excluding pg_ built-ins)"
    metric homelab_pg_restore_test_roles "$ROLES"
    metric_help homelab_pg_restore_test_tables gauge \
      "User tables present after the last restore, across all databases"
    metric homelab_pg_restore_test_tables "$TABLES"
  fi

  metrics_close

  if [ -n "$HC_URL" ]; then
    hc_ping "$HC_URL/$_code" pg-restore-test || true
  fi
}
trap finish EXIT

if [ -z "$HC_URL" ]; then
  echo "pg-restore-test: $HC_REASON — nothing notices if this stops running" >&2
  hc_unarmed pg-restore-test
fi

fail() { echo "pg-restore-test: FAIL — $*" >&2; exit 1; }

[ -n "$IMAGE" ] || fail "could not find the postgres image in docker/core/docker-compose.yml"
DUMP=$(ls -1t "$DUMPS"/pgdumpall_*.sql.gz 2>/dev/null | head -n1 || true)
[ -n "$DUMP" ] || fail "no dump in $DUMPS (has pg-backup.sh ever run?)"
echo "pg-restore-test: testing $(basename "$DUMP") against $IMAGE" >&2

# 1. The archive itself.
gzip -t "$DUMP" || fail "$(basename "$DUMP") is not a valid gzip file"

# 2. A scratch server. A leftover from a run killed mid-test is removed first.
docker rm -f -v "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --network none --cpus 1 --memory 512m \
  -e POSTGRES_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')" \
  "$IMAGE" >/dev/null || fail "could not start a scratch container from $IMAGE"

# The image's entrypoint starts a temporary server for initialisation and then
# restarts it, so one successful pg_isready can come from the server that is
# about to stop. Wait for the init to finish, then for the real server.
for _ in $(seq 1 60); do
  docker logs "$NAME" 2>&1 | grep -q 'PostgreSQL init process complete' && break
  sleep 1
done
for _ in $(seq 1 30); do
  docker exec "$NAME" pg_isready -U postgres -q 2>/dev/null && break
  sleep 1
done
docker exec "$NAME" pg_isready -U postgres -q || fail "scratch server never became ready"

# 3. Replay. psql exits 0 even with ERRORs when ON_ERROR_STOP is off, so the
# errors are counted from its output instead.
LOG=$(mktemp)
gunzip -c "$DUMP" | docker exec -i "$NAME" psql -U postgres -d postgres -q -X >/dev/null 2>"$LOG" || true
UNEXPECTED=$(grep 'ERROR:' "$LOG" | grep -v 'role "postgres" already exists' || true)
if [ -n "$UNEXPECTED" ]; then
  echo "$UNEXPECTED" | head -n 20 >&2
  fail "restore produced $(echo "$UNEXPECTED" | wc -l | tr -d ' ') unexpected error(s)"
fi

# 4. Count what the dump claims, then what the server now holds.
q() { docker exec "$NAME" psql -U postgres -d "$1" -tAX -c "$2"; }
WANT_DBS=$(gunzip -c "$DUMP" | grep -c '^CREATE DATABASE ' || true)
WANT_ROLES=$(gunzip -c "$DUMP" | grep -c '^CREATE ROLE ' || true)
WANT_TABLES=$(gunzip -c "$DUMP" | grep -c '^CREATE TABLE ' || true)

DATABASES=$(q postgres "SELECT count(*) FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'")
ROLES=$(q postgres "SELECT count(*) FROM pg_roles WHERE rolname !~ '^pg_'")
TABLES=0
for _db in postgres $(q postgres "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'"); do
  _n=$(q "$_db" "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')")
  TABLES=$(( TABLES + _n ))
done

[ "$DATABASES" -eq "$WANT_DBS" ] || fail "dump creates $WANT_DBS database(s), restore has $DATABASES"
[ "$ROLES" -ge "$WANT_ROLES" ]   || fail "dump creates $WANT_ROLES role(s), restore has $ROLES"
[ "$TABLES" -eq "$WANT_TABLES" ] || fail "dump creates $WANT_TABLES table(s), restore has $TABLES"

echo "$(date -Is) ok $(basename "$DUMP"): $DATABASES database(s), $ROLES role(s), $TABLES table(s) restored in $(( $(date +%s) - START ))s"
