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
| Local-LLM jobs & digests | Discord bot (`docker/assistant/`) |
| Database | PostgreSQL |
| Media server | Jellyfin *(planned)* |

## Service URLs

Port-free names on a real domain with real certificates, served by the `proxy`
stack (Caddy). `HOMELAB_DOMAIN` in [`docker/proxy/.env`](docker/proxy/.env.example)
sets the base — e.g. `home.example.com` gives `stats.home.example.com`.
Source of truth: [`docker/proxy/Caddyfile`](docker/proxy/Caddyfile).

| URL | Service | What it's for |
|-----|---------|---------------|
| https://`$HOMELAB_DOMAIN` | **Dashboard** | **Start here** — links to everything below, with up/down status |
| https://chat.`$HOMELAB_DOMAIN` | Open WebUI | AI chat |
| https://stats.`$HOMELAB_DOMAIN` | Grafana | Dashboards & metrics |
| https://apps.`$HOMELAB_DOMAIN` | Portainer | Docker management |
| https://dns.`$HOMELAB_DOMAIN` | AdGuard Home | DNS admin & ad blocking |
| https://alerts.`$HOMELAB_DOMAIN` | ntfy | Monitoring push notifications |
| https://mcp.`$HOMELAB_DOMAIN` | Grafana MCP | Read-only telemetry for Claude Code (bearer-gated) |
| https://kali.`$HOMELAB_DOMAIN` | Kali Linux (webtop) | On-demand security desktop (boots on visit, scales to zero) |

> **These are not public.** DNS resolves to `10.0.0.201`, a private address — the
> name is looked up publicly, but only a device on the LAN or tailnet can reach
> it. Nothing is port-forwarded and no tunnel is deployed.
>
> Certificates are real Let's Encrypt ones, obtained via the ACME **DNS-01**
> challenge: Caddy proves ownership by writing a TXT record to the Cloudflare
> zone, never by receiving a connection. That's what makes HTTPS possible on a
> host the internet cannot reach — and it retires the browser warnings the old
> `kali.home` internal CA produced.
>
> The legacy `*.home` names still work; each one now redirects to its real
> counterpart. To add a service: add a site block to the Caddyfile, add a tile to
> [`docker/dashboard/config/services.yaml`](docker/dashboard/config/services.yaml),
> then `docker compose restart caddy` — the wildcard DNS record already covers
> it. CI fails if you do the first and forget the second.
>
> The dashboard is the one exception to "the wildcard covers it": it is served at
> the bare `$HOMELAB_DOMAIN`, and a DNS wildcard substitutes for the `*` rather
> than matching the parent name, so it needs its own `A` record.

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
│   ├── ai/                  # Ollama + Open WebUI
│   │   └── docker-compose.yml
│   ├── dashboard/           # Landing page — links + status for everything
│   │   ├── docker-compose.yml
│   │   └── config/          # Tiles, checked against the Caddyfile by CI
│   └── assistant/           # Discord bot: local-LLM jobs + daily digest
│       ├── docker-compose.yml
│       └── app/
├── scripts/                 # Run from cron on the Docker LXC
│   ├── bootstrap-docker.sh  # Install Docker on a fresh Debian/Ubuntu host
│   ├── heartbeat.sh         # Dead man's switch -> Healthchecks (*/5 min)
│   ├── repo-sync.sh         # Daily git pull, restart stale stacks, report
│   └── check-dashboard.sh   # CI: every proxied site has a dashboard tile
├── .github/
│   └── workflows/ci.yml     # Compose, Caddyfile, shell and assistant checks
├── docs/
│   └── setup-log.md         # Chronological setup notes
└── .gitignore
```

`repo-sync.sh` pulls nightly, restarts any stack running older code than the
tree, and **verifies each one came back** — a restart that is not checked is not
self-healing, it is auto-breaking faster. It reports what it restarted, so
`#alerts` doubles as a deployment log and silence means nothing changed.

One stack is excluded by default and should stay that way: `proxy` contains
AdGuard, which is the household's DNS. A 4am restart that does not come back
takes the network down until someone notices, and every tool you would reach for
to diagnose it resolves names through the thing that is down. It is reported
instead, with the command to run. Adjust with `HL_NO_AUTOHEAL`, or set
`HL_AUTOHEAL=no` for report-only.

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
   cp docker/assistant/.env.example docker/assistant/.env
   ```
3. Bring up a stack:
   ```bash
   docker compose -f docker/core/docker-compose.yml up -d
   docker compose -f docker/monitoring/docker-compose.yml up -d
   docker compose -f docker/ai/docker-compose.yml up -d
   docker compose -f docker/assistant/docker-compose.yml up -d --build
   ```

## AI models

One model runs on Ollama: **`llama3.2:3b`** (built from
[`docker/ai/models/llama3.2.Modelfile`](docker/ai/models/llama3.2.Modelfile) via
[`docker/ai/load-models.sh`](docker/ai/load-models.sh); pins `num_thread 4` for
the LXC CPU quota — see [`AGENTS.md`](AGENTS.md)). Kept resident
(`OLLAMA_KEEP_ALIVE=-1`) so there's no cold-load lag.

**Use it for** fast, private, offline tasks: quick Q&A from training knowledge,
summarizing/rewriting pasted text, drafting boilerplate. No web search — see
[`docs/ai-strategy.md`](docs/ai-strategy.md) for what goes to local vs. Claude.

### Using it: Discord, not the chat page

The deciding question is **is anyone waiting on the answer?** A CPU-only 3B at
~16 tok/s is painful to watch and perfectly fine when the result arrives on its
own — so most local work goes through the
[`assistant`](docker/assistant/README.md) stack, a Discord bot that runs jobs and
pushes results back:

| | |
|---|---|
| daily digest | homelab health posted each morning — services up, CPU/RAM/disk, restarts, log errors |
| `/ask`, `/summarize` | fire it off and read the reply when it lands |
| *right-click → Summarize message* | summarize any Discord message in place |
| `/digest`, `/status` | run a digest now; check model + queue |

It adds **no inbound attack surface** (the bot dials out, so there's no port,
route or tunnel) and consequently works **off-tailnet**, which `chat.home`
can't. Open WebUI stays for when you actually want interactive chat.

The Discord server's own layout is codified too —
[`docker/assistant/guild.yml`](docker/assistant/guild.yml) declares the
categories, channels, topics and permissions, applied with an idempotent
`--provision` that never deletes anything. Setup steps:
[`docker/assistant/README.md`](docker/assistant/README.md). Design:
[`docs/design/tsd-local-llm-discord-jobs.md`](docs/design/tsd-local-llm-discord-jobs.md).

> **Tried and removed (2026-06-07):** a self-hosted SearXNG + `qwen2.5:7b` for
> web-augmented answers. On this CPU-only / 16 GB box the 7B was too slow and
> RAM-hungry, and a 3B can't faithfully use retrieved sources anyway. Anything
> needing current web data or real reasoning (trip/weather planning, research,
> debugging) goes to Claude. See `docs/ai-strategy.md`.

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
| assistant | *(none — outbound only)* |
| PostgreSQL | 5432 |
