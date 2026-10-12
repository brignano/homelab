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
| Voice | Alexa, through Matterbridge over the LAN ([`../matterbridge/`](../matterbridge/README.md)). Echos as speakers through Alexa Media Player (HACS): see "Echos as speakers" |
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

## Alexa

Alexa controls HA through **[Matterbridge](../matterbridge/README.md)**. It
offers the entities you label **Alexa** to an Echo as Matter devices, over the
LAN. It's free, and HA stays off the internet. Setup, what Alexa may see, and
pairing are all in that README.

**A "Discovered: matter" card in Settings → Devices & services is
Matterbridge. Click Ignore, never Add.** Both containers are on host
networking, so HA hears the mDNS announcement Matterbridge makes for the Echo
and offers to add it. Adding it would bring your own entities back into HA
through Matter as duplicates. It would also fail: HA's Matter integration
needs a Matter Server container, which this lab does not run. Ignoring it
changes nothing for Alexa, since the Echo pairs with Matterbridge directly.
Ignored on 2026-10-11. There is nothing to do afterwards. If the card comes
back after a Matterbridge rebuild, ignore it again. To see the ignored entry,
use the ⋮ menu on the integrations page → ignored integrations. You would
only add the Matter integration for real Matter devices, and it needs a
`python-matter-server` stack first. Matterbridge's card stays ignored even
then.

Why not the other routes:

- **Home Assistant Cloud (Nabu Casa)** works and needs no container, but it
  is a paid subscription and relays every command through Nabu Casa. If you
  ever switch to it, unlink Matterbridge first, or every device shows up twice.
- **A self-built Alexa skill (AWS Lambda)** needs HA on public HTTPS for the
  Lambda and for Alexa's account linking. That means putting `ha.` on the
  Cloudflare Tunnel, which this stack rules out.
- **`emulated_hue`** stays on the LAN, but newer Echos only look for a Hue
  bridge on port 80, which Caddy holds. It also only does on/off and dimming.

### Echos as speakers: Alexa Media Player

