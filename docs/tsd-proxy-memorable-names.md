# TSD — Memorable service names (`proxy` stack)

**Status:** approved 2026-06-07
**Goal:** Reach every homelab service at a memorable, port-free name (`chat.home`, `stats.home`, …) that resolves on phone + laptop anywhere on the Tailscale tailnet.

## Naming scheme

Function-based names under a `.home` suffix (chosen for instant clarity over themed alternatives like `.hq` / `.olympus`):

| Name | Service | Container target |
|------|---------|------------------|
| `chat.home` | Open WebUI (AI chat) | `open-webui:8080` |
| `stats.home` | Grafana | `grafana:3000` |
| `apps.home` | Portainer | `portainer:9000` |
| `dns.home` | AdGuard Home (admin) | `adguard:80` |

Names are trivial to change later: edit the site label in `Caddyfile` + the matching AdGuard rewrite, then restart Caddy.

## Architecture

```
client (phone/laptop, anywhere)
  │  asks DNS for chat.home
  ▼
Tailscale split-DNS  ──(domain "home")──▶  AdGuard Home (in LXC)
  │  AdGuard rewrite: *.home -> 10.0.0.201
  ▼
Caddy :80 (in LXC)  ──(Host: chat.home)──▶  open-webui:8080
```

- **Caddy** (`caddy:2-alpine`) — reverse proxy on `:80`, routes by Host header. Attached to the `core`, `monitoring`, and `ai` Docker networks so it can reach each service by container name. `auto_https off` → plain HTTP (Tailscale already encrypts).
- **AdGuard Home** (`adguard/adguardhome:latest`) — DNS resolver answering `*.home → 10.0.0.201`, plus network-wide ad/tracker blocking. DNS on `:53`, admin UI behind Caddy at `dns.home`.

## Enabling changes

1. **Tailscale inside the LXC** — install + `tailscale up --hostname=lab`, so AdGuard is a reachable tailnet node usable as the split-DNS resolver. (Host subnet route stays as-is.)
2. **Tailscale admin → DNS → Split DNS** — add a custom nameserver for domain `home` pointing at the LXC's tailnet IP, so `*.home` resolves for every tailnet device, anywhere.

## Conventions / footprint

- New stack `docker/proxy/`: named volumes, `unless-stopped`, no secrets, no TLS.
- AdGuard admin password is the only credential (set during setup, store in password manager).
- ~150 MB RAM combined. No Proxmox storage/GPU considerations.

## Risks / notes

- `.home` is a made-up suffix — fine over private split-DNS; `.internal` is the collision-safe alternative if ever desired.
- Port `53` may collide with the LXC's `systemd-resolved` stub; disable the stub listener or bind AdGuard to the LXC IP if so.
- Caddy depends on the `core_core`, `monitoring_monitoring`, `ai_ai` external networks — those stacks must be up first.

## Implementation order

1. Add Tailscale to the LXC (`tailscale up --hostname=lab`).
2. Bring up `proxy` stack; complete AdGuard setup; add `*.home → 10.0.0.201` rewrite.
3. Configure Tailscale Split DNS (`home` → LXC tailnet IP).
4. Verify `chat.home` from the phone off-WiFi; log + PR.
