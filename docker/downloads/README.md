# Downloads

A torrent client that can only reach the internet through the VPN, and the apps
that search for, download and file shows and movies into Jellyfin's library.
Design and reasoning: `docs/design/tsd-media-downloads.md`.

| App | Address | Does |
|---|---|---|
| Sonarr | `https://sonarr.$HOMELAB_DOMAIN` | shows: you pick a show, it fetches every episode, now and as they air |
| Radarr | `https://radarr.$HOMELAB_DOMAIN` | movies, the same way |
| Prowlarr | `https://prowlarr.$HOMELAB_DOMAIN` | the indexers (search sources), shared by both |
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
  *Update library*.

**Radarr** (`radarr.`): the same, with root folder **`/data/library/movies`**
and category `movies`.

**Prowlarr** (`prowlarr.`):
- *Settings → Apps → + Sonarr*: Prowlarr server `http://prowlarr:9696`, Sonarr
  server `http://sonarr:8989`, Sonarr's API key (*Sonarr → Settings → General*).
  Then the same for **Radarr** at `http://radarr:7878`.
- *Indexers → +*: your choice. Prowlarr pushes them to both apps.

Then in Sonarr *Series → Add New*, or Radarr *Movies → Add New*. A finished
download is hardlinked into the library, Jellyfin is told to scan, and it
appears there.

## Monitoring

| Signal | Where |
|---|---|
| **VPN down** | blackbox probe of gluetun's health server `http://gluetun:9999/`, `service=gluetun` (`hl-probe-down`). qBittorrent is offline while it is, by design |
| App not answering | probes of `/ping` on sonarr, radarr, prowlarr, and qBittorrent's web UI at `gluetun:8080` (`hl-probe-down`) |
| Container stopped | `homelab_container_running`. **Required only once `docker/downloads/.env` exists** (`SETUP_GATED_STACKS` in `heartbeat.sh`); until then nothing pages, probes included |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `up` fails: `error gathering device information ... /dev/net/tun` | Step 1 not done, or `dev1` lost from CT 100's config. |
| `up` fails: `bind source path does not exist: /media/jellyfin/...` | The media drive is not mounted into CT 100, or step 2 was skipped. Deliberate. |
| `qbittorrent` probe fails while `gluetun` is healthy | gluetun was restarted or recreated on its own. qBittorrent is still attached to the old network namespace and has no network at all. Recreate both together: `docker compose -f docker/downloads/docker-compose.yml up -d --force-recreate`. Never restart gluetun alone. |
| Downloads stuck at 0 B/s, firewall icon in qBittorrent | No forwarded port reached qBittorrent: check *Bypass authentication for clients on localhost* (step 4), then `docker logs gluetun 2>&1 \| grep -i 'port forward'`. |
| Sonarr/Radarr: "Import failed, path does not exist" | qBittorrent and the app disagree on the path. Both must see downloads at `/data/downloads`. |
| Files copied, not hardlinked (the drive fills twice as fast) | The root folder is not under `/data/library`, or *Use Hardlinks* is off. |
| `torrent.` gives 502 | qBittorrent is down, or Caddy was restarted rather than recreated after the route was added. |
