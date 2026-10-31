---
name: media-stack
description: How the media stack works — Sonarr, Radarr, Prowlarr, qBittorrent behind the gluetun VPN, Jellyfin (libraries, transcoding, Live TV, Intro Skipper), playing on the TVs (Chromecast, Roku, Samsung), and where Otto and Home Assistant hook in. Use when touching docker/downloads/, docker/jellyfin/ or docker/epg/, or when a show, movie, download, queue, playback, casting or skip-credits question comes up.
---

# Media stack

The setup and every troubleshooting table live in
[`docker/downloads/README.md`](../../../docker/downloads/README.md),
[`docker/jellyfin/README.md`](../../../docker/jellyfin/README.md) and
[`docker/epg/README.md`](../../../docker/epg/README.md); the reasoning is in
`docs/design/tsd-media-downloads.md` and `docs/design/tsd-jellyfin.md`. Check
the matching troubleshooting row before diagnosing from scratch: most problems
so far were already in one.

## Paths: the rule everything else depends on

- **Downloads and the library share one drive** (`/media/jellyfin` on the
  5 TB disk), so an import is a hardlink, not a copy. Sonarr and Radarr see it
  as `/data/downloads` and `/data/library/...`; Jellyfin sees the library as
  `/media/...`. The Jellyfin connection in Sonarr/Radarr maps
  `/data/library/` → `/media/`.
- **Mounts use `create_host_path: false` on purpose.** A missing folder stops
  the container rather than writing onto CT 100's thin pool. Create the folder;
  never remove the guard.

## Downloads

- **qBittorrent has no network except gluetun's.** Never restart gluetun alone:
  recreate the whole stack, or qBittorrent is left with no network at all.
- **A stuck queue is usually qBittorrent's queue limits**, not the VPN: the
  limits are 5/3/8 with "Do not count slow torrents" on. Dead releases (0 seeds
  once they get a slot) get removed from Sonarr/Radarr's Activity → Queue with
  *Blocklist release* so another release is searched.
- **Size caps are MB per minute of runtime** and apply to the next grab only.
  Radarr's HD-1080p profile ships with Remux ticked, which bypasses the caps.
- **Fake and stuck downloads are Cleanuparr's job** (`cleanuparr.`): it
  removes them through the apps' APIs, blocklists and re-searches. Its Malware
  Blocker whitelists video and subtitle files
  (`docker/downloads/cleanuparr/whitelist.txt`); qBittorrent's excluded file
  names are the backstop. Check its *Events* page before removing anything by
  hand. Nyaa is for anime only (tagged); showRSS outranks the public trackers.
- **Indexers are Anthony's choice.** This repo ships none; only add the
  `flaresolverr` tag to an indexer Cloudflare actually blocks.

## Jellyfin

- **Transcoding is VA-API on the iGPU** (`/dev/dri/renderD128`).
  `hl-jellyfin-gpu-missing` fires if passthrough is lost; CPU at 200% in the
  container means it is transcoding on the CPU.
- **Skip intro/credits is the Intro Skipper plugin.** After it analyses
  episodes, Jellyfin's **Media Segment Scan** hands the timestamps to clients.
  It runs after library scans on its own; run it by hand if detection finished
  later. If the browser shows the skip button and the Roku doesn't, update the
  Roku app.
- **Live TV tuners are added by IP**, never by Detect My Devices (Jellyfin is
  on a bridge). Guide data is `http://epg:3000/guide.xml` from `docker/epg/`.
  Recordings go to `/recordings`.
- **Blink clips** land in the *Cameras* library from Home Assistant through
  `scripts/blink-clips.sh` (see the home-assistant skill).

## The TVs

- **Living Room TV** casts from Jellyfin in Chrome through **Google Cast**. If
  the cast list is empty, it was the Jellyfin page not loading Google's cast
  code: hard reload, try Incognito (an extension blocking `gstatic.com`), or
  switch the Google Cast version under Profile → Playback.
- **Roku:** its built-in casting is not usable by Jellyfin. With the Jellyfin
  app open, it appears as a second cast target.
- The living-room set is a Samsung (Tizen). There is no Fire stick.

## Where the rest of the lab hooks in

- **Otto (`docker/concierge/`)**: `media_search` and adding shows/movies from
  #ask, and `movie_night` (Jellyfin + a TMDB watchlist + Anthony's streaming
  services). Its Jellyfin key is admin-scoped, but the code only GETs.
- **Home Assistant**: `calendar.sonarr` (Remote Calendar from Sonarr's iCal
  link) feeds the dashboard, the digest and the *Sonarr - episode airing*
  automation, which adds a **Watch <episode>** item to Apple Reminders. Sonarr
  episodes are never counted as calendar conflicts (`HA_FREE_CALENDARS`).
- **Updates**: images are pinned by version and digest, so a new version
  arrives as a Renovate PR. Once merged, `repo-sync.sh` redeploys `downloads`
  and `jellyfin` at 04:00 (neither is on `HL_NO_AUTOHEAL`), so merging is
  deploying.
