# Monitoring stack

Observability for the homelab: metrics (Prometheus), logs (Loki), dashboards +
alerting (Grafana), and push notifications (ntfy).

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
| ntfy | Push notifications for alerts | `:8090` (or `alerts.home` via Caddy) |

## First-time setup

1. **Copy env and fill secrets**
   ```bash
   cp .env.example .env
   # set GRAFANA_ADMIN_PASSWORD, PVE_*, POSTGRES_EXPORTER_DSN, NTFY_BASE_URL
   ```

2. **Create the Proxmox read-only token** (Datacenter → Permissions):
   - User `monitoring@pve`, API token `grafana` (disable privilege separation),
     permission on path `/` with role **PVEAuditor**. Put the token value in `.env`.

3. **Create the Postgres monitoring role** (on the core stack DB):
   ```sql
   CREATE ROLE monitoring WITH LOGIN PASSWORD 'strong-password';
   GRANT pg_monitor TO monitoring;
   ```

4. **Set the Proxmox host IP** in `prometheus/prometheus.yml` — replace both
   `PROXMOX_HOST_IP` occurrences (jobs `node-proxmox` and `pve`).

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

8. **ntfy app**: install the ntfy app (iOS/Android), point it at `NTFY_BASE_URL`,
   subscribe to topic `homelab-alerts`. Keep Tailscale active on the phone so it
   can fetch alert bodies when away from home.

## Verify

- **Targets**: Prometheus → Status → Targets, every job `UP`
  (`curl -s localhost:9090/api/v1/targets`).
- **Dashboards**: open each Homelab dashboard, confirm panels populate.
- **Logs**: Grafana → Drilldown → Logs, filter `{job="docker"}`.
- **Alerts**: Grafana → Alerting → Contact points → test `ntfy`; a push should
  land on the phone. Then with the phone on cellular + Tailscale, repeat to
  validate the iOS upstream/APNs path.

## Notes

- ntfy can be fronted by Caddy as `alerts.home`; add a route in `docker/proxy/`
  and set `NTFY_BASE_URL=http://alerts.home`.
- Grafana alert notification body is the raw alert JSON (functional, not pretty).
  A small relay or message template can be added later for nicer formatting.
