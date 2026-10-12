# Downloads

A torrent client that can only reach the internet through the VPN, and the apps
that search for, download and file shows and movies into Jellyfin's library.
Design and reasoning: `docs/design/tsd-media-downloads.md`.

| App | Address | Does |
|---|---|---|
| Sonarr | `https://sonarr.$HOMELAB_DOMAIN` | shows: you pick a show, it fetches every episode, now and as they air |
| Radarr | `https://radarr.$HOMELAB_DOMAIN` | movies, the same way |
| Prowlarr | `https://prowlarr.$HOMELAB_DOMAIN` | the indexers (search sources), shared by both |
| FlareSolverr | none (API at `flaresolverr:8191`) | gets Prowlarr past Cloudflare's browser check on indexers that have one |
| qBittorrent | `https://torrent.$HOMELAB_DOMAIN` | the downloads themselves, inside the VPN |
| gluetun | none | the Proton VPN tunnel; qBittorrent has no other network |

LAN and tailnet only, like everything behind Caddy. Nothing is forwarded on the
router.

**What you download, and from where, is your call.** The VPN hides the traffic
from your ISP; it does not change what copyright law in your country allows.
This repo ships no indexers.

## 1. The VPN device (on m5)

```bash
pct set 100 -dev1 /dev/net/tun
pct config 100 | grep dev           # dev0 (GPU) and dev1: /dev/net/tun
pct reboot 100                      # ~1 minute; DNS too. Check DNS twice after.
```

On docker: `ls -l /dev/net/tun` lists a `c` (character) device.

## 2. Folders (on docker)

The apps run as uid/gid 1000 (`PUID`/`PGID` in `.env`), so they must own what
they write. Jellyfin runs as root and reads them regardless.

```bash
mkdir -p /media/jellyfin/downloads
chown -R 1000:1000 /media/jellyfin/downloads /media/jellyfin/library/movies /media/jellyfin/library/shows
```

## 3. Start it (on docker)

```bash
cd /root/homelab && git pull --ff-only
cp docker/downloads/.env.example docker/downloads/.env
chmod 600 docker/downloads/.env
nano docker/downloads/.env          # paste the key after WIREGUARD_PRIVATE_KEY=
docker compose -f docker/downloads/docker-compose.yml up -d
```

Paste the key with `nano`, never with `echo ... >>`: an `echo` puts the key in
your shell history. It comes from Google Password Manager (`hl-vpn.m5`).

Prove the VPN before anything else:

```bash
docker logs gluetun 2>&1 | grep -iE 'public ip|port forwarded' | tail -3
docker exec qbittorrent curl -s https://ipinfo.io/ip; echo     # a Proton IP
curl -s https://ipinfo.io/ip; echo                             # your home IP: must differ
```

Then make the rest of the lab pick up the new routes, probes and tiles:
`/deploy`, or `./scripts/deploy-plan.sh`. By hand it is a recreate of `caddy`
alone (check DNS twice after), of `prometheus`, and of `dashboard` (new icon
files).

## 4. qBittorrent

```bash
docker logs qbittorrent 2>&1 | grep -i 'temporary password'
```

Sign in at `torrent.` as `admin` with that password, then in *Tools → Options*:

- **Web UI:** set your own password (save it as `https://torrent.<domain>`).
  Tick **Bypass authentication for clients on localhost**. That is what lets
  gluetun hand qBittorrent each new forwarded port; nothing outside the
  container counts as localhost.
- **Downloads:** default save path `/data/downloads`.
- **BitTorrent → Torrent Queueing:** tick **Do not count slow torrents in
  these limits** (thresholds 2 KiB/s and 60 s are fine), and set maximum active
  downloads **5**, uploads **3**, torrents **8**. The defaults are 3 / 3 / 5,
  and a stalled torrent keeps its slot unless slow torrents are excluded, so a
  few dead releases stop everything behind them. On 2026-10-12, 39 entries
  sat *queued* behind the one that was moving.
  *Active torrents* counts seeding as well as downloading, so keep it above
  *downloads* or seeding will hold the downloads back.
