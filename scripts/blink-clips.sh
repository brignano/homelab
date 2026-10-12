#!/usr/bin/env bash
#
# Move Blink camera clips that Home Assistant saved into Jellyfin's library.
# Run from cron on the Docker LXC every five minutes:
#
#   */5 * * * * /root/homelab/scripts/blink-clips.sh >> /var/log/blink-clips.log 2>&1
#
# How the clips get here
# ----------------------
# Home Assistant's Blink integration polls Blink every 5 minutes, and an
# automation in docker/homeassistant/config/configuration.yaml calls
# blink.save_recent_clips into /config/blink, inside HA's own volume. That
# works the same whether the clips live in Blink's cloud (a subscription) or on
# a USB stick in a Sync Module 2 (local storage).
#
# HA writes to its own volume, never to the media drive, on purpose: a bind
# mount of the media drive into HA would either stop HA from starting when the
# drive is missing (create_host_path: false, as Jellyfin does) or quietly write
# clips onto the thin pool (the default). Neither is acceptable for the thing
# that runs the house. So this script is the only writer, and it refuses to
# write when the library is not there.
#
# What it does
# ------------
#   1. Moves each finished clip (unchanged for a minute) from the staging
#      directory to  <library>/cameras/<Camera>/<YYYY-MM>/<YYYY-MM-DD HH-MM-SS>.mp4.
#      Copy to a dotfile, then rename, so Jellyfin never indexes half a file.
#   2. Deletes clips older than HL_BLINK_RETAIN_DAYS (default 90; 0 keeps
#      everything). The media drive is backup=0, so this is the only copy on
#      the box; Blink's own copy expires on Blink's schedule.
#   3. Writes metrics and pings HEALTHCHECKS_BLINK_CLIPS_URL
#      (docker/monitoring/.env), like every other scheduled job here.
#
# A staging directory missing from HA's volume means the HA side was never
# deployed: the run is a no-op and says so as homelab_blink_configured 0. A
# missing volume is a failure.
#
set -uo pipefail

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$REPO/docker/monitoring/.env"

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$(dirname "$0")/healthchecks.sh"

# HA's named volume: compose project "homeassistant" + volume "homeassistant_config".
HA_VOLUME="${HL_HA_VOLUME:-homeassistant_homeassistant_config}"
LIBRARY="${HL_MEDIA_LIBRARY:-/media/jellyfin/library}"
DEST="${HL_BLINK_DIR:-$LIBRARY/cameras}"
RETAIN_DAYS="${HL_BLINK_RETAIN_DAYS:-90}"

if hc_url HEALTHCHECKS_BLINK_CLIPS_URL "$ENV_FILE"; then HC_URL=$HC_VALUE; else HC_URL=""; fi

STAGE="${HL_BLINK_STAGE:-}"
if [ -z "$STAGE" ]; then
  _mp=$(docker volume inspect -f '{{.Mountpoint}}' "$HA_VOLUME" 2>/dev/null || true)
  [ -z "$_mp" ] || STAGE="$_mp/blink"
fi

configured=0
moved=0
dupes=0

finish() {
  _code=$?

  metrics_open blink_clips

  metric_help homelab_blink_configured gauge \
    "1 if Home Assistant's Blink staging directory exists"
  metric homelab_blink_configured "$configured"

  metric_help homelab_blink_success gauge \
    "1 if the last blink-clips.sh run succeeded"
  if [ "$_code" -eq 0 ]; then metric homelab_blink_success 1; else metric homelab_blink_success 0; fi

  metric_help homelab_blink_moved_clips gauge \
    "Clips moved into the library by the last run"
  metric homelab_blink_moved_clips "$moved"

  if [ "$configured" -eq 1 ]; then
    metric_help homelab_blink_staged_files gauge \
      "Files still waiting in Home Assistant's staging directory"
    metric homelab_blink_staged_files \
      "$(find "$STAGE" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')"
  fi

  if [ -d "$DEST" ]; then
    metric_help homelab_blink_clips gauge "Clips in the library's cameras folder"
    metric homelab_blink_clips "$(find "$DEST" -type f -name '*.mp4' 2>/dev/null | wc -l | tr -d ' ')"

    metric_help homelab_blink_bytes gauge "Size of the library's cameras folder"
    metric homelab_blink_bytes "$(du -sb "$DEST" 2>/dev/null | cut -f1 || echo 0)"

    # From the files on disk, not from this run, so it survives a failed run.
    _newest=$(find "$DEST" -type f -name '*.mp4' -printf '%T@\n' 2>/dev/null | sort -n | tail -n1 | cut -d. -f1)
    if [ -n "$_newest" ]; then
      metric_help homelab_blink_newest_clip_timestamp_seconds gauge \
        "Modification time of the newest clip in the library"
      metric homelab_blink_newest_clip_timestamp_seconds "$_newest"
    fi
  fi

  metrics_close

  [ -z "$HC_URL" ] || hc_ping "$HC_URL/$_code" blink-clips || true
}
trap finish EXIT

