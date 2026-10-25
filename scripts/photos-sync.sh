#!/usr/bin/env bash
#
# Mirror iCloud Photos onto the media drive with icloudpd (docker/photos/).
# Run from cron on the Docker LXC, nightly:
#
#   0 1 * * * /root/homelab/scripts/photos-sync.sh >> /var/log/photos-sync.log 2>&1
#
# And by hand, for the sign-in Apple asks for every couple of months:
#
#   ./scripts/photos-sync.sh --auth     # asks for the Apple ID password and 2FA code
#   ./scripts/photos-sync.sh            # then sync now, which also clears the alert
#
# Design: docs/design/tsd-photo-library.md. Setup: docker/photos/README.md.
#
# Download only, and that is the point
# ------------------------------------
# The mirror is what protects the library from a deletion nobody meant, so a
# photo deleted in iCloud stays here. icloudpd's --auto-delete,
# --delete-after-download and --keep-icloud-recent-days are never passed: the
# first deletes local files, the other two delete from iCloud. Anything that
# removes a photo from iCloud is a separate, reviewed step, never this job.
#
# Why it watches its own download
# -------------------------------
# The first sync is about 1.5 TB, and some Xfinity plans cap at about 1.2 TB a
# month. icloudpd has no byte limit of its own, so this script counts what the
# container has received (its eth0 rx_bytes, which is what the ISP counts too)
# and stops it at PHOTOS_MONTHLY_BUDGET_GB for the calendar month, or at
# PHOTOS_STOP_HOUR, whichever comes first. A stop is not a failure. icloudpd
# skips what is already on disk and resumes a half-written .part file, so the
# next night carries on where this one stopped. Once a month's budget is spent,
# runs fetch only the last 7 days (new photos stay backed up; the backfill
# waits for the 1st). The count is sampled every 30 seconds, so a run can
# overshoot by one interval's traffic; the default budget leaves room for that.
#
# Not configured = not running, and not paging
# --------------------------------------------
# Until docker/photos/.env sets ICLOUD_USERNAME this exits 0 and reports
# homelab_photos_configured 0, as offsite-backup.sh does before restic.env.
# Once it is set the job is real: the media drive must be mounted, the sign-in
# must be valid, and a failure alerts as hl-photos-sync-failed.
#
set -uo pipefail

REPO="${HL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$REPO/docker/monitoring/.env"
STACK_DIR="$REPO/docker/photos"
COMPOSE="$STACK_DIR/docker-compose.yml"
PHOTOS_ENV="$STACK_DIR/.env"
# The bind-mount source in docker/photos/docker-compose.yml; keep them equal.
LIBRARY="${HL_PHOTOS_DIR:-/media/photos/icloud}"
STATE_DIR="${HL_PHOTOS_STATE_DIR:-/var/lib/homelab/photos}"
NAME="${HL_PHOTOS_CONTAINER:-photos-icloudpd}"
POLL_SECONDS="${HL_PHOTOS_POLL_SECONDS:-30}"

# shellcheck source=scripts/metrics.sh
. "$(dirname "$0")/metrics.sh"
# shellcheck source=scripts/healthchecks.sh
. "$(dirname "$0")/healthchecks.sh"

# env_get <VAR>: the last value of VAR in docker/photos/.env, quotes stripped.
# Read rather than sourced, so a stray line in .env cannot run as code here.
env_get() {
  [ -f "$PHOTOS_ENV" ] || return 0
  sed -n "s/^$1=//p" "$PHOTOS_ENV" | tail -n1 \
    | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

USERNAME=$(env_get ICLOUD_USERNAME)
BUDGET_GB=$(env_get PHOTOS_MONTHLY_BUDGET_GB)
STOP_HOUR=$(env_get PHOTOS_STOP_HOUR)
case "$BUDGET_GB" in ''|*[!0-9]*) BUDGET_GB=500 ;; esac
case "$STOP_HOUR" in ''|*[!0-9]*) STOP_HOUR=7 ;; esac
[ "$STOP_HOUR" -le 23 ] || STOP_HOUR=7
BUDGET_BYTES=$((BUDGET_GB * 1000000000))

# The icloudpd arguments, the one copy of them. Positional parameters rather
# than an array: CI checks every script with `sh -n` (dash).
#
#   --folder-structure {:%Y/%m}   a folder per month, useful without any app
#   --file-match-policy name-id7  every file carries its iCloud asset id, so
#                                 two photos never share a name and a file can
#                                 be traced back to the asset it came from.
#                                 Changing this later re-downloads everything.
#   --xmp-sidecar                 keeps favourites, titles, keywords and
#                                 location next to each file, which is what
#                                 survives if a photo ever leaves iCloud
#   --password-provider console   no stored password. Under cron there is no
#                                 stdin, so an expired session fails at once
#                                 instead of waiting on a prompt.
set_args() {
  # Global options first, then the user and its options: icloudpd's usage is
  # `icloudpd [GLOBAL] [COMMON] [--username <user> [COMMON]]`.
  set -- icloudpd \
    --log-level info \
    --no-progress-bar \
    --password-provider console \
    --mfa-provider console \
    --username "$USERNAME" \
    --directory /data \
    --cookie-directory /config \
    --folder-structure '{:%Y/%m}' \
    --file-match-policy name-id7 \
    --xmp-sidecar
  for _a in "$@"; do printf '%s\n' "$_a"; done
}