- **BitTorrent → Seeding Limits:** ratio **1.0** or seeding time **20160**
  minutes (14 days), then **Stop torrent** (*Pause torrent* before
  qBittorrent 5). Sonarr and Radarr remove stopped, imported torrents
  themselves (*Remove Completed*, step 5). Choose *Stop* over either
  *Remove*: removing behind the apps' backs can beat their import.
  While a torrent seeds it costs no extra space (the library copy is a
  hardlink), but a file deleted from the library still fills the drive until
  its torrent goes.
- **Advanced:** Network interface **tun0**. Belt and braces: the namespace
  already has no other way out.

Save, then `docker compose -f docker/downloads/docker-compose.yml restart`.
*Options → Connection* should then show the forwarded port as the listening
port, and the status bar a green connection icon rather than a firewall one.

## 5. Prowlarr, Sonarr, Radarr

Each asks you to create a login on first visit (Authentication: **Forms**,
required for all addresses). Save each in Google Password Manager.

**Sonarr** (`sonarr.`):
- *Settings → Media Management*: add root folder **`/data/library/shows`**.
  Leave **Use Hardlinks instead of Copy** on.
- *Settings → Download Clients → + → qBittorrent*: host **`gluetun`**, port
  **8080**, your qBittorrent login, category `tv`.
- *Settings → Connect → + → Emby / Jellyfin*: host **`jellyfin`**, port
  **8096**, an API key from Jellyfin (*Dashboard → API Keys → +*). Tick
  *Update library*, and set **Map Paths From** `/data/library/` and **Map
  Paths To** `/media/`: Sonarr sees the library at `/data/library`, Jellyfin
  at `/media`, and without the mapping Jellyfin is told to scan a folder it
  does not have.
- *Settings → Download Clients → qBittorrent*: tick **Remove Completed**, so
  a torrent goes once it is imported and has finished seeding (step 4's
  seeding limit stops it, then Sonarr removes it).
- **Size caps** (*Settings → Quality*): the defaults have no real ceiling, and
  an untouched 1080p web release runs ~4 GB per 43-minute episode, ~60 GiB a
  season. On 2026-10-12 five seasons of one show came to 307 GiB. Units are
  **MB per minute of runtime**, so one number covers every episode length.
  Set **Max** on these rows; leave **Min** alone:

  | Quality | Preferred | Max | ≈ per 43-min episode |
  |---|---|---|---|
  | WEBDL-1080p | 15 | 35 | 0.6–1.5 GB |
  | WEBRip-1080p | 15 | 35 | 0.6–1.5 GB |
  | HDTV-1080p | 15 | 35 | 0.6–1.5 GB |
  | Bluray-1080p | 20 | 45 | 0.9–1.9 GB |

  *Preferred* only ranks releases that pass; *Max* rejects. Season packs are
  judged per episode (pack size ÷ episodes). Caps apply to the **next** grab:
  what is already queued keeps downloading until removed from *Activity →
  Queue*.
- **Prefer HEVC** (*Settings → Custom Formats → +*): name `x265`, condition
  *Release Title*, value `\b(x265|h\.?265|HEVC)\b`. Then in *Settings →
  Profiles*, open the profile your shows use and give `x265` a score of
  **100**. Same picture at about half the bitrate; Jellyfin transcodes it on
  the iGPU for anything that cannot play it.

**Radarr** (`radarr.`): the same, with root folder **`/data/library/movies`**
and category `movies`, and the same `x265` custom format and *Remove
Completed*. Size caps, still MB per minute, so a long film is allowed more:

| Quality | Preferred | Max | ≈ per 2-hour film |
|---|---|---|---|
| HDTV-1080p | 30 | 60 | 3.6–7 GB |
| WEBDL-1080p | 30 | 60 | 3.6–7 GB |
| WEBRip-1080p | 30 | 60 | 3.6–7 GB |
| Bluray-1080p | 30 | 60 | 3.6–7 GB |

In the profile (usually *HD-1080p*), untick **Remux-1080p** and every 2160p
quality: they have no cap, and a remux is 20–40 GB. If *Upgrades Allowed* is
on, set *Upgrade Until Custom Format Score* to 0, or Radarr re-downloads
every film it has to get an HEVC copy.

