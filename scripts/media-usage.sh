#!/usr/bin/env sh
#
# How much of the media drive each folder takes, for the home page's Media
# drive details ("What's on it") and its "TB of movies and shows" chip
# (homelab#395). Written as a textfile metric; node-exporter serves it.
#
# Cron (scripts/install-cron.sh): 20 5 * * *   — once a day, after the night's
# downloads, photo mirror and backups have finished.
#
# ONE du over the whole drive, two levels deep, on purpose. Imports are
# hardlinks (a downloaded episode and its library copy are the same blocks), and
# du counts a hardlinked file once per invocation, under the first folder it
# meets. Separate du runs per folder would count those files twice and add up
# to more than the drive holds.
#
# Read-only: du and stat, nothing else. A missing /media is a no-op.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
. "$REPO/scripts/metrics.sh"

ROOT="${HL_MEDIA_ROOT:-/media}"
[ -d "$ROOT" ] || exit 0

out=$(du -B1 --max-depth=2 -x "$ROOT" 2>/dev/null || true)
[ -n "$out" ] || exit 0

metrics_open media_usage
metric_help homelab_media_folder_bytes gauge \
  "Bytes under a folder of the media drive, one and two levels deep (hardlinks counted once)"
printf '%s\n' "$out" | while IFS="$(printf '\t')" read -r bytes path; do
  [ "$path" = "$ROOT" ] && continue
  metric homelab_media_folder_bytes "$bytes" "$(metric_kv path "$path")"
done
metric_help homelab_media_usage_timestamp_seconds gauge \
  "When media-usage.sh last measured the drive"
metric homelab_media_usage_timestamp_seconds "$(date +%s)"
metrics_close
