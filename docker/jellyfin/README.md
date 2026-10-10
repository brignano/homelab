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

Follow `docs/design/tsd-backups-and-monitoring.md`, Phase 1 step 7. On the host:

```bash
pct set 100 -mp0 /mnt/media,mp=/media,backup=0
pct config 100 | grep mp0                 # must show backup=0
```

Inside CT 100:

```bash
mountpoint /media                          # must say "is a mountpoint"
mkdir -p /media/jellyfin/library/{movies,shows,home-videos,music} /media/jellyfin/cache
```

## 3. Start it

```bash
cp docker/jellyfin/.env.example docker/jellyfin/.env   # set RENDER_GID
docker compose -f docker/jellyfin/docker-compose.yml up -d
docker compose -f docker/proxy/docker-compose.yml up -d --force-recreate caddy
```

Caddy needs the **recreate** for the new `jellyfin.` route: the Caddyfile is a
single-file mount (`AGENTS.md` → Config mounts). Recreating `caddy` alone, not
the whole `proxy` stack, leaves AdGuard and the house's DNS running. Prometheus
needs `docker restart prometheus` for the new probe.

Then in the web UI: create the admin user, add the libraries (`/media/movies`,
`/media/shows`, …), and under *Dashboard → Playback → Transcoding* choose
**VA-API** with device `/dev/dri/renderD128`. Enable hardware decoding for
H.264 and HEVC and hardware encoding. Set scheduled tasks (scans, trickplay) to
03:30.

## Monitoring

| Signal | Where |
|---|---|
| Container stopped | `homelab_container_running{container="jellyfin"}`, automatic from this compose file |
| App not answering | blackbox probe `http://jellyfin:8096/health` (`hl-probe-down`) |
| GPU gone, transcodes on CPU | `homelab_jellyfin_gpu_present` (`hl-jellyfin-gpu-missing`) |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `up` fails: `bind source path does not exist: /media/jellyfin/...` | The media drive is not mounted into CT 100, or the folders were not created. This failure is deliberate. |
| `up` fails: `error gathering device information ... renderD128` | GPU passthrough not done, or lost after a host update. Step 1. |
| `required variable RENDER_GID is missing` | `.env` not created from `.env.example`. |
| Playback stutters, CPU at 200% in the container | Transcoding on CPU: check the VA-API setting, and `hl-jellyfin-gpu-missing`. |
| `jellyfin.` gives 502 | Jellyfin is down, or Caddy was restarted rather than recreated after the route was added. |
