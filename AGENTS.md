# Homelab — Claude Code Context

## Hardware
- **Device:** GMKtec M5 Ultra
- **CPU:** AMD Ryzen 7 7730U (8c/16t) — CPU-only, no discrete GPU
- **RAM:** 16 GB DDR4
- **Storage:** 512 GB NVMe

## LXC Configuration

Planned Proxmox LXC container for Docker workloads:

| Parameter | Value |
|-----------|-------|
| RAM | 14 GB (limit) |
| vCPUs | 6 cores |
| Disk | 400 GB (thin-provisioned) |
| Disk bus | VirtIO |
| Network | VirtIO |
| OS | Debian (latest stable) |
| Privilege | Privileged container |
| Features | `nesting=1` (required for Docker-in-LXC) |

**Rationale:** LXC RAM is a limit, not a hard carve-out, and the disk is thin-provisioned, leaving host headroom on a 16 GB / 512 GB box.

## Stack overview

| Service | Stack file | Network exposure |
|---------|-----------|-----------------|
| Proxmox VE | bare-metal hypervisor | Tailscale only (host mgmt) |
| Portainer | `docker/core/` | LAN + tailnet (`apps.home`) |
| PostgreSQL 16 | `docker/core/` | internal only (`127.0.0.1` + Docker network) |
| Prometheus | `docker/monitoring/` | LAN + tailnet |
| Grafana | `docker/monitoring/` | LAN + tailnet (`stats.home`) |
| node-exporter | `docker/monitoring/` | internal |
| cAdvisor | `docker/monitoring/` | internal |
| pve-exporter | `docker/monitoring/` | internal (Proxmox API) |
| postgres-exporter | `docker/monitoring/` | internal (+ `core_core`) |
| blackbox-exporter | `docker/monitoring/` | internal (+ `core`/`ai`/`proxy`) |
| Loki | `docker/monitoring/` | internal (log store) |
| Alloy | `docker/monitoring/` | internal (log shipper) |
| ntfy | `docker/monitoring/` | LAN + tailnet (alerts, `:8090`) |
| Ollama | `docker/ai/` | LAN + tailnet |
| Open WebUI | `docker/ai/` | LAN + tailnet (via Caddy, `chat.home`) |
| Caddy | `docker/proxy/` | LAN + tailnet (`:80`, routes `*.home`) |
| AdGuard Home | `docker/proxy/` | LAN + tailnet (`:53` DNS, `dns.home`) |
| cloudflared | `docker/tunnel/` | public via Cloudflare Zero Trust (not yet deployed) |
| Sablier | `docker/desktops/` | internal (on-demand engine for desktops) |
| Kali webtop | `docker/desktops/` | LAN + tailnet (via Caddy, `kali.home`, on-demand) |
| Jellyfin | planned | Cloudflare Tunnel |

## Networking rules
- **Remote access:** the Proxmox host runs Tailscale as a subnet router advertising `10.0.0.0/24`, so tailnet devices reach the LXC and its services at `10.0.0.201`. (Tailscale runs on the host, not in the LXC — `/dev/net/tun` isn't exposed to the container.)
- **Memorable names (`*.home`):** AdGuard serves DNS (`*.home → 10.0.0.201`) and Tailscale split-DNS points the `home` domain at it; Caddy (`:80`) routes by Host header to each service — `chat.home` (Open WebUI), `stats.home` (Grafana), `apps.home` (Portainer), `dns.home` (AdGuard), `alerts.home` (ntfy), `kali.home` (Kali webtop, on-demand).
- **Port bindings:** admin UIs and the AI/metrics services (Portainer, Grafana, Prometheus, Ollama) bind to all interfaces — reachable over LAN + tailnet, **not** public. PostgreSQL stays on `127.0.0.1` (apps reach it over the internal Docker network).
- **Public access:** only via Cloudflare Tunnel (`cloudflared`), reserved for app previews and Jellyfin (planned) — not yet deployed. `cloudflared` joins `core_core` and `ai_ai` so it can proxy to other stacks without opening host ports.

## Ollama / AI tuning (LXC constraint)

- Ollama auto-detects the **host's** logical CPU count (16), not the LXC's **6-core cgroup quota**. Left at its default it oversubscribes the quota, the kernel CFS-throttles the inference threads, and generation collapses to ~0.5 tok/s.
- **Every model must pin `num_thread` ≤ the LXC core count** via a Modelfile in `docker/ai/models/`. Use `num_thread 4` (matches 6's ~16 tok/s while leaving 2 cores for other stacks).
- Apply all tuned models at once with `docker/ai/load-models.sh` (runs `ollama create` for every `models/*.Modelfile`, rebuilding each tag in place — no Open WebUI change needed). For a single model: `ollama create <tag> -f docker/ai/models/<name>.Modelfile`.
- There is no global Ollama thread env var, so this is per-model: adding a model means dropping a Modelfile in `docker/ai/models/` and re-running the loader.

## Repo conventions
- Each Docker stack lives in its own `docker/<name>/` directory with its own `docker-compose.yml` and `.env.example`.
- Never commit `.env` files — only `.env.example` with placeholder values.
- Secrets that must exist use `${VAR:?required}` syntax so Compose fails loudly if unset.
- Document every significant change in `docs/setup-log.md` using the template at the top of that file.
- New services default to `127.0.0.1:<port>` bindings. Bind to all interfaces only when the service must be reached over LAN/tailnet, and prefer fronting it with Caddy for a `*.home` name rather than exposing a raw port.

## Custom commands
These slash commands are available in `.claude/commands/`:

| Command | Purpose |
|---------|---------|
| `/new-service` | Scaffold a new Docker Compose stack |
| `/log-entry` | Write a dated entry to docs/setup-log.md |
| `/debug-container` | Diagnose a failing or unhealthy container |
| `/expose-service` | Add a service to the Cloudflare Tunnel config |

## How to help me
- When adding a new service, follow the existing stack pattern: separate directory, `.env.example`, `127.0.0.1` port bindings by default (open to all interfaces + a Caddy `*.home` route only if it needs LAN/tailnet access), named volume, restart policy.
- When I describe a problem with a container, check `docker logs`, `docker inspect`, and the compose file before suggesting fixes.
- When writing setup log entries, use the template in `docs/setup-log.md` and today's date.
- Prefer `docker compose` (v2) over `docker-compose` (v1).
- Don't suggest exposing admin services (Portainer, Grafana, PostgreSQL) via Cloudflare Tunnel.

## Token usage rules

- Prefer reading only the smallest relevant files before proposing changes.
- Do not scan the whole repo unless explicitly asked.
- Summarize findings before making large edits.
- For broad repo questions, first inspect README.md, AGENTS.md, and docs/setup-log.md.
- Use local Ollama/Open WebUI for low-risk, high-token tasks:
  - repo summaries
  - log summaries
  - documentation drafts
  - boilerplate
  - first-pass scripts
  - test scaffolding
- Use Claude for:
  - architecture decisions
  - multi-file edits
  - hard debugging
  - security-sensitive changes
  - final review
- Before starting a large task, produce a short plan and list the files likely needed.
- Avoid repeated full-file reads when a targeted grep/search is sufficient.