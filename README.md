# Homelab

Personal homelab running on a GMKtec M5 Ultra mini PC.

## Hardware

| Component | Spec |
|-----------|------|
| Device | GMKtec M5 Ultra |
| CPU | AMD Ryzen 7 7730U (8c/16t, up to 4.5 GHz) |
| RAM | 16 GB DDR4 |
| Storage | 512 GB NVMe SSD |

## Stack

| Layer | Technology |
|-------|-----------|
| Hypervisor | Proxmox VE |
| Container runtime | Docker |
| Container management | Portainer |
| VPN / mesh network | Tailscale |
| Metrics collection | Prometheus |
| Dashboards | Grafana |
| Local AI inference | Ollama |
| AI chat UI | Open WebUI |
| Database | PostgreSQL |
| Media server | Jellyfin *(planned)* |

## Service URLs

Memorable, port-free names served by the `proxy` stack (Caddy + AdGuard split-DNS).
They resolve on any device on the Tailscale tailnet — phone or laptop, anywhere.
Source of truth: [`docker/proxy/Caddyfile`](docker/proxy/Caddyfile).

| URL | Service | What it's for |
|-----|---------|---------------|
| http://chat.home | Open WebUI | AI chat |
| http://stats.home | Grafana | Dashboards & metrics |
| http://apps.home | Portainer | Docker management |
| http://dns.home | AdGuard Home | DNS admin & ad blocking |
| http://alerts.home | ntfy | Monitoring push notifications |
| http://kali.home | Kali Linux (webtop) | On-demand security desktop (boots on visit, scales to zero) |

> These names only resolve over the tailnet via AdGuard (`*.home → 10.0.0.201`).
> To add or rename one: edit the site label in the Caddyfile, then `docker compose restart caddy`.

## Repository layout

```
homelab/
├── docker/
│   ├── monitoring/          # Grafana + Prometheus
│   │   ├── docker-compose.yml
│   │   └── prometheus/
│   │       └── prometheus.yml
│   ├── core/                # Portainer + PostgreSQL
│   │   └── docker-compose.yml
│   └── ai/                  # Ollama + Open WebUI
│       └── docker-compose.yml
├── scripts/
│   └── bootstrap-docker.sh  # Install Docker on a fresh Debian/Ubuntu host
├── docs/
│   └── setup-log.md         # Chronological setup notes
└── .gitignore
```

## Quick start

1. Bootstrap Docker on the host (run once on a fresh install):
   ```bash
   bash scripts/bootstrap-docker.sh
   ```
2. Copy and fill in environment variables:
   ```bash
   cp docker/core/.env.example docker/core/.env
   cp docker/monitoring/.env.example docker/monitoring/.env
   cp docker/ai/.env.example docker/ai/.env
   ```
3. Bring up a stack:
   ```bash
   docker compose -f docker/core/docker-compose.yml up -d
   docker compose -f docker/monitoring/docker-compose.yml up -d
   docker compose -f docker/ai/docker-compose.yml up -d
   ```

## Tailscale Hostname

The Proxmox host `m5` is on the tailnet as **`m5.tail58e272.ts.net`** (`100.116.69.120`). It runs as a **subnet router** advertising the LAN `10.0.0.0/24` (route approved in the admin console), so the Docker LXC and every service at `10.0.0.201` is reachable from any tailnet device — e.g. Grafana at `http://10.0.0.201:3000`. Set `--accept-routes` on client devices to use it.

## Networking

All services are exposed on the Tailscale interface only (no public ports). Tailscale MagicDNS is used for service discovery within the mesh.

| Service | Default port |
|---------|-------------|
| Portainer | 9000 |
| Grafana | 3000 |
| Prometheus | 9090 |
| Open WebUI | 3010 |
| Ollama API | 11434 |
| PostgreSQL | 5432 |