The section above is Alexa controlling HA. This is the other direction: HA
making an Echo speak ("garage has been open 2h"). Matter cannot do that.
[Alexa Media Player](https://github.com/alandtse/alexa_media_player) can. It is
a **community integration on Amazon's unofficial API**, installed through
HACS. Amazon can break it without notice, so nothing that matters may depend
on an Echo saying it: Discord and Grafana alerts stay the real channels.

**It holds your Amazon password.** The config entry stores the password and the
2-step-verification key in `.storage/core.config_entries` in the volume, and
both are enough to sign in as you, purchases included. Treat the volume, and
any backup copied off the box, as an Amazon credential. Only an HA
**administrator** can see config entries, so the `digest` user cannot read it.

#### 1. Install HACS (once)

HACS is a downloader for community integrations. It writes to
`/config/custom_components` in the volume, not to git:

```bash
docker exec -it homeassistant bash -c 'wget -O - https://get.hacs.xyz | bash -'
docker restart homeassistant
```

`restart` is right here: nothing git owns changed. Then **Settings → Devices &
services → + Add integration → HACS**, accept the prompts, and authorize it
with the GitHub device code it shows.

#### 2. Install Alexa Media Player

1. **HACS → search "Alexa Media Player" → Download.**
2. Restart HA **twice**: the first restart installs its `alexapy` dependency,
   the second loads it.

#### 3. Get Amazon's authenticator key

The integration signs in with an authenticator-app key, not SMS codes.

1. Open `https://www.amazon.com/a/settings/approval` (your Amazon domain).
2. **Add a new app → Can't scan the barcode.** Copy the 52-character key under
   "Enter your key". Store it in your password manager: a reinstall needs it.
3. Amazon asks for a code to finish. Add the same key to your usual
   authenticator app and enter the code it shows. The phone and HA then both
   generate valid codes.

#### 4. Add the integration

**Settings → Devices & services → + Add integration → Alexa Media Player.**
Enter your Amazon email, password and the **Amazon 2SV Authenticator App
Key**. For the **Public URL**, use `https://ha.$HOMELAB_DOMAIN/`. The login
runs through a proxy in HA, and your browser has to reach that URL to finish.
LAN or tailnet is enough. HA never needs to be on the internet for this.

Each Echo then shows up as a `media_player.*` entity.

#### 5. Make it talk

In each Echo's settings in the **Alexa app**, turn **Communications** on.
Without it, announcements are dropped silently. Do Not Disturb also silences
them without an error.

An automation action, for an announcement with the chime first:

```yaml
action: notify.alexa_media
data:
  message: "The garage door has been open for an hour."
  target:
    - media_player.kitchen_echo
  data:
    type: announce
    method: speak
```

`type: tts` speaks without the chime. `type: push` sends a notification to the
Alexa app instead. `method: speak` keeps Echo Shows from putting the text on
screen.

#### What it changes

- **It logs out now and then.** Amazon expires sessions, and a restart can ask
  for a CAPTCHA, which shows up as an HA notification. Until you finish it, the
  Echos are `unavailable`. No alert covers it, which is one more reason to
  keep it to nice-to-haves.
- **HA upgrades can break it.** It is not in HA's own test suite. After an HA
  update, check **HACS** for an Alexa Media Player update, and check that the
  Echos are not `unavailable` (see "Updating").
- **Outbound only**, to Amazon's API. It opens no port.

### Asking Alexa questions: spoken reports

Matter gives Alexa switches, lights, thermostats and sensors, and nothing
else. Alexa has no way to ask Home Assistant "what's the Tesla's battery?"
or "how's Jax?", and a routine cannot speak a value it read. So a question
takes three parts:

1. **A report script in HA** that reads the entities and speaks the answer
   on the Echo you just talked to, through Alexa Media Player.
2. **The `Alexa` label on that script.** Matterbridge offers it to Alexa as
   a switch that turns itself back off.
3. **Alexa routines**, one per phrase: **When you say** "car report" →
   **Smart Home** → the report's switch → **On**. Use short command phrases
   Alexa has no answer of its own for: "car report", "check the car", "Jax
   report". Questions lose to Alexa's built-in answers: "what's the Tesla's
   battery" read out Tesla's stock price instead of running the routine
   (2026-10-12). Phrases cannot contain punctuation, so no apostrophes. There
   is no catch-all: a phrase with no routine goes to Alexa's own answers.

Reports so far: **Jax report** (UI-managed, reads the `jax_*` sensors in
`configuration.yaml`) and **Tesla report**, below. Scripts live in the
volume's `scripts.yaml`, not git. Paste this one at **Settings → Automations
& scenes → Scripts → + Add script → ⋮ → Edit in YAML**:

```yaml
alias: Tesla report
mode: single
sequence:
  - variables:
      # The Echo that heard the routine phrase. Alexa Media Player marks it.
      echo: >-
        {{ states.media_player | selectattr('attributes.last_called', 'eq', true)
           | map(attribute='entity_id') | list | first | default('') }}
      level: "{{ states('sensor.spaceship_battery_level') }}"
      range: "{{ states('sensor.spaceship_battery_range') }}"
      charging: "{{ states('sensor.spaceship_charging') }}"
      message: >-
        {% if level in ['unknown', 'unavailable'] %}I can't reach the car right now.
        {%- else %}The Tesla is at {{ level | float(0) | round(0) | int }} percent,
        about {{ range | float(0) | round(0) | int }} miles of range
        {%- if charging == 'charging' %}, and it's charging{% endif %}.{% endif %}
  - condition: template
    value_template: "{{ echo != '' }}"
  - action: notify.alexa_media
    data:
      message: "{{ message }}"
      target: "{{ [echo] }}"
      data:
        type: tts
```

The entity IDs assume the car is named **Spaceship** in the Tesla Fleet
integration. If **Settings → Entities** shows other IDs, change the three
`sensor.spaceship_*` lines. The values are the car's last report: Tesla
Fleet does not wake a sleeping car to read them.

## Blink recordings

Every Blink clip lands in Jellyfin as **Cameras → <camera> → <month>**, kept
for 90 days. Two halves:

- **Home Assistant** saves each new clip. The Blink integration polls every
  5 minutes, and the `blink` automation in `config/configuration.yaml` calls
  `blink.save_recent_clips` into `/config/blink`, inside HA's own volume.
- **`scripts/blink-clips.sh`** (cron, every 5 minutes) moves them to
  `/media/jellyfin/library/cameras/<Camera>/<YYYY-MM>/<YYYY-MM-DD HH-MM-SS>.mp4`
  and deletes clips older than `HL_BLINK_RETAIN_DAYS` (90; `0` keeps all).

HA never touches the media drive. Mounting it into HA would either stop HA
starting when the drive is missing, or write clips onto the thin pool; the
script instead refuses to write when the library is absent, leaves the clips
in staging, and `hl-blink-clips-failed` says so in `#alerts`.

**What Blink must be doing for there to be clips:** a Blink subscription
(cloud clips) **or** a Sync Module 2 with a USB stick (local storage). Both
work. With neither, Blink records nothing to fetch. Clips are saved only while
the system is armed, the same as the Blink app. Clips recorded while HA is down
for more than an hour are not fetched afterwards; they stay in the Blink app.

### Setting it up

First, so nothing pages `#alerts` while you work: create a Healthchecks check
(period 5m, grace 1h) and set `HEALTHCHECKS_BLINK_CLIPS_URL` in
`docker/monitoring/.env`. Then, on CT 100, soon after the pull (heartbeat
flags the new cron job as missing until `install-cron.sh` runs):

```bash
cd ~/homelab && git pull
# Recreate, not restart: configuration.yaml is a single-file mount.
docker compose -f docker/homeassistant/docker-compose.yml up -d --force-recreate
docker compose -f docker/monitoring/docker-compose.yml restart grafana   # new alert rule
./scripts/install-cron.sh            # adds blink-clips.sh
```

Then:

1. **Add Blink** in HA: **Settings → Devices & services → Add integration →
   Blink**, sign in, and enter the code Blink emails or texts you. Check that
   a `camera.*` entity appears for each camera.
2. **Check the automation**: **Settings → Automations** lists *Blink - save
   recent clips*. Walk past a camera with the system armed; within ~10
   minutes a file appears in `/media/jellyfin/library/cameras/` (or run
   `./scripts/blink-clips.sh` to move it straight away).
3. **Add the library in Jellyfin**: **Dashboard → Libraries → Add Media
   Library**, content type **Home videos and photos**, name *Cameras*, folder
   **`/media/cameras`** (Jellyfin sees the library drive at `/media`). Turn on
   **Real time monitoring** so new clips show up without a scan.

To keep clips longer or forever, set `HL_BLINK_RETAIN_DAYS` on the cron line
(`crontab -e`), e.g. `HL_BLINK_RETAIN_DAYS=0 /root/homelab/scripts/blink-clips.sh ...`.

## Arming Blink by presence

Two automations in `config/configuration.yaml` arm every Blink sync module
(**Away**) once everyone has been out of the Home zone for 10 minutes, and
disarm it as soon as anyone is back. Clips are only recorded while armed, so
this is also what feeds "Blink recordings" above.

They need HA to know where you are:

1. Install the **Home Assistant Companion app** on your phone, sign in, and
   allow location **Always**.
2. In **Settings → People**, open your person and add the phone's
   `device_tracker` under *Track device*. Do the same for anyone else who
   lives here; arming waits until *everyone* tracked is away.
3. Check **Settings → Areas & zones → Home** has the right address and a
   radius of ~100 m.

Nobody tracked means both automations do nothing, rather than arming forever.
To turn them off without a code change, disable them in **Settings →
Automations** (they are read-only there, but can be disabled). After a pull,
recreate HA as usual (`up -d --force-recreate homeassistant`).

## Updating

Not automatic. Read the [release notes](https://www.home-assistant.io/blog/categories/release-notes/)
for breaking changes first, then:

```bash
docker compose -f docker/homeassistant/docker-compose.yml pull
docker compose -f docker/homeassistant/docker-compose.yml up -d
```

Then open **HACS** and apply any updates for community integrations (Alexa
Media Player). An HA release often needs one. HACS integrations are not pinned
by anything in git, and nothing updates them on its own.

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
| Alexa: "device is unresponsive", or does not know a device | Alexa goes through Matterbridge: see its [troubleshooting](../matterbridge/README.md#troubleshooting). |
| Echos are `unavailable` | Alexa Media Player lost its Amazon session. Look for a notification asking for a CAPTCHA or a new login, then **Settings → Devices & services → Alexa Media Player → Reconfigure**. Right after an HA update, check HACS for an update first. |
| Alexa says "I don't know that" or answers from the web instead of reading a report | No routine has that phrase, or the phrase sounds like a question Alexa answers itself (Tesla's stock price). Use a command phrase like "car report" (see "Asking Alexa questions"). Tap ▶ on the routine in the Alexa app to test the HA side alone. |
| An announcement runs with no error and nothing plays | **Communications** is off for that Echo in the Alexa app, or it is on Do Not Disturb. |
| An edit to `configuration.yaml` did nothing | It is a single-file mount: `up -d --force-recreate homeassistant`, not `restart`. |
