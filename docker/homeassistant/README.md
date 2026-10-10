# homeassistant — the home hub

[Home Assistant](https://www.home-assistant.io/) on CT 100: the one place the
house's doors, locks, sensors, cameras and alarm come together. Phase 2 of
[`tsd-personal-assistant.md`](../../docs/design/tsd-personal-assistant.md),
the read-only half of it. The digest reads the states you choose and puts a
**Home** line in its Today section:

```
**Home** Garage Door open 2h · Basement Leak unavailable 3d
**Home** nothing open, unlocked or offline (6 watched)
```

| | |
|---|---|
| UI | `https://ha.$HOMELAB_DOMAIN`, and directly at `http://10.0.0.201:8123` (host networking). LAN + tailnet only, never the Cloudflare Tunnel |
| Read by | the assistant, at `http://host.docker.internal:8123` (the host, via `extra_hosts`) |
| State | the `homeassistant_config` volume (users, tokens, integrations, history) |
| Owned by git | `config/configuration.yaml` only, mounted read-only. Reverse-proxy trust is a UI setting now: see "Fresh volume" |
| Networking | `network_mode: host`, for HomeKit, Matter and discovery. See "Host networking" |
| Updates | by hand. On `HL_NO_AUTOPULL`, because HA's database migrations are one-way |

## Bring it up

HA runs on host networking (see "Host networking"), so it needs no other
stack to start. Caddy reaches it through the host.

```bash
docker compose -f docker/homeassistant/docker-compose.yml up -d
docker compose -f docker/proxy/docker-compose.yml up -d --force-recreate caddy   # picks up the ha. route
```

Recreating `caddy` alone leaves AdGuard, and so the house's DNS, untouched.
`/deploy` works out the rest.

**Deploy it soon after merging.** `heartbeat.sh` counts every `container_name`
in `docker/*/docker-compose.yml` as must-be-running, so an undeployed
`homeassistant` raises `hl-container-missing`, which is the right alarm for a
container that should be there.

## First run

1. Open `https://ha.$HOMELAB_DOMAIN` and create your owner account. Use
   a strong password: this account can do anything in the house.
2. Add your integrations under **Settings → Devices & services**. With host
   networking, discovery finds most local devices (HomeKit, Matter, Chromecast,
   …) on its own. Cloud integrations (most cameras, smart locks, alarm systems)
   just need a sign-in. See "Host networking" below.

That works on the volume this lab already has. **On a fresh volume, step 1
answers 400 instead.** See below.

### Fresh volume: setting the reverse-proxy trust

HA must trust Caddy's `X-Forwarded-For` header, or every request through
`ha.$HOMELAB_DOMAIN` is a **400 Bad Request**. That is a UI setting now: HA
moved the http integration out of YAML and stops reading it in 2027.2. The
existing volume has it. A new one (say, after losing the volume, since there
are no backups yet) does not. Host networking makes the fix direct: a request
straight to the box carries no `X-Forwarded-For`, so it is never refused.

1. Open `http://10.0.0.201:8123` from the LAN or tailnet and onboard.
2. **Settings → System → Network → Reverse proxy:** turn on **Trust
   X-Forwarded-For** and add **Trusted proxies** `172.16.0.0/12` and
   `192.168.0.0/16`. These are Docker's default address pools, which is where
   Caddy's requests come from. The proxy network's subnet is not pinned
   anywhere, so the whole range is trusted.
3. **Save** (it restarts HA). `https://ha.$HOMELAB_DOMAIN` now answers.
   Continue with "First run" step 2.

## Connect the digest

The digest reads states with a token. **Home Assistant has no read-only
token**: any token, from any user, can also unlock and open. So the digest
gets a user of its own, as narrow as HA allows:

1. **Settings → People → Users → Add user.** Name it `digest`, leave
   **Administrator** off, and turn on **Can only log in from the local
   network** if you like. Non-admin means it cannot change HA's configuration,
   add integrations or create users. It can still read and operate entities,
   which is the part HA cannot restrict.
2. Log in as `digest` once (a private window is easiest). Then open **Profile →
   Security → Long-lived access tokens → Create token**, name it
   `homelab-digest`, and copy it. HA shows it only once.
3. Pick the entities to watch: **Settings → Devices & services → Entities**.
   Copy each **Entity ID** (`cover.garage_door`, `lock.front_door`,
   `binary_sensor.back_door`, …).
4. In `docker/assistant/.env`:

   ```
   HA_TOKEN=eyJ...
   HA_ENTITIES=cover.garage_door,lock.front_door,binary_sensor.back_door
   ```

5. Check it, then apply it:

   ```bash
   docker compose -f docker/assistant/docker-compose.yml run --rm --build assistant --selftest
   docker compose -f docker/assistant/docker-compose.yml up -d --build --force-recreate
   ```

### What counts as worth a mention

Decided in [`app/home.py`](../assistant/app/home.py) by HA's own domain and
device class. Never by the model, which never sees any of it.

| Entity | Mentioned when |
|---|---|
| `binary_sensor` door, garage door, window, opening | open |
| `binary_sensor` lock | unlocked |
| `binary_sensor` moisture, smoke, gas, CO, safety, problem, tamper | on |
| `binary_sensor` battery | low |
| `cover` | open or opening |
| `lock` | unlocked, open, jammed |
| `alarm_control_panel` | triggered |
| `sensor` with device class battery | below 20% |
| `climate` (thermostat) | current temperature below 55 °F or above 85 °F, in whichever unit HA is set to (°C is converted); no temperature reading at all, as **temperature not read**; or HVAC mode `off` |
| anything | `unavailable` or `unknown`, as **unavailable** |
| an entity ID HA does not know | always, as **not found**. Usually a typo or a renamed device |

Motion, occupancy and presence are never mentioned: someone being home is not
an event. With nothing to mention, the line says how many entities it checked.

A thermostat's "for 2h" appears only when it is **off**. HA's `last_changed`
tracks the mode, not the temperature, so "at 52°F 3d" would be a false claim.

### Calendars

`HA_CALENDARS` names `calendar.*` entities, such as the iCloud calendars added
through the CalDAV integration (**Settings → Devices & services → CalDAV →
entities**). Their events merge into the digest's **Calendar** line with
Google's. They need the same `digest` token and nothing else in HA: reading a
calendar needs no admin rights.

```
HA_CALENDARS=calendar.home,calendar.work
```

**If HA does not answer, the line says `_not read_`, never "nothing open".**
A rejected token or a dead container fails the whole read rather than
rendering part of the house as quiet. `hl-google-stale` (named before HA was a
source) alerts once the Home source has gone 25h without a good read.

## Host networking

HA runs with `network_mode: host`, switched from a bridge on 2026-10-10.
HomeKit pairing, Matter and local discovery all depend on mDNS/SSDP multicast
on the LAN, which a Docker bridge does not carry. (Matter also needs a Matter
Server container next to HA, which this lab does not run yet. Host networking
is the part it needs from this stack.) The first device that needed
it was the Honeywell T6 Pro Smart thermostat. HomeKit is its only local
integration, and Resideo's cloud API would not issue developer access.

**What it changes:**

- **Nothing reaches HA by container name.** A host-network container joins no
  Docker network. Caddy, blackbox-exporter and the assistant reach it as
  `host.docker.internal:8123`. Each of their compose files maps that name to
  the host with `extra_hosts: "host.docker.internal:host-gateway"`. Their
  requests arrive from their own 172.x bridge addresses, which the trusted
  proxy range already covers.
- **:8123 is open on the LAN and tailnet.** Anything that can reach
  10.0.0.201 can reach HA's login page directly, not only through Caddy. It is
  still not public: the box has no inbound route from the internet, and HA is
  never on the Cloudflare Tunnel. It is an exception to the repo's "bind to
  127.0.0.1 by default" rule, listed under Port bindings in `AGENTS.md`.
  Hand out `https://ha.$HOMELAB_DOMAIN`: the direct port is plain HTTP, so a
  login there crosses the LAN unencrypted. Keep it for the fresh-volume
  bootstrap.
- **Every container on the box can reach HA.** Before, only containers on
  `proxy` could. Now any bridge can, through the host (`172.17.0.1:8123`),
  the Kali webtop and Open WebUI included. HA still asks them for a login,
  but they sit inside the trusted proxy range, so they could forge
  `X-Forwarded-For` and dodge an IP ban.
- **HA can reach the host's loopback**, including PostgreSQL on
  `127.0.0.1:5432`. Postgres still needs its credentials.

### Exposure

Turn on **Settings → System → Network → IP banning** with a low login-attempt
threshold. Because Caddy's `X-Forwarded-For` is trusted, bans apply to the
client address for requests through `ha.` too, not to Caddy's.

**On the tailnet, that address is the Proxmox host's.** Tailscale runs on the
host as a subnet router and SNATs, so every tailnet device (your phone away
from home, the companion app) arrives as the same LAN address. Five typos
from any of them bans all of them, and HA has no allow list to exempt it.
So use a higher threshold (10 or more) rather than 5. To unban, delete the
address's entry from `ip_bans.yaml` in the volume and restart HA:

```bash
docker exec -it homeassistant vi /config/ip_bans.yaml
docker restart homeassistant
```

### Pairing a HomeKit device

1. If the device is in **Apple Home**, remove it there first. A HomeKit
   accessory pairs with one controller at a time.
2. **Settings → Devices & services.** HA discovers it on its own. Or use
   **+ Add integration → HomeKit Device**.
3. Enter the 8-digit setup code from the device's label (`XXX-XX-XXX`).

The maker's app (Resideo, for the thermostat) keeps working alongside.

### Rolling back to a bridge

Revert this change. That puts back `networks: [homeassistant, proxy]` and
container-name addressing in the Caddyfile, prometheus.yml and the assistant.
Then recreate `homeassistant`, `caddy`, `blackbox-exporter`, `prometheus` and
`assistant`. HomeKit and Matter devices stop updating on a bridge, but HA keeps
their pairings, so switching back to host mode later restores them.

## Updating

Not automatic. Read the [release notes](https://www.home-assistant.io/blog/categories/release-notes/)
for breaking changes first, then:

```bash
docker compose -f docker/homeassistant/docker-compose.yml pull
docker compose -f docker/homeassistant/docker-compose.yml up -d
```

## Backups

**None yet**, like the rest of the lab (`AGENTS.md` → Planned). Losing the
`homeassistant_config` volume means redoing onboarding, every integration
sign-in and every automation. HA's own **Settings → System → Backups** can
make an archive inside the volume, but that dies with it. Copy one off the box
until `tsd-backups-and-monitoring.md` lands.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ha.` answers **400 Bad Request** | HA does not trust Caddy's `X-Forwarded-For`: the reverse-proxy settings are missing (a fresh volume) or were changed in **Settings → System → Network**. Set them as in "Fresh volume" above. |
| HA starts in recovery mode | A YAML error in `configuration.yaml`, or an `!include` target missing from the volume. `docker compose ... up -d` reruns `homeassistant-init`, which recreates the missing ones. |
| Digest says **Home** `_not read_ (token rejected (401))` | The `digest` user's token was deleted or the user was removed. Make a new token, update `HA_TOKEN`, recreate the assistant, then `/digest`. |
| An entity shows **not found** | The entity ID in `HA_ENTITIES` is wrong, or the device was renamed in HA. |
| `ha.` answers **502** | Caddy cannot reach HA through the host. Check that `homeassistant` is running and that `caddy` was recreated with the `extra_hosts` line: `docker exec caddy getent hosts host.docker.internal` must print an address. |
| An edit to `configuration.yaml` did nothing | It is a single-file mount: `up -d --force-recreate homeassistant`, not `restart`. |
