# Monitoring stack

Observability for the homelab: metrics (Prometheus), logs (Loki), and dashboards
+ alerting (Grafana). Alerts are delivered to Discord `#alerts` by webhook.

## Components

| Service | Role | Exposure |
|---|---|---|
| prometheus | Metrics store + scraper | LAN/tailnet `:9090` |
| grafana | Dashboards + unified alerting | `stats.home` / `:3000` |
| node-exporter | LXC/Docker-host OS metrics | internal |
| cadvisor | Per-container metrics | internal |
| pve-exporter | Proxmox VE API metrics | internal |
| postgres-exporter | PostgreSQL metrics (read-only role) | internal + `core_core` |
| blackbox-exporter | HTTP uptime probes | internal + `core`/`ai`/`proxy` |
| loki | Log store (30-day retention) | internal |
| alloy | Ships Docker + journal logs → Loki | internal (`127.0.0.1:12345` UI) |

## First-time setup

1. **Copy env and fill secrets**
   ```bash
   cp .env.example .env
   # set GRAFANA_ADMIN_PASSWORD, PVE_*, POSTGRES_EXPORTER_DSN, DISCORD_ALERT_WEBHOOK
   ```

2. **Create the Proxmox read-only token** (Datacenter → Permissions):
   - User `monitoring@pve`, API token `grafana` (disable privilege separation),
     permission on path `/` with role **PVEAuditor**. Put the token value in `.env`.

3. **Create the Postgres monitoring role** (on the core stack DB):
   ```sql
   CREATE ROLE monitoring WITH LOGIN PASSWORD 'strong-password';
   GRANT pg_monitor TO monitoring;
   ```

4. **Proxmox host IP** — `prometheus/prometheus.yml` is pre-set to `10.0.0.200`
   (host m5). Change it there if your host IP differs.

5. **Install node_exporter on the Proxmox host** (bare metal, Debian):
   ```bash
   apt install prometheus-node-exporter
   ```

6. **Fetch dashboards**
   ```bash
   ./scripts/fetch-dashboards.sh
   ```

7. **Bring it up**
   ```bash
   docker compose up -d
   ```

8. **Discord webhook**: Server Settings → Integrations → Webhooks → New Webhook
   → pick `#alerts` → Copy Webhook URL, and put it in `.env` as
   `DISCORD_ALERT_WEBHOOK`. This is required — with ntfy gone it is the only
   path an alert takes, so the stack will not start without it.

## Verify

- **Targets**: Prometheus → Status → Targets, every job `UP`
  (`curl -s localhost:9090/api/v1/targets`).
- **Dashboards**: open each Homelab dashboard, confirm panels populate.
- **Logs**: Grafana → Drilldown → Logs, filter `{job="docker"}`.
- **Alerts**: Grafana → Alerting → Contact points → test `homelab`; a message
  should land in Discord `#alerts`. Since this is now the only delivery path,
  re-run this test after any change to the webhook.
- **Contact points are what the file says**: the list should show `homelab`
  with exactly one integration (Discord). A leftover marked "Unused" means a
  provisioning deletion did not apply — see the note below.
  ```bash
  # Every provisioned receiver uid Grafana currently holds:
  curl -su admin:"$GRAFANA_ADMIN_PASSWORD" \
    localhost:3000/api/v1/provisioning/contact-points \
    | python3 -c 'import json,sys; [print(c["uid"], c["type"], c["name"]) for c in json.load(sys.stdin)]'
  ```

## When `#alerts` is noisy

Start by separating the three things that produce identical-looking Discord
traffic, because they have opposite fixes:

```bash
./scripts/probe-status.sh          # run on CT 100, from the repo root
```

It prints, in order: whether Prometheus is scraping the targets *this checkout*
declares, what each target answers when probed right now, and how many times
each probe has changed state in the last 6h.

| What it shows | What it means | Fix |
|---|---|---|
| `NOT SCRAPED` | Prometheus is running an older config than the repo | The script says which: a reload, or a recreate — see below |
| `DOWN` + 0 state changes | The probe is stuck — it has never passed, so it is asking the wrong question | Point it at a path the service answers 2xx on |
| `DOWN` + many state changes | The service really is bouncing | Fix the service |
| Everything `up`, messages continue | Grafana has not reloaded the provisioning | `docker compose restart grafana` |

A repeating Discord message is **not** evidence of a repeating failure. One
never-resolving alert produces a message every `repeat_interval` (4h) forever,
which reads as "firing constantly" and sends you looking for a flap that is not
there.

### `restart` and `reload` are not enough after a `git pull`

`prometheus.yml`, `blackbox.yml`, `loki-config.yml`, `config.alloy` and the
Caddyfile are bind-mounted **as single files**, and Docker resolves a file mount
to an inode when the container is created. git does not edit files in place — it
writes a new file and renames it over the old one — so `git pull` gives the path
a new inode and leaves the container mapped to the original, now unlinked.

The container then serves the old config indefinitely, and nothing says so:

```
$ git pull                                   # Already up to date.
$ curl -X POST localhost:9090/-/reload       # HTTP/1.1 200 OK
$ docker exec prometheus grep caddy /etc/prometheus/prometheus.yml
          - http://caddy:80/                 # ...the file from six days ago
```

`docker compose restart` does not help either — same container, same mounts.
**Recreate**, which is what re-resolves the mount:

```bash
docker compose -f docker/monitoring/docker-compose.yml up -d --force-recreate prometheus
```

`probe-status.sh` tells this apart from a plain missing reload by diffing the
container's copy against the one on disk, so you get the right command rather
than the plausible one.

`grafana/provisioning/` is mounted as a **directory**, which does not have this
problem — file replacements inside it are visible to the container. It only
needs `docker compose restart grafana`, because provisioning is read at startup.

## Notes

- **Capacity & Headroom** dashboard (`grafana/dashboards/homelab-capacity.json`) is a
  custom, committed dashboard — a focused at-a-glance view of total CPU/RAM/disk
  usage, free RAM headroom, and per-VM/LXC usage. Unlike the fetched community
  dashboards (generated by `fetch-dashboards.sh`), it lives in git.
- **Alerting is Discord-only.** ntfy previously ran here for phone push and was
  removed once Discord covered the same ground — see `docs/setup-log.md`. The
  box being *down* is still covered from off-box by `scripts/heartbeat.sh`,
  whose Healthchecks.io check alerts the same `#alerts` channel.
- **Changing alert provisioning needs a Grafana restart, and deletions need a
  directive.** Two separate traps, both silent:
  1. `grafana/provisioning/` is bind-mounted and read only at Grafana startup.
     Editing a file under it does not change the container's config hash, so
     `docker compose up -d` reports `Running` and changes nothing. Use
     `docker compose restart grafana`.
  2. File provisioning **upserts**. Removing a contact point, receiver or rule
     from a file does not delete it from Grafana's database — it lingers,
     marked "Unused", and the UI will not let you delete a provisioned resource
     either. Deletion needs an explicit `deleteContactPoints:` (or
     `deleteRules:`) block naming the uid. See `contactpoints.yml`.
