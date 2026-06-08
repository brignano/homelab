#!/usr/bin/env bash
set -euo pipefail
umask 077                      # dumps contain role password hashes — keep them private
DEST=/opt/backups/postgres
RETAIN_DAYS=14
CONTAINER=$(docker ps --format '{{.Names}} {{.Image}}' \
  | awk '$2 ~ /postgres/ && $2 !~ /exporter/ {print $1; exit}')
[ -n "$CONTAINER" ] || { echo "no postgres DB container found"; exit 1; }
echo "using container: $CONTAINER" >&2
mkdir -p "$DEST"; chmod 700 "$DEST"
TS=$(date +%Y-%m-%d_%H%M%S)
TMP="$DEST/.pgdumpall_$TS.sql.gz"; OUT="$DEST/pgdumpall_$TS.sql.gz"
trap 'rm -f "$TMP"' EXIT
docker exec -u postgres "$CONTAINER" pg_dumpall | gzip > "$TMP"
mv "$TMP" "$OUT"
find "$DEST" -name 'pgdumpall_*.sql.gz' -mtime +$RETAIN_DAYS -delete
echo "$(date -Is) ok $OUT ($(du -h "$OUT" | cut -f1))"