if [ -z "$HC_URL" ]; then
  echo "blink-clips: $HC_REASON — nothing notices if this stops running" >&2
  hc_unarmed blink-clips
fi

# No volume (Docker not answering, or the volume or compose project renamed)
# is a fault: HA would keep saving clips that nothing moves. Only "volume
# there, no blink/ in it yet" means the HA side is not deployed, since
# homeassistant-init creates it on every `up`.
if [ -z "$STAGE" ]; then
  echo "$(date -Is) blink-clips: cannot find Docker volume $HA_VOLUME" >&2
  exit 1
fi
if [ ! -d "$STAGE" ]; then
  # docker/homeassistant/README.md → "Blink recordings". Quiet, because this
  # runs every five minutes.
  exit 0
fi
configured=1

shopt -s nullglob
pending=("$STAGE"/*.mp4)
if [ "${#pending[@]}" -eq 0 ] && [ "$RETAIN_DAYS" = 0 ]; then
  exit 0
fi

# The library lives on the media drive. If the drive is not mounted, the
# library directory is absent and nothing must be created in its place: the
# clips wait in staging and the run fails, which hl-blink-clips-failed reports.
if [ ! -d "$LIBRARY" ]; then
  echo "$(date -Is) blink-clips: $LIBRARY is missing (media drive not mounted?); ${#pending[@]} clip(s) left in staging" >&2
  exit 1
fi
mkdir -p "$DEST" && chmod 755 "$DEST" || exit 1

failed=0
# blinkpy names each clip ${created}_${name}.mp4: local time as YYYYMMDD_HHMMSS,
# then the camera name with everything but letters, digits and _ removed.
for src in "${pending[@]}"; do
  # Skip anything written to in the last minute; HA may still be saving it.
  [ -n "$(find "$src" -mmin +1 2>/dev/null)" ] || continue

  base=$(basename "$src")
  if [[ ! "$base" =~ ^([0-9]{4})([0-9]{2})([0-9]{2})_([0-9]{2})([0-9]{2})([0-9]{2})_(.+)\.mp4$ ]]; then
    echo "blink-clips: leaving $base in staging, not a name blinkpy writes" >&2
    continue
  fi
  y=${BASH_REMATCH[1]} m=${BASH_REMATCH[2]} d=${BASH_REMATCH[3]}
  hh=${BASH_REMATCH[4]} mm=${BASH_REMATCH[5]} ss=${BASH_REMATCH[6]}
  cam=${BASH_REMATCH[7]}

  dir="$DEST/$cam/$y-$m"
  out="$dir/$y-$m-$d $hh-$mm-$ss.mp4"
  mkdir -p "$dir" || { failed=1; continue; }
  chmod 755 "$DEST/$cam" "$dir" 2>/dev/null || true

  # The same clip can be saved twice (blinkpy re-lists a clip while motion is
  # still flagged). Identical bytes: drop the copy. Different bytes with the
  # same timestamp: keep both.
  if [ -e "$out" ]; then
    if cmp -s "$src" "$out"; then
      rm -f "$src"; dupes=$((dupes + 1)); continue
    fi
    out="$dir/$y-$m-$d $hh-$mm-$ss ($(date +%s%N | tail -c 7)).mp4"
  fi

  tmp="$dir/.$(basename "$out").part"
  if cp "$src" "$tmp" && chmod 644 "$tmp" && touch -r "$src" "$tmp" && mv "$tmp" "$out"; then
    rm -f "$src"
    moved=$((moved + 1))
  else
    rm -f "$tmp"
    echo "blink-clips: could not move $base to $out" >&2
    failed=1
  fi
done

if [ "$RETAIN_DAYS" != 0 ]; then
  find "$DEST" -type f -name '*.mp4' -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true
  find "$DEST" -mindepth 1 -type d -empty -delete 2>/dev/null || true
fi

[ "$moved" -eq 0 ] && [ "$dupes" -eq 0 ] || echo "$(date -Is) blink-clips: moved $moved, dropped $dupes duplicate(s)"
exit "$failed"
