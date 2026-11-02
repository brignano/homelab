# Jellyfin

Media server for the house, reached at `https://jellyfin.$HOMELAB_DOMAIN` over
the LAN and Tailscale. Design and reasoning: `docs/design/tsd-jellyfin.md`.

**Deploy order matters.** The stack refuses to start until both the GPU and the
media drive are in CT 100, on purpose: without the GPU every transcode lands on
the CPU, and without the drive the library would be written to the thin pool.

## 1. GPU (Proxmox host — needs no drive)

```bash
# On the Proxmox host (m5). The render node only, never all of /dev/dri.
getent group render                       # note the gid, e.g. 993 or 104
pct set 100 -dev0 /dev/dri/renderD128,gid=<that gid>
```

`pct reboot 100` applies it. **That restarts every stack, including `proxy`, so
the house's DNS is down for about a minute.** Pick a quiet moment, then check DNS
twice (`.claude/commands/deploy.md`).

Prove it inside CT 100 before Jellyfin exists:

```bash
ls -l /dev/dri/renderD128                 # present, owned by some gid
stat -c %g /dev/dri/renderD128            # → RENDER_GID in .env
docker run --rm --device /dev/dri/renderD128 \
  --entrypoint /usr/lib/jellyfin-ffmpeg/vainfo jellyfin/jellyfin:12.2 \
  --display drm --device /dev/dri/renderD128
```

`vainfo` should list `VAProfileH264*` and `VAProfileHEVCMain` with
`VAEntrypointEncSlice`. Those are the encode profiles transcoding needs.

## 2. Media drive (when the 5 TB drive arrives)

Format, mount and pass it in on the host: `docs/runbook-backups.md` §3. That
ends with `pct set 100 -mp0 /mnt/media,mp=/media,backup=0` and a reboot.

Then inside CT 100. Checking `mountpoint /media` here proves nothing: it is a
bind mount, so it is "a mountpoint" even when the host's drive is missing. Ask
for the drive's size instead:

```bash
df -h /media                               # must show ~4.5T, not CT 100's own disk
mkdir -p /media/jellyfin/library/{movies,shows,home-videos,music} /media/jellyfin/cache \
         /media/jellyfin/recordings
```

## 3. Start it

```bash
cp docker/jellyfin/.env.example docker/jellyfin/.env   # set RENDER_GID
docker compose -f docker/jellyfin/docker-compose.yml up -d
```

Then make the rest of the lab pick up what this change added. `/deploy`, or
`./scripts/deploy-plan.sh`, works this out for you. By hand it is:

```bash
# New jellyfin. route. The Caddyfile is a single-file mount, so RECREATE, and
# recreate caddy alone: the rest of proxy is AdGuard, the house's DNS.
docker compose -f docker/proxy/docker-compose.yml up -d --force-recreate caddy
# New probe target. prometheus.yml is a single-file mount too: recreate.
docker compose -f docker/monitoring/docker-compose.yml up -d --force-recreate prometheus
# New alert rule (hl-jellyfin-gpu-missing). provisioning/ is a directory
# mount, read at boot: a restart is enough.
docker compose -f docker/monitoring/docker-compose.yml restart grafana
# New icon FILE (jellyfin.svg). Homepage lists its public dir once at startup,
# so a new filename 404s until it is recreated.
docker compose -f docker/dashboard/docker-compose.yml up -d --force-recreate
```

Check DNS twice after touching `caddy` (`.claude/commands/deploy.md`).

Then in the web UI: create the admin user, add the libraries (`/media/movies`,
`/media/shows`, …), and under *Dashboard → Playback → Transcoding* choose
**VA-API** with device `/dev/dri/renderD128`. Enable hardware decoding for
H.264 and HEVC and hardware encoding. Set scheduled tasks (scans, trickplay) to
03:30.

## Live TV and DVR

Tuners and guide data are configured in the web UI (*Dashboard → Live TV*), not
in this repo. Two things in the compose file shape them:

- **Tuners are added by IP, never by *Detect My Devices*.** Jellyfin is on the
  `proxy_proxy` bridge, so HDHomeRun's discovery broadcast never reaches it. A
  tuner on the LAN is still reachable at its address (give it a DHCP
  reservation). M3U tuners are just a URL.
- **Recordings go to `/recordings`**, which is `/media/jellyfin/recordings` on
  the media drive. Set it under *Live TV → Recording path*. The library mount is
  read-only, so the DVR has nowhere else to write.
- **Guide data comes from `docker/epg/`**: add an XMLTV provider with URL
  `http://epg:3000/guide.xml`. It covers the channels in the playlists listed
  in `docker/epg/.env`, so keep those in step with the tuners here.