AUTH_HINT="on CT 100, run cd /root/homelab && ./scripts/photos-sync.sh --auth"

if [ "${1:-}" = "--auth" ]; then
  [ -n "$USERNAME" ] || { echo "photos-sync: set ICLOUD_USERNAME in $PHOTOS_ENV first" >&2; exit 2; }
  [ -d "$LIBRARY" ] || { echo "photos-sync: $LIBRARY is missing (media drive not mounted, or README step 1 not done)" >&2; exit 1; }
  # shellcheck disable=SC2046  # one argument per line, none contain newlines
  IFS='
'
  # Interactive on purpose: compose run gives it the terminal, so icloudpd can
  # ask for the password and the code Apple sends to your devices.
  docker compose -f "$COMPOSE" run --rm icloudpd $(set_args) --auth-only
  rc=$?
  unset IFS
  if [ "$rc" -eq 0 ]; then
    echo "photos-sync: signed in. Run ./scripts/photos-sync.sh to sync now and clear the alert."
  fi
  exit "$rc"
elif [ -n "${1:-}" ]; then
  echo "usage: $0 [--auth]" >&2
  exit 2
fi

configured=0
[ -n "$USERNAME" ] && configured=1

if hc_url HEALTHCHECKS_PHOTOS_URL "$ENV_FILE"; then HC_URL=$HC_VALUE; else HC_URL=""; fi

START=$(date +%s)
MONTH=$(date +%Y-%m)
month_used=0
run_rx=0
stopped=none
auth_needed=0
files_before=""

count_files() {
  find "$LIBRARY" -type f ! -name '*.xmp' ! -name '*.part' 2>/dev/null | wc -l | tr -d ' '
}

finish() {
  _code=$?

  if [ "$configured" -eq 1 ] && [ "$_code" -eq 0 ]; then
    mkdir -p "$STATE_DIR" 2>/dev/null && touch "$STATE_DIR/last-success" 2>/dev/null || true
  fi

  metrics_open photos_sync

  metric_help homelab_photos_configured gauge \
    "1 once docker/photos/.env names an Apple ID and the mirror is expected to run"
  metric homelab_photos_configured "$configured"

  if [ "$configured" -eq 1 ]; then
    metric_help homelab_photos_success gauge \
      "1 if the last photos-sync.sh run succeeded (a budget or deadline stop counts)"
    if [ "$_code" -eq 0 ]; then metric homelab_photos_success 1; else metric homelab_photos_success 0; fi

    metric_help homelab_photos_auth_needed gauge \
      "1 if the last run failed because Apple wants the sign-in done again"
    metric homelab_photos_auth_needed "$auth_needed"

    metric_help homelab_photos_stopped gauge \
      "1 for the reason the last run was stopped early: budget, deadline or none"
    for _r in none budget deadline; do
      if [ "$_r" = "$stopped" ]; then _v=1; else _v=0; fi
      metric homelab_photos_stopped "$_v" "$(metric_kv reason "$_r")"
    done

    metric_help homelab_photos_month_received_bytes gauge \
      "Bytes the sync has downloaded so far this calendar month"
    metric homelab_photos_month_received_bytes "$month_used"

    metric_help homelab_photos_month_budget_bytes gauge \
      "PHOTOS_MONTHLY_BUDGET_GB in bytes; 0 means no limit"
    metric homelab_photos_month_budget_bytes "$BUDGET_BYTES"

    metric_help homelab_photos_duration_seconds gauge "How long the last run took"
    metric homelab_photos_duration_seconds "$(( $(date +%s) - START ))"

    if [ -d "$LIBRARY" ]; then
      _files=$(count_files)
      metric_help homelab_photos_files gauge \
        "Photos and videos in the mirror (sidecars and partial files not counted)"
      metric homelab_photos_files "$_files"

      if [ -n "$files_before" ]; then
        metric_help homelab_photos_new_files gauge "Files the last run added"
        metric homelab_photos_new_files "$((_files - files_before))"
      fi

      metric_help homelab_photos_bytes gauge "Size of the mirror on the media drive"
      metric homelab_photos_bytes "$(du -sb "$LIBRARY" 2>/dev/null | cut -f1 || echo 0)"
    fi

    # From a marker file, not "now", so a failing run cannot hide the age of
    # the last good one.
    if [ -f "$STATE_DIR/last-success" ]; then
      metric_help homelab_photos_timestamp_seconds gauge \
        "When photos-sync.sh last succeeded"
      metric homelab_photos_timestamp_seconds \
        "$(stat -c %Y "$STATE_DIR/last-success" 2>/dev/null || echo 0)"
    fi
  fi

  metrics_close

  if [ "$configured" -eq 1 ] && [ -n "$HC_URL" ]; then
    if [ "$auth_needed" -eq 1 ]; then _body="Apple ended the iCloud session: $AUTH_HINT"
    else _body="stopped=$stopped received=$run_rx month=$month_used/$BUDGET_BYTES"; fi
    hc_ping "$HC_URL/$_code" photos-sync "$_body" || true
  fi
}
trap finish EXIT