**Prowlarr** (`prowlarr.`):
- *Settings → Apps → + Sonarr*: Prowlarr server `http://prowlarr:9696`, Sonarr
  server `http://sonarr:8989`, Sonarr's API key (*Sonarr → Settings → General*).
  Then the same for **Radarr** at `http://radarr:7878`.
- *Settings → Indexers → + → FlareSolverr*: tag **`flaresolverr`**, host
  **`http://flaresolverr:8191/`**, *Test*, save.
- *Indexers → +*: your choice. Prowlarr pushes them to both apps. An indexer
  that fails with "blocked by CloudFlare Protection" gets the `flaresolverr`
  tag; only tagged indexers go through it, so leave the rest untagged (every
  FlareSolverr request starts a browser and takes seconds).

Then in Sonarr *Series → Add New*, or Radarr *Movies → Add New*, or ask in
Discord's #ask (`docker/concierge/README.md` → "Movies and shows"). A finished
download is hardlinked into the library, Jellyfin is told to scan, and it
appears there.

## Monitoring

| Signal | Where |
|---|---|
| **VPN down** | blackbox probe of gluetun's health server `http://gluetun:9999/`, `service=gluetun` (`hl-probe-down`). qBittorrent is offline while it is, by design |
| App not answering | probes of `/ping` on sonarr, radarr, prowlarr, `/health` on flaresolverr, and qBittorrent's web UI at `gluetun:8080` (`hl-probe-down`) |
| Container stopped | `homelab_container_running`. **Required only once `docker/downloads/.env` exists** (`SETUP_GATED_STACKS` in `heartbeat.sh`); until then nothing pages, probes included |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `up` fails: `error gathering device information ... /dev/net/tun` | Step 1 not done, or `dev1` lost from CT 100's config. |
| `up` fails: `bind source path does not exist: /media/jellyfin/...` | The media drive is not mounted into CT 100, or step 2 was skipped. Deliberate. |
| `qbittorrent` probe fails while `gluetun` is healthy | gluetun was restarted on its own (a crash, or `docker restart gluetun`); Compose's own replacements restart qBittorrent too (`depends_on … restart: true`). qBittorrent is still attached to the old network namespace and has no network at all. Recreate both together: `docker compose -f docker/downloads/docker-compose.yml up -d --force-recreate`. Never restart gluetun alone. |
| Downloads stuck at 0 B/s, firewall icon in qBittorrent | No forwarded port reached qBittorrent: check *Bypass authentication for clients on localhost* (step 4), then `docker logs gluetun 2>&1 \| grep -i 'port forward'`. |
| Most of the queue says *queued* at 0 B/s while one or two download, even with peers listed | qBittorrent's queue limits, not the network: see *Torrent Queueing* in step 4. Entries that still show 0 (0) seeds and peers once they get a slot are dead; remove them from Sonarr/Radarr's *Activity → Queue* with *Blocklist release* so a different release is searched for. |
| Sonarr/Radarr: "Import failed, path does not exist" | qBittorrent and the app disagree on the path. Both must see downloads at `/data/downloads`. |
| A search finds releases but grabs none; *Interactive Search* says "size too large" | The size caps in step 5. Raise **Max** on that quality a step, or accept it has no small release. |
| Files copied, not hardlinked (the drive fills twice as fast) | The root folder is not under `/data/library`, or *Use Hardlinks* is off. |
| Indexer: "blocked by CloudFlare Protection" | It is not tagged `flaresolverr`, or the FlareSolverr proxy in Prowlarr has no tag (then it applies to nothing). |
| FlareSolverr: "Captcha detected but no automatic solver is configured" | That site shows a real captcha, not just the browser check. FlareSolverr cannot solve those; use another indexer. |
| FlareSolverr: timeouts, `docker logs flaresolverr` shows Chrome crashing | Out of memory under its 1g cap (many solves at once). Fewer tagged indexers, or raise `mem_limit`. |
| `torrent.` gives 502 | qBittorrent is down, or Caddy was restarted rather than recreated after the route was added. |