Live TV traffic leaves through the house's connection, not the VPN: only
qBittorrent is inside gluetun (`docker/downloads/`).

### Adding the recordings mount to a running box

The mount has `create_host_path: false` like the others, so **create the folder
before anything recreates Jellyfin**, or the new container will refuse to start:

```bash
df -h /media                                # the 5 TB drive, not CT 100's disk
mkdir -p /media/jellyfin/recordings
docker compose -f docker/jellyfin/docker-compose.yml up -d
```

There is a deadline: `repo-sync.sh` pulls at 04:00 and, finding the jellyfin
container older than the last commit to `docker/jellyfin/`, recreates it by
itself (`jellyfin` is not on `HL_NO_AUTOHEAL`). Without the folder that heal
fails every night and the drift metric climbs, so do the `mkdir` before the
first 04:00 after this change lands.

## Photo Archive and the HEIC previews

The **Photo Archive** library reads the iCloud mirror (`/photos/icloud`, from
`/media/photos`, mounted read-only). Jellyfin 12.2 does not index `.HEIC`, so
the mirror's HEIC photos (about 6,500 on 2026-11-01, most of the iPhone shots)
were missing from it. It does index JPG, NEF, PNG and MOV/MP4.

`scripts/photos-previews.sh` fills the gap. Nightly at 07:15, after the mirror
sync, it writes a JPEG of every HEIC to `/media/photos/icloud-previews/<YYYY>/<MM>/`
(2048 px long edge, quality 85, EXIF kept, file date = capture date).

- **Previews are derived copies, not originals.** The HEIC in the mirror (and
  iCloud) is the photo; a preview is a smaller JPEG for browsing. Deleting
  `icloud-previews` loses nothing, and the next run rebuilds it.
- **Incremental, and gentle.** Existing previews are skipped; one core at
  `nice 19`, stopped after 240 minutes (`PREVIEW_MAX_MINUTES`), so the first run
  of ~6,500 photos may take a few nights.
- **`icloud-hidden` is never read or written.**
- **Failure** warns `#alerts` as `hl-photos-previews-failed`, and a job that
  stops running (no success for 3 days) as `hl-photos-previews-stale`. There is
  deliberately no Healthchecks dead man's switch: an unarmed one pages as
  `hl-hc-ping-failing`, and these previews are rebuildable.

Setup, once, on CT 100:

```bash
mkdir -p /media/photos/icloud-previews     # the job is a no-op until this exists
cd /root/homelab && ./scripts/install-cron.sh
./scripts/photos-previews.sh               # first run now (counts: see below)
```

Then add the library in Jellyfin: *Dashboard → Libraries → Add Media Library →
Home Videos and Photos*, name **Photo Archive (HEIC previews)**, folder
`/photos/icloud-previews`, and untick the image fetchers and Screen Grabber.

Check: `find /media/photos/icloud -iname '*.heic' | wc -l` should match
`find /media/photos/icloud-previews -name '*.jpg' | wc -l`.

## Monitoring

| Signal | Where |
|---|---|
| Container stopped | `homelab_container_running{container="jellyfin"}`, automatic from this compose file. **Required only once `docker/jellyfin/.env` exists** (`SETUP_GATED_STACKS` in `heartbeat.sh`): until then it is expected to be down and nothing pages, including its probe. |
| App not answering | blackbox probe `http://jellyfin:8096/health` (`hl-probe-down`) |
| GPU passthrough lost from CT 100 | `homelab_jellyfin_gpu_present`, checked in CT 100 once `.env` exists (`hl-jellyfin-gpu-missing`) |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `up` fails: `bind source path does not exist: /media/jellyfin/...` | The media drive is not mounted into CT 100, or the folders were not created. This failure is deliberate. |
| `up` fails: `error gathering device information ... renderD128` | GPU passthrough not done, or lost after a host update. Step 1. |
| `required variable RENDER_GID is missing` | `.env` not created from `.env.example`. |
| Playback stutters, CPU at 200% in the container | Transcoding on CPU: check the VA-API setting, and `hl-jellyfin-gpu-missing`. |
| `up` fails: `bind source path does not exist: /media/jellyfin/recordings` | The recordings folder was never created. `mkdir -p /media/jellyfin/recordings`, then `up -d` again. |
| Live TV channel won't play | Check the newest `FFmpeg.LiveTv-*` log under *Dashboard → Logs*. A `403`/`404` from an M3U source means the link is dead or geo-blocked. |
| `jellyfin.` gives 502 | Jellyfin is down, or Caddy was restarted rather than recreated after the route was added. |