if [ "$configured" -eq 0 ]; then
  # Quiet: an unset Apple ID is a setup step, not a fault (README.md).
  exit 0
fi

if [ -z "$HC_URL" ]; then
  echo "photos-sync: $HC_REASON — nothing notices if this stops running" >&2
  hc_unarmed photos-sync
fi

# The media drive. If it is not mounted, /media/photos is absent and nothing
# must be created in its place; compose would refuse anyway, but saying so
# here names the cause.
if [ ! -d "$LIBRARY" ]; then
  echo "$(date -Is) photos-sync: $LIBRARY is missing (media drive not mounted?)" >&2
  exit 1
fi

files_before=$(count_files)

# This month's running total. A new month starts from zero.
mkdir -p "$STATE_DIR" 2>/dev/null || true
if [ -f "$STATE_DIR/month" ]; then
  read -r _m _b < "$STATE_DIR/month" || true
  if [ "${_m:-}" = "$MONTH" ]; then
    case "${_b:-}" in ''|*[!0-9]*) ;; *) month_used=$_b ;; esac
  fi
fi

# Over budget: new photos only. The listing itself is a few MB.
extra=""
if [ "$BUDGET_BYTES" -gt 0 ] && [ "$month_used" -ge "$BUDGET_BYTES" ]; then
  # Newline-separated, like set_args' output: IFS is a newline where it is used.
  extra=$(printf '%s\n%s' --skip-created-before 7d)
  echo "$(date -Is) photos-sync: this month's ${BUDGET_GB} GB budget is spent; fetching the last 7 days only"
fi

# The deadline: the next PHOTOS_STOP_HOUR:00 after the start.
deadline=$(date -d "$(date +%F) $STOP_HOUR:00" +%s)
[ "$deadline" -gt "$START" ] || deadline=$((deadline + 86400))
# A cap for a run started by hand in the daytime, which would otherwise run
# until tomorrow morning:  HL_PHOTOS_MAX_MINUTES=10 ./scripts/photos-sync.sh
case "${HL_PHOTOS_MAX_MINUTES:-}" in
  ''|*[!0-9]*) ;;
  *) [ $((START + HL_PHOTOS_MAX_MINUTES * 60)) -ge "$deadline" ] \
       || deadline=$((START + HL_PHOTOS_MAX_MINUTES * 60)) ;;
esac

# A container left behind by a run that was killed with the script.
docker rm -f "$NAME" >/dev/null 2>&1 || true

IFS='
'
# shellcheck disable=SC2046,SC2086
if ! docker compose -f "$COMPOSE" run -d --name "$NAME" icloudpd $(set_args) $extra >/dev/null; then
  unset IFS
  echo "$(date -Is) photos-sync: could not start icloudpd" >&2
  exit 1
fi
unset IFS

while [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]; do
  _rx=$(docker exec "$NAME" cat /sys/class/net/eth0/statistics/rx_bytes 2>/dev/null || true)
  case "$_rx" in ''|*[!0-9]*) ;; *) run_rx=$_rx ;; esac

  if [ "$BUDGET_BYTES" -gt 0 ] && [ -z "$extra" ] \
     && [ $((month_used + run_rx)) -ge "$BUDGET_BYTES" ]; then
    stopped=budget
  elif [ "$(date +%s)" -ge "$deadline" ]; then
    stopped=deadline
  fi
  if [ "$stopped" != none ]; then
    echo "$(date -Is) photos-sync: stopping at the $stopped ($((run_rx / 1000000)) MB this run)"
    docker stop -t 20 "$NAME" >/dev/null 2>&1 || true
    break
  fi
  sleep "$POLL_SECONDS"
done

code=$(docker wait "$NAME" 2>/dev/null || echo 1)
logs=$(docker logs --tail 200 "$NAME" 2>&1 || true)
docker rm -f "$NAME" >/dev/null 2>&1 || true

month_used=$((month_used + run_rx))
printf '%s %s\n' "$MONTH" "$month_used" > "$STATE_DIR/month" 2>/dev/null || true

if [ "$stopped" != none ]; then
  # Our own stop: SIGTERM, or SIGKILL after the grace period. Not a failure.
  code=0
fi

if [ "$code" -ne 0 ]; then
  printf '%s\n' "$logs" | tail -n 20 >&2
  if printf '%s' "$logs" | grep -qiE 'password|authenticat|two-factor|two-step|2fa|mfa|security code'; then
    auth_needed=1
    echo "$(date -Is) photos-sync: icloudpd exited $code; Apple wants the sign-in again: $AUTH_HINT" >&2
  else
    echo "$(date -Is) photos-sync: icloudpd exited $code" >&2
  fi
  exit 1
fi

echo "$(date -Is) photos-sync: ok, $((run_rx / 1000000)) MB this run, $((month_used / 1000000000)) GB this month, stopped=$stopped"
exit 0
