# iCloud Photos mirror (icloudpd)

A nightly, download-only copy of iCloud Photos on the 5 TB media drive, at
`/media/photos/icloud/<YYYY>/<MM>/`. iCloud stays the primary copy; this is
the second one. Design: [`docs/design/tsd-photo-library.md`](../../docs/design/tsd-photo-library.md).

Nothing in this stack is ever `up`. `scripts/photos-sync.sh` starts icloudpd
with `docker compose run` at 01:00 from cron, watches it, and removes it when
it is done. The stack has no ports, no route and no external network.

## What it does each night

1. Lists the library and downloads anything not yet on disk, as originals
   (HEIC, MOV, the Live Photo pairs), each with an `.xmp` sidecar holding its
   favourite flag, title, keywords and location. Filenames carry the iCloud
   asset id (`--file-match-policy name-id7`), so two photos never collide.
2. Deletes nothing, here or in iCloud. A photo deleted in iCloud stays in the
   mirror.
3. Stops early, cleanly, at whichever comes first:
   - **`PHOTOS_MONTHLY_BUDGET_GB`** (default 500) downloaded this calendar
     month, counted on the container's network interface. This keeps the
     first ~1.5 TB under an Xfinity data cap: at the default it takes about
     three months. Once a month's budget is spent, runs fetch only photos
     from the last 7 days until the 1st.
   - **`PHOTOS_STOP_HOUR`** (default 7, so 07:00), so a backfill never runs
     into the day.

   The next night carries on: what is on disk is skipped, and a
   half-downloaded file is resumed.
4. Writes metrics (`homelab_photos_*`) and pings Healthchecks.

## Setup, once

All on **CT 100**, not the Proxmox host. `/media` is the media drive only
inside CT 100.

1. **Make the folder on the media drive.**
   ```bash
   df -h /media                     # must show the 5 TB drive (/dev/sdb1, ~4.6T)
   mkdir -p /media/photos/icloud
   ```
   If `df` shows the rootfs instead, stop: the drive is not mounted, and the
   compose file refuses to start without this folder on purpose.
2. **Name the account.**
   ```bash
   cd /root/homelab/docker/photos && cp .env.example .env && nano .env   # ICLOUD_USERNAME
   ```
3. **Advanced Data Protection.** On the iPhone: Settings → your name → iCloud
   → Advanced Data Protection. If it says **On**, icloudpd also needs
   *Access iCloud Data on the Web* switched on in the same screen. That
   reopens web access to the account, so decide it deliberately
   (tsd-photo-library.md → Security). If it says **Off**, nothing to do.
4. **Dead man's switch.** In Healthchecks, create a check `photos-sync`
   (period 1 day, grace 8 hours) and put its ping URL in
   `docker/monitoring/.env` as `HEALTHCHECKS_PHOTOS_URL`.
5. **Sign in.**
   ```bash
   cd /root/homelab && ./scripts/photos-sync.sh --auth
   ```
   It asks for the Apple ID password and the code Apple sends to your
   devices. The password is not stored. The session it creates lives in the
   `photos_session` volume.
6. **Schedule it**, then try a short run:
   ```bash
   ./scripts/install-cron.sh
   HL_PHOTOS_MAX_MINUTES=10 ./scripts/photos-sync.sh
   ls /media/photos/icloud/$(date +%Y)/$(date +%m) | head
   ```
   A run started by hand otherwise goes until the next `PHOTOS_STOP_HOUR`,
   or the month's budget.

## When Apple asks you to sign in again

Apple ends the session every couple of months. That night's run fails,
`hl-photos-sync-failed` pages `#alerts` with the command, and Healthchecks
reports the failure too. On CT 100:

```bash
cd /root/homelab && ./scripts/photos-sync.sh --auth
./scripts/photos-sync.sh    # optional: sync now, which also clears the alert
```

If signing in keeps failing with a correct password, Apple may have changed
its sign-in flow. Check icloudpd's releases for a fix and bump the pinned
image in `docker-compose.yml` (Renovate opens these PRs too).

## After the switch to fiber

Set `PHOTOS_MONTHLY_BUDGET_GB=0` in `.env`. No other change is needed; the
next night downloads without a limit until `PHOTOS_STOP_HOUR`.

## Checking on it

- **Log:** `/var/log/photos-sync.log`. One line per healthy run.
- **Metrics:** `homelab_photos_files`, `homelab_photos_bytes`,
  `homelab_photos_new_files`, `homelab_photos_month_received_bytes` against
  `homelab_photos_month_budget_bytes`, `homelab_photos_stopped{reason}`, and
  `homelab_photos_timestamp_seconds` (the last success).
- **Space:** the mirror shares the media drive with Jellyfin. The existing
  disk alerts cover `/mnt/media`.

## Backups

The media drive is `backup=0` and has no copy of its own. That is fine while
iCloud holds every photo, because the mirror *is* the copy. It stops being
fine the moment a photo exists only here (see the design's archiving
section): an archive needs its own second copy before anything leaves iCloud.
