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

## Ollama / AI tuning (LXC constraint)

- Ollama auto-detects the **host's** logical CPU count (16), not the LXC's **6-core cgroup quota**. Left at its default it oversubscribes the quota, the kernel CFS-throttles the inference threads, and generation collapses to ~0.5 tok/s.
- **Every model must pin `num_thread` ≤ the LXC core count** via a Modelfile in `docker/ai/models/`. Use `num_thread 4` (matches 6's ~16 tok/s while leaving 2 cores for other stacks).
- Apply with `ollama create <tag> -f docker/ai/models/<name>.Modelfile` (rebuilds the same tag in place — no Open WebUI change needed).
- There is no global Ollama thread env var, so this is per-model and must be repeated for each new model.

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