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
| UI | `https://ha.$HOMELAB_DOMAIN`. LAN + tailnet only, never the Cloudflare Tunnel |
| Read by | the assistant, at `http://homeassistant:8123`, over the `proxy` network |
| State | the `homeassistant_config` volume (users, tokens, integrations, history) |
| Owned by git | `config/configuration.yaml` only, mounted read-only |
| Updates | by hand. On `HL_NO_AUTOPULL`, because HA's database migrations are one-way |

## Bring it up

The proxy stack must be up first: HA joins its network, so Caddy can route to it.

```bash
docker compose -f docker/homeassistant/docker-compose.yml up -d
docker compose -f docker/proxy/docker-compose.yml up -d --force-recreate caddy   # picks up the ha. route
```

Recreating `caddy` alone leaves AdGuard, and so the house's DNS, untouched.
`/deploy` works out the rest. Prometheus needs a recreate for the new probe,
and the dashboard a restart for the new tile icon.

**Deploy it soon after merging.** `heartbeat.sh` counts every `container_name`
in `docker/*/docker-compose.yml` as must-be-running, so an undeployed
`homeassistant` raises `hl-container-missing`, which is the right alarm for a
container that should be there.

## First run

1. Open `https://ha.$HOMELAB_DOMAIN` and create your owner account. Use
   a strong password: this account can do anything in the house.
2. Add your integrations under **Settings → Devices & services**. On this
   bridge network, discovery mostly will not find local devices on its own.
   Cloud integrations (most cameras, smart locks, alarm systems) just need a
   sign-in, and local ones need the device's IP. See "Why not host networking"
   below.

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
| anything | `unavailable` or `unknown`, as **unavailable** |
| an entity ID HA does not know | always, as **not found**. Usually a typo or a renamed device |

Motion, occupancy and presence are never mentioned: someone being home is not
an event. With nothing to mention, the line says how many entities it checked.

**If HA does not answer, the line says `_not read_`, never "nothing open".**
A rejected token or a dead container fails the whole read rather than
rendering part of the house as quiet. `hl-google-stale` (named before HA was a
source) alerts once the Home source has gone 25h without a good read.

## Why not host networking

Host networking (`network_mode: host`) is how HA's docs suggest running it,
for mDNS/SSDP auto-discovery. It is not used here, for three reasons:

- **It cannot join Docker networks.** Caddy and the assistant would have to
  reach HA through the host's address instead of by container name.
- **It publishes :8123 to the whole LAN.** On a bridge, HA is reachable only
  through Caddy.
- **What it buys is discovery,** and most of what a house has (cloud cameras,
  locks, alarm systems, the car) is set up by signing in, not discovered.

If an integration you need truly requires discovery or multicast (some
Chromecast and HomeKit setups), that is the time to revisit it, in its own
change.

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
| `ha.` answers **400 Bad Request** | The `http:` block in `config/configuration.yaml` is missing or not loaded. Recreate the container (it is a single-file mount). |
| HA starts in recovery mode | A YAML error in `configuration.yaml`, or an `!include` target missing from the volume. `docker compose ... up -d` reruns `homeassistant-init`, which recreates the missing ones. |
| Digest says **Home** `_not read_ (token rejected (401))` | The `digest` user's token was deleted or the user was removed. Make a new token, update `HA_TOKEN`, recreate the assistant, then `/digest`. |
| An entity shows **not found** | The entity ID in `HA_ENTITIES` is wrong, or the device was renamed in HA. |
| An edit to `configuration.yaml` did nothing | It is a single-file mount: `up -d --force-recreate homeassistant`, not `restart`. |
