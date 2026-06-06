# Homelab — Claude Code Context

## Hardware
- **Device:** GMKtec M5 Ultra
- **CPU:** AMD Ryzen 7 7730U (8c/16t) — CPU-only, no discrete GPU
- **RAM:** 16 GB DDR4
- **Storage:** 512 GB NVMe

## Stack overview

| Service | Stack file | Network exposure |
|---------|-----------|-----------------|
| Proxmox VE | bare-metal hypervisor | Tailscale only |
| Portainer | `docker/core/` | Tailscale only |
| PostgreSQL 16 | `docker/core/` | Tailscale only |
| Prometheus | `docker/monitoring/` | Tailscale only |
| Grafana | `docker/monitoring/` | Tailscale only |
| node-exporter | `docker/monitoring/` | internal |
| cAdvisor | `docker/monitoring/` | internal |
| Ollama | `docker/ai/` | Tailscale only |
| Open WebUI | `docker/ai/` | Tailscale only |
| cloudflared | `docker/tunnel/` | public via Cloudflare Zero Trust |
| Jellyfin | planned | Cloudflare Tunnel |

## Networking rules
- All ports bind to `127.0.0.1` — nothing is exposed to the LAN or internet directly.
- **Tailscale-only services:** admin UIs (Portainer, Grafana), databases, AI stack.
- **Cloudflare Tunnel (public):** Jellyfin, any app previews being shared with friends.
- The `cloudflared` container joins `core_core` and `ai_ai` networks so it can proxy to containers in other stacks without opening host ports.

## Repo conventions
- Each Docker stack lives in its own `docker/<name>/` directory with its own `docker-compose.yml` and `.env.example`.
- Never commit `.env` files — only `.env.example` with placeholder values.
- Secrets that must exist use `${VAR:?required}` syntax so Compose fails loudly if unset.
- Document every significant change in `docs/setup-log.md` using the template at the top of that file.
- All ports default to `127.0.0.1:<port>` bindings.

## Custom commands
These slash commands are available in `.claude/commands/`:

| Command | Purpose |
|---------|---------|
| `/new-service` | Scaffold a new Docker Compose stack |
| `/log-entry` | Write a dated entry to docs/setup-log.md |
| `/debug-container` | Diagnose a failing or unhealthy container |
| `/expose-service` | Add a service to the Cloudflare Tunnel config |

## How to help me
- When adding a new service, follow the existing stack pattern: separate directory, `.env.example`, `127.0.0.1` port bindings, named volume, restart policy.
- When I describe a problem with a container, check `docker logs`, `docker inspect`, and the compose file before suggesting fixes.
- When writing setup log entries, use the template in `docs/setup-log.md` and today's date.
- Prefer `docker compose` (v2) over `docker-compose` (v1).
- Don't suggest exposing admin services (Portainer, Grafana, PostgreSQL) via Cloudflare Tunnel.
