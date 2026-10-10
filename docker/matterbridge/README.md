# matterbridge — Home Assistant to Alexa, over Matter

[Matterbridge](https://github.com/Luligu/matterbridge) with its
[Home Assistant plugin](https://github.com/Luligu/matterbridge-hass). It reads
the HA entities you label **Alexa** and offers them as Matter devices. An
Echo pairs with it once over the LAN, and "Alexa, turn off the kitchen lights"
goes Echo → Matterbridge → Home Assistant without leaving the house.

It replaces Home Assistant Cloud (Nabu Casa) for this job. It's free, and
Alexa's commands stay on the LAN instead of going through a cloud relay.

| | |
|---|---|
| Image | built here: `luligu/matterbridge` 3.10.13 + `matterbridge-hass` 1.5.0, both pinned (`Dockerfile`) |
| Networking | `network_mode: host` (Matter needs LAN multicast and IPv6) |
| Talks to | Home Assistant at `ws://127.0.0.1:8123`, as the non-admin `matterbridge` user |
| Exposes | Matter on UDP 5540 and mDNS on 5353 to the LAN. The web UI on docker0 only (`172.17.0.1:8283`) |
| What Alexa sees | entities with the HA label **Alexa**, minus locks, covers (garage), valves and the alarm, always |
| State | the `data` volume: Alexa's pairing, device identities, the web UI password |
| Monitored | blackbox probe `service="matterbridge"` → `hl-probe-down` |
| Updates | Renovate PRs (base image and plugin together), then a rebuild |

## Before you start

1. **An Echo that can control Matter devices.** Most Echos from about 2021
   on can, such as the Echo 4th gen and Echo Dot 5th gen. In the Alexa app,
   look under the device's settings for Matter.
2. **IPv6 on CT 100.** Matter runs on IPv6, and the container refuses to start
   without it. On CT 100:

   ```bash
   cat /proc/sys/net/ipv6/conf/all/disable_ipv6   # must print 0
   ip -6 addr show dev eth0 | grep fe80           # a link-local address
   ```

   A link-local address is enough: your router does not need to hand out
   IPv6. If the first line prints `1`, turn IPv6 back on and keep it on:

   ```bash
   echo 'net.ipv6.conf.all.disable_ipv6 = 0' > /etc/sysctl.d/90-ipv6.conf
   sysctl --system
   ```

3. **No Home Assistant Cloud Alexa skill.** If you turned it on earlier,
   turn it off in **Settings → Home Assistant Cloud** and disable the Home
   Assistant skill in the Alexa app. Otherwise every device shows up twice.

## Home Assistant user

Like the digest, Matterbridge gets an HA user of its own, as narrow as HA
allows. HA has no read-only or per-entity token: this token can operate
anything in the house. What keeps the locks out of Alexa is the label filter and
the domain blacklist in `start.sh`, not the token.

1. **Settings → People → Users → Add user.** Name it `matterbridge`, leave
   **Administrator** off, and turn on **Can only log in from the local
   network**.
2. Log in as `matterbridge` once (a private window is easiest). Open **Profile →
   Security → Long-lived access tokens → Create token**, name it `matterbridge`,
   and copy it.
3. Make the label: **Settings → Areas, labels & zones → Labels → Create
   label** `Alexa`.
4. Add the label to each entity Alexa should have: **Settings → Entities**,
   select them, **Add label → Alexa**. Label entities rather than whole
   devices: a labelled device brings every entity on it.

What to label: lights, switches, fans, the thermostat, sensors, and scenes
and scripts (Alexa sees each as a switch that turns itself back off). Locks,
covers (the garage door), valves and the alarm are dropped even if labelled. If
you want the garage closed by voice, label a **script** that only closes it.

## Deploy

```bash
cp docker/matterbridge/.env.example docker/matterbridge/.env
# set HA_TOKEN
docker compose -f docker/matterbridge/docker-compose.yml up -d --build
docker logs -f matterbridge    # wait for "Manual pairing code"
```

Creating `.env` is also what makes `heartbeat.sh` count `matterbridge` as
must-be-running (`SETUP_GATED_STACKS`). Before that, a stopped container does
not page.

## Pair with Alexa

1. Get the code:

   ```bash
   docker logs matterbridge 2>&1 | grep -i "manual pairing code"
   ```

2. In the Alexa app: **Devices → + → Add Device → Matter** (or "Other",
   then Matter). Say yes to the Matter logo question, then choose to **enter
   the code** instead of scanning, and type the 11-digit code.
3. Alexa finds one bridge and every labelled entity behind it. Rename them
   in the Alexa app if HA's names are awkward to say.

**Pair soon after the first start.** Until something pairs, anyone on the LAN
who has the code could pair instead. The code is only in this container's
logs. Once Alexa has paired, the bridge stops accepting new pairings.

**Adding an entity later:** label it in HA. If it has not appeared in Alexa
within a minute, `docker restart matterbridge`.

## The web UI

The Matterbridge web UI is bound to **docker0** (`172.17.0.1:8283`), which no
LAN or tailnet device can reach. Containers can, which is how the blackbox
probe gets to `/health`. Open it from your machine through SSH:

```bash
ssh -L 8283:172.17.0.1:8283 root@10.0.0.201
# then http://localhost:8283
```

It is not on Caddy and not on every interface, and that is on purpose:

- **Its login is per client IP address.** Behind Caddy, every user arrives as
  Caddy's IP, so one person logging in would unlock it for everyone on the
  LAN and tailnet.
- **It can install npm packages as root** in a container on host networking.
  That is code execution on CT 100's network for anyone who gets in.

Set a password anyway (**Settings** in the UI): every container on the box can
reach docker0.

You rarely need the UI. The pairing code is in the logs, and the plugin's
settings come from `start.sh`. **Changes to the plugin's settings made in the
UI are overwritten on the next start**: what Alexa can reach is a security
decision, so it lives in a file that gets reviewed.

## Updating

Renovate opens one PR for the base image and the plugin together (they have to
match: each plugin release names the Matterbridge it needs). After merging,
rebuild:

```bash
docker compose -f docker/matterbridge/docker-compose.yml up -d --build --force-recreate
```

Alexa's pairing survives a rebuild. It lives in the `data` volume.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Exits at once: "this host has no IPv6" | IPv6 is disabled on CT 100. See "Before you start" step 2. |
| Exits at once: "no docker0 IPv4 address" | Docker's default bridge is off or renamed. Set `MATTERBRIDGE_BIND` in `.env` to an address only the host and containers can reach. Never `0.0.0.0`. |
| Alexa: "device is unresponsive" for everything | Matterbridge or HA is down. `docker ps`, then `docker logs matterbridge`. After an HA restart the plugin reconnects within a minute. |
| Logs show `401` or "unauthorized" from HA | The `matterbridge` user's token was deleted or the user removed. Make a new token, update `HA_TOKEN`, then `up -d --force-recreate`. |
| An entity is not in Alexa | It lacks the `Alexa` label, its domain is blacklisted (lock, cover, valve, alarm, button, remote, media player), or it is hidden in HA. Fix it, then `docker restart matterbridge`. |
| Alexa cannot find the bridge while pairing | The phone and the Echo must be on the same LAN as CT 100, not the tailnet. Check `docker logs matterbridge` for mDNS errors. |
| Every device shows up twice | The Home Assistant Cloud Alexa skill is still linked. See "Before you start" step 3. |
| Need to pair again from scratch | Remove the bridge in the Alexa app. Then `docker compose -f docker/matterbridge/docker-compose.yml down`, `docker volume rm matterbridge_data` and deploy again. A new pairing code appears in the logs. |
