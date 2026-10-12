# TV guide (iptv-org/epg)

Programme listings for Jellyfin's Live TV. It runs
[iptv-org/epg](https://github.com/iptv-org/epg)'s grabber for the channels in
Jellyfin's M3U playlists and serves the result as XMLTV at
`http://epg:3000/guide.xml`. The URL works only on `proxy_proxy`, where
Jellyfin is. There is no port and no Caddy route.

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
3. **Jellyfin** reads `guide.xml` through its XMLTV provider.

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

Then in Jellyfin, go to *Dashboard → Live TV → TV Guide Data Providers → + →
XMLTV*:
- **File or URL:** `http://epg:3000/guide.xml`
- Leave the rest at their defaults. Save, then run
  *Scheduled Tasks → Refresh Guide*.

**Keep `EPG_PLAYLISTS` in step with Jellyfin's tuners.** The guide only covers
the playlists listed there. After changing it, recreate the container to
rebuild the channel list. Then grab now, or wait for 01:00:

```bash
docker compose -f docker/epg/docker-compose.yml up -d --force-recreate
docker exec epg npm run grab -- --channels=public/channels.xml --output=public/guide.xml
```

## A source has gone dead

If one feed returns 404 for every request, its channels get no guide, even
when another site lists them. In `docker logs epg`, look for a run of `ERR: Request
failed with status code 404` against the same site, or the same prefix of
`site_id`. Add that source to `EPG_SKIP` in `.env`, as `<site>` or
`<site>/<site_id prefix>`. Setting it replaces the default, so keep
`i.mjh.nz/Flash/`. Then recreate the container and grab. The startup log line
names what it skipped.

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
| `hl-epg-empty` | Every scraper failed: `docker logs epg` shows `ERR:` per channel. Usually a site change, which the next nightly image pull often fixes, or the box being blocked. |
| Jellyfin cannot load `http://epg:3000/guide.xml` | The `epg` container is down, or the first grab has not finished. |
