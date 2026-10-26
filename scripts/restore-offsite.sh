#!/usr/bin/env bash
#
# Put the lab's irreplaceable data back from the offsite (restic → B2) copy,
# onto a FRESH Docker host, before any stack has started. The inverse of
# offsite-backup.sh. Run on CT 100 as root:
#
#   ./scripts/restore-offsite.sh            # restore what is missing
#   ./scripts/restore-offsite.sh --dry-run  # say what it would do
#
# The Ansible playbook (ansible/, `-e hl_restore_offsite=true`) calls this;
# docs/runbook-rebuild.md has the whole sequence.
#
# When this is the right tool
# ---------------------------
# Only when the nightly image is gone too — box and backup drive lost together.
# If the backup drive survived, `pct restore` of the newest vzdump archive gives
# back all of CT 100 in one step, Postgres included, and none of this is needed.
#
# What it restores
# ----------------
#   - every docker/<stack>/.env from the snapshot's env/<stack>.env
#   - each named volume in the snapshot, created with the labels
#     Compose would have given it, so `up` adopts it instead of warning
#   - the SQLite databases from their consistent copies, over the live-file
#     copies (which offsite-backup.sh excluded in any case)
#   - the newest Postgres dump, left in $TARGET/postgres for the replay below
#
# What it never does
# ------------------
# Overwrite. An .env that exists stays; a volume that already exists is
# skipped and named. A half-restored box re-run is safe, and a live box is
# untouched — which matters, because the playbook runs against the live box.
#
# Postgres is not replayed here: it needs the core stack up. The last lines it
# prints are the command.
set -euo pipefail
umask 077

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
RESTIC_ENV="${HL_RESTIC_ENV:-/root/.config/homelab/restic.env}"
TARGET="${HL_RESTORE_DIR:-/var/lib/homelab/restore}"
STAGING=/var/lib/homelab/offsite-staging     # the path offsite-backup.sh stored
SNAPSHOT="${HL_RESTORE_SNAPSHOT:-latest}"

DRY=""
[ "${1:-}" = "--dry-run" ] && DRY=1

fail() { echo "restore-offsite: FAIL — $*" >&2; exit 1; }
say()  { echo "restore-offsite: $*"; }
run()  { if [ -n "$DRY" ]; then echo "  would: $*"; else "$@"; fi; }

[ "$(id -u)" -eq 0 ] || fail "run as root"
[ -f "$RESTIC_ENV" ] || fail "$RESTIC_ENV is missing — recreate it from the password manager (docs/runbook-backups.md §1)"
command -v restic >/dev/null || fail "restic is not installed (apt install restic)"
command -v docker >/dev/null || fail "docker is not installed (scripts/bootstrap-docker.sh)"

set -a
# shellcheck disable=SC1090
. "$RESTIC_ENV"
set +a

if [ -d "$TARGET$STAGING" ]; then
  say "reusing the snapshot already restored into $TARGET (delete it to fetch again)"
else
  say "fetching snapshot $SNAPSHOT into $TARGET"
  mkdir -p "$TARGET"
  # Everything in the snapshot: it is a few GB by design, and a partial
  # --include list is one more hand-kept copy of offsite-backup.sh's choices.
  restic restore "$SNAPSHOT" --host homelab --target "$TARGET"
fi
SRC="$TARGET$STAGING"
[ -d "$SRC" ] || fail "snapshot has no $STAGING — not one offsite-backup.sh wrote?"

# 1. Secrets.
for _f in "$SRC"/env/*.env; do
  [ -f "$_f" ] || continue
  _stack=$(basename "$_f" .env)
  _dst="$REPO/docker/$_stack/.env"
  if [ ! -d "$REPO/docker/$_stack" ]; then
    say "env: $_stack is not a stack in this checkout — left in $_f"
  elif [ -f "$_dst" ]; then
    say "env: $_stack/.env exists — kept"
  else
    say "env: $_stack/.env restored"
    run install -m 600 "$_f" "$_dst"
  fi
done

# 2. Volumes: whichever ones the snapshot holds. Reading them from the
#    snapshot rather than from offsite-backup.sh's VOLUMES means a volume added
#    there later restores without editing this. (The list it used is not in the
#    snapshot: offsite-backup.sh excludes its staging dotfiles.)
SNAPVOLS="$TARGET/var/lib/docker/volumes"
for _from in "$SNAPVOLS"/*/_data; do
  [ -d "$_from" ] || continue
  _vol=$(basename "$(dirname "$_from")")
  if docker volume inspect "$_vol" >/dev/null 2>&1; then
    say "volume: $_vol exists — kept, not overwritten"
    continue
  fi
  # <project>_<volume>: stack names have no underscore, so the first one
  # splits them. These two labels are what `docker compose up` checks.
  _project=${_vol%%_*}
  _name=${_vol#*_}
  say "volume: $_vol restored"
  if [ -n "$DRY" ]; then
    run docker volume create "$_vol"
    continue
  fi
  docker volume create \
    --label "com.docker.compose.project=$_project" \
    --label "com.docker.compose.volume=$_name" "$_vol" >/dev/null
  _dst=$(docker volume inspect -f '{{.Mountpoint}}' "$_vol")
  cp -a "$_from/." "$_dst/"

  # The consistent copy of each database, over nothing (the live file was
  # excluded from the snapshot) or over a torn copy.
  if [ -d "$SRC/sqlite/$_vol" ]; then
    cp -a "$SRC/sqlite/$_vol/." "$_dst/"
  fi
done

# 3. Postgres: left for the replay.
_dump=$(ls -1t "$SRC"/postgres/pgdumpall_*.sql.gz 2>/dev/null | head -n1 || true)
say "done."
if [ -n "$_dump" ]; then
  cat <<EOF

Postgres is not restored yet. Once the core stack is up and healthy, on an
EMPTY postgres_data (a fresh box), replay the dump. One "role postgres already
exists" error is expected (scripts/pg-restore-test.sh explains why):

  gunzip -c $_dump | docker exec -i -u postgres postgres psql -q -d postgres
EOF
else
  echo "No Postgres dump in the snapshot."
fi
