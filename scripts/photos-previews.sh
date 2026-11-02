#!/usr/bin/env bash
#
# Make JPEG previews of the HEIC photos in the iCloud mirror, so Jellyfin can
# show them (docker/jellyfin/README.md). Run from cron on the Docker LXC,
# nightly, after photos-sync.sh has finished (its deadline is 07:00):
#
#   15 7 * * * /root/homelab/scripts/photos-previews.sh >> /var/log/photos-previews.log 2>&1
#
# Jellyfin 12.2 does not scan .HEIC, so none of the mirror's HEIC photos appear
# in the "Photo Archive" library. The previews are derived copies (JPEG, 2048 px
# long edge, quality 85, EXIF kept, mtime = capture date) in
# /media/photos/icloud-previews, mirroring the year/month layout. The originals
# stay in the mirror, untouched; delete the previews folder and this job
# rebuilds it. The Hidden album's folder is never read or written.
#
# Incremental: a preview that exists is skipped. One core at nice 19
# (docker/photos/docker-compose.yml, previews/Dockerfile), stopped after
# PREVIEW_MAX_MINUTES (default 240) so a first run of ~6,500 files carries over
# to the next night instead of running into the day.
#
# No Healthchecks dead man's switch, on purpose: the previews are derived and
# rebuildable, so a missed night costs nothing. Failure pages as
# hl-photos-previews-failed and a job that stops running as
# hl-photos-previews-stale (both in rules.yml), which needs no account setup.
#
# Not configured = not running: until the previews folder exists (README step
# in docker/jellyfin/README.md) this exits 0 and creates nothing, so a missing
# media drive cannot make it write onto CT 100's rootfs.
#
set -uo pipefail

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE="$REPO/docker/photos/docker-compose.yml"
SRC="${HL_PHOTOS_DIR:-/media/photos/icloud}"
DST="${HL_PHOTOS_PREVIEWS_DIR:-/media/photos/icloud-previews}"
STATE_DIR="${HL_PHOTOS_STATE_DIR:-/var/lib/homelab/photos}"
LOCK="${HL_PHOTOS_PREVIEWS_LOCK:-/run/photos-previews.lock}"

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"

configured=0
[ -d "$DST" ] && configured=1

count_heic() { find "$SRC" -type f -iname '*.heic' 2>/dev/null | wc -l | tr -d ' '; }
count_previews() { find "$DST" -type f -name '*.jpg' 2>/dev/null | wc -l | tr -d ' '; }

START=$(date +%s)

finish() {
  _code=$?

  if [ "$configured" -eq 1 ] && [ "$_code" -eq 0 ]; then
    mkdir -p "$STATE_DIR" 2>/dev/null && touch "$STATE_DIR/previews-last-success" 2>/dev/null || true
  fi

  metrics_open photos_previews

  metric_help homelab_photos_previews_configured gauge \
    "1 once the previews folder exists and the job is expected to run"
  metric homelab_photos_previews_configured "$configured"

  if [ "$configured" -eq 1 ]; then
    metric_help homelab_photos_previews_success gauge \
      "1 if the last photos-previews.sh run succeeded (a time-limit stop counts)"
    if [ "$_code" -eq 0 ]; then metric homelab_photos_previews_success 1; else metric homelab_photos_previews_success 0; fi

    metric_help homelab_photos_previews_heic_files gauge "HEIC originals in the mirror"
    metric homelab_photos_previews_heic_files "$(count_heic)"
    metric_help homelab_photos_previews_files gauge "JPEG previews made so far"
    metric homelab_photos_previews_files "$(count_previews)"

    metric_help homelab_photos_previews_duration_seconds gauge "How long the last run took"
    metric homelab_photos_previews_duration_seconds "$(( $(date +%s) - START ))"

    if [ -f "$STATE_DIR/previews-last-success" ]; then
      metric_help homelab_photos_previews_timestamp_seconds gauge \
        "When photos-previews.sh last succeeded"
      metric homelab_photos_previews_timestamp_seconds \
        "$(stat -c %Y "$STATE_DIR/previews-last-success" 2>/dev/null || echo 0)"
    fi
  fi

  metrics_close
}
trap finish EXIT

[ "$configured" -eq 1 ] || exit 0

if [ ! -d "$SRC" ]; then
  echo "$(date -Is) photos-previews: $SRC is missing (media drive not mounted?)" >&2
  exit 1
fi

exec 9>"$LOCK"
flock -n 9 || { echo "photos-previews: another run is still going" >&2; exit 1; }

out=$(docker compose -f "$COMPOSE" --profile job run --rm -T --build heicpreview 2>&1 </dev/null)
code=$?
summary=$(printf '%s\n' "$out" | tail -n 1)
if [ "$code" -ne 0 ]; then
  printf '%s\n' "$out" | tail -n 20 >&2
  echo "$(date -Is) photos-previews: failed (exit $code)" >&2
  exit 1
fi
echo "$(date -Is) photos-previews: $summary"
exit 0
