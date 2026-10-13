# TV guide (iptv-org/epg)

Programme listings and a checked channel list for Jellyfin's Live TV. It runs
[iptv-org/epg](https://github.com/iptv-org/epg)'s grabber for the channels in
Jellyfin's M3U playlists and serves two files:

- `http://epg:3000/guide.xml`: the guide, as XMLTV.
- `http://epg:3000/playlist.m3u`: the same playlists, with only the channels
  whose streams answered at the last check. Jellyfin's M3U tuner reads this
  one, so channels that only spin never show up.

Both URLs work only on `proxy_proxy`, where Jellyfin is. There is no port and
no Caddy route.

## How it fits together

1. **At container start**, `bin/start.sh` runs `bin/make-channels.mjs`. It
   reads every playlist in `EPG_PLAYLISTS` and keeps each `tvg-id`. For every
   id, it looks for a listings site among the scrapers in the image
   (`/epg/sites`). It then writes `channels.xml` with one site per channel,
   preferring the sites that cover the most of the playlist and, within a
   site, the entry for the channel's own country. Sources in `EPG_SKIP` are
   left out (see "A source has gone dead" below). The ids for i.mjh.nz, the
   largest source, are checked against its live feeds. Ids that have gone
   stale are rewritten, and ids that are gone fall through to another site.
   The startup line reports both counts. The playlist's
   `tvg-id` is the guide's `xmltv_id`, so Jellyfin matches them without any
   mapping. If any playlist fails to answer, the previous list is kept.
2. **Grabs:** nightly at 01:00 (`EPG_CRON_SCHEDULE`), plus one at start if
   there is no guide yet. Each fetches `EPG_DAYS` days (default 3) and writes
   `guide.xml`.
3. **Stream check:** at container start and at the start of every grab,
   `bin/check-streams.mjs` tries every channel in `EPG_PLAYLISTS` the way a
   player would and writes `playlist.m3u` with the ones that answer (see
   "Why the channel list is checked").
4. **Jellyfin** reads `playlist.m3u` through its M3U tuner and `guide.xml`
   through its XMLTV provider.

Both files live in `/var/lib/homelab/epg` on CT 100. That folder is small, on the
rootfs, and included in the vzdump.

**Coverage is partial.** On 2026-10-12, 561 of the 1,109 channels in the US
and Pluto playlists had a guide source. Most of them are covered by i.mjh.nz
(Pluto, Plex, Samsung) and tvtv.us. The rest show "no information" in Jellyfin,
which is what happens when no site lists that channel.

## Set up

On CT 100:

```bash
cp docker/epg/.env.example docker/epg/.env      # EPG_PLAYLISTS = your tuner URLs
docker compose -f docker/epg/docker-compose.yml up -d
docker logs -f epg        # "make-channels: N of M playlist channels...", then the grab
```

The first grab can take an hour or more. It is finished when
`/var/lib/homelab/epg/guide.xml` exists.

Then in Jellyfin, point the tuner at the checked playlist. In *Dashboard →
Live TV → Tuner Devices*, edit the M3U tuner (or add one) and set **File or
URL** to `http://epg:3000/playlist.m3u`. It exists a few minutes after the
container starts (`docker logs epg 2>&1 | grep check-streams`).

Then add the guide: *Dashboard → Live TV → TV Guide Data Providers → + →
XMLTV*:
- **File or URL:** `http://epg:3000/guide.xml`
- Leave the rest at their defaults. Save, then run
  *Scheduled Tasks → Refresh Guide*.

**Keep `EPG_PLAYLISTS` in step with Jellyfin's tuners.** The guide only covers
the playlists listed there. After changing it, recreate the container to
rebuild the channel list. Then grab now, or wait for 01:00:

```bash
docker compose -f docker/epg/docker-compose.yml up -d --force-recreate
docker exec -d epg node /hl/grab-all.mjs
```

## A source has gone dead

If one feed returns 404 for every request, its channels get no guide, even
when another site lists them. In `docker logs epg`, look for a run of `ERR: Request
failed with status code 404` against the same site, or the same prefix of
`site_id`. Add that source to `EPG_SKIP` in `.env`, as `<site>` or
`<site>/<site_id prefix>`. Setting it replaces the default, so keep
`i.mjh.nz/Flash/`. Then recreate the container and grab. The startup log line
names what it skipped.

## Why the channel list is checked

iptv-org's playlists list every stream someone found once, and many no longer
play from here. On 2026-10-12, with the US list as the tuner, Pluto channels
behind `jmp2.uk` were answered 403 by Pluto's stitcher, WFTV's variant
playlists could not be parsed, and three Stirr channels answered 503. Jellyfin
shows every channel in its tuner whether it plays or not, so the channel list
filled with channels that only spin.

`bin/check-streams.mjs` checks each channel with the headers its playlist asks
for, or the browser user agent Jellyfin sends:

1. The URL answers with an HLS playlist, or with video.
2. For a master playlist, its first variant answers with a media playlist.
3. That playlist's first segment answers.
4. `ffprobe` finds a video stream in it. Steps 1–3 let through channels FFmpeg
   could not open, and Jellyfin's web player hung on each of them. `start.sh`
   installs `ffmpeg` from Alpine's packages on each start; if that fails, this
   step is skipped and the log says so.

Channels that pass go into `playlist.m3u`; the log line counts what was left
out and why. The file is replaced only when every playlist answered and at
least a fifth of the channels passed. Below that, the box is more likely
offline than the streams dead, and the previous list is kept.

It cannot catch a stream that plays for a minute and then stalls, such as a
Pluto channel freezing at an ad break. Those still need trying in Jellyfin.

- `EPG_STREAM_SKIP`: hosts to leave out without checking, such as `jmp2.uk`.
- `EPG_REQUIRE_GUIDE=true`: also leave out channels with no listings in the
  last grab's `guide.xml`, so every channel in Jellyfin has a filled row.
  Having a guide source is not enough: some sources grab nothing. Before the
  first grab, a guide source is required instead. A channel whose listings
  come back is listed again after the next nightly run.
- `EPG_LANGS=en,es`: keep only channels whose guide source is in one of these
  languages (the `lang` of its entry in `channels.xml`). The US playlist also
  carries Arabic-language channels; this leaves them out. Channels without a
  guide source are kept unless `EPG_REQUIRE_GUIDE` drops them.

Jellyfin re-reads the tuner on its daily *Refresh Guide* task, or straight
away with *Live TV → Refresh Guide Data*.

## Why the grab runs in batches

The grabber keeps everything it fetches until it writes the guide at the end,
so one process for every channel grows until it runs out of memory. With ~600
channels it died at request 1,185 of 1,842 with a 1.5 GB heap. `bin/grab-all.mjs`
splits `channels.xml` into batches of `EPG_BATCH` (default 50), runs the stock
grab once per batch, and merges the results into `guide.xml`. Each batch
returns its memory when it exits.

- If a batch fails, it is logged and its channels are left out of that night's
  guide.
- If every batch fails, `guide.xml` is not touched, so `hl-epg-stale` fires.
- A lock file stops a second grab, scheduled or by hand, while one is running.

It replaces the image's `pm2.config.js` with `bin/pm2.config.cjs`, which runs
the same three apps but uses this grab command.

## Deploy

`bin/` is a directory mount read once at container start. A change to either
script needs `up -d --force-recreate`. A plain `up -d` leaves the old process
running, because Compose does not hash files behind a mount (AGENTS.md →
"Config mounts").

The image is `:master`, so the nightly auto-pull keeps the scrapers current.
Listings sites change their pages often, and fixes land on master. Every update
recreates the container at 04:00, which rebuilds the channel list. It does
**not** grab again, because `start.sh` skips the startup grab once a guide
exists. Scraping the listings sites twice a night is how you get blocked.

## Monitoring

| Signal | Where |
|---|---|
| Container stopped | `homelab_container_running{container="epg"}`, automatic from this compose file. **Required only once `docker/epg/.env` exists** (`SETUP_GATED_STACKS` in `heartbeat.sh`). |
| Guide not updating | `homelab_epg_guide_timestamp_seconds`, guide.xml's mtime read from the host by `heartbeat.sh`. `hl-epg-stale` fires past 30h. Before the first grab finishes, the `.env`'s mtime stands in, so a grab that never worked still alerts. |
| Channel list not updating | `homelab_epg_playlist_timestamp_seconds`, playlist.m3u's mtime. `hl-epg-playlist-stale` fires past 30h. `homelab_epg_playlist_channels` counts the channels in it. |
| Guide empty | `homelab_epg_guide_programmes`. The grabber skips channels that fail and still writes the file, so a broken scraper keeps the mtime fresh. `hl-epg-empty` fires when the count is 0 for 2h. A partial guide is normal and does not page. |

The schedule runs inside the container (pm2), where cron and `install-cron.sh`
cannot see it. The file's age is its dead man's switch.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `grab-at-startup` exits with code 134; log shows `JavaScript heap out of memory` | Node's heap is too small for the grabber's database load. `NODE_OPTIONS` sets it to 1.5 GB under a 2g limit; raise both together if the database outgrows it. |
| `up` fails: `set EPG_PLAYLISTS` | `.env` not created from `.env.example`. |
| Log says `keeping the existing channel list` | A playlist URL did not answer at start. The previous list is used; recreate later to retry. |
| Guide shows "no information" for some channels | No listings site covers them (see Coverage), or Jellyfin has not refreshed: *Scheduled Tasks → Refresh Guide*. |
| `hl-epg-stale` | The grab is not finishing: `docker logs epg`, and whether the container is running. |
| Log shows `JavaScript heap out of memory` inside a batch | Lower `EPG_BATCH` in `.env` (e.g. 25). Exit 134 is the heap limit, exit 137 the container's `mem_limit`, then `up -d --force-recreate`. |
| `grab-all: another grab is running` | A grab is already going. A lock older than 12h is treated as left over and removed. |
| `hl-epg-empty` | Every scraper failed: `docker logs epg` shows `ERR:` per channel. Usually a site change, which the next nightly image pull often fixes, or the box being blocked. |
| A channel in Jellyfin spins and never plays | It passed the last check but has died since, or stalls after its first segment. It drops out at the next check if it is dead. |
| Log says `only N of M streams answered; keeping the existing playlist` | Most streams failed at once, which usually means the box's internet connection, not the streams. The previous list stays. |
| Jellyfin cannot load `http://epg:3000/guide.xml` | The `epg` container is down, or the first grab has not finished. |
