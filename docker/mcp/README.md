# mcp — Homelab MCP server (read-only observability)

A single [`grafana/mcp-grafana`](https://github.com/grafana/mcp-grafana) container that
gives Claude Code on the dev machines a consistent, **read-only** tool surface over the
lab's telemetry — Prometheus metrics, Loki logs, and alert-rule status — by querying the
Grafana datasources that are already wired up.

Design + decisions: [`docs/design/tsd-homelab-mcp-server.md`](../../docs/design/tsd-homelab-mcp-server.md).

```
dev machine (Claude Code)
   │  http://mcp.home/mcp   Authorization: Bearer <MCP_BEARER_TOKEN>
   ▼
Caddy (mcp.home)  ──bearer check──▶  mcp-grafana:8000  ──Viewer SA token──▶  Grafana ──▶ Prometheus + Loki
```

Three security layers: **Tailscale** (network boundary) → **Caddy bearer token** (app layer)
→ **Viewer-scoped Grafana service account** (hard read-only ceiling).

## Setup

### 1. Create the Grafana service account (read-only)
Grafana (`http://stats.home`) → **Administration → Users and access → Service accounts →
Add service account** → role **Viewer** → **Add service account token** → copy the value.

> The Viewer role is the real security guarantee — it cannot write regardless of which
> tools the MCP server exposes.

### 2. Configure secrets
```sh
# On CT 100, in this stack:
cp docker/mcp/.env.example docker/mcp/.env
# paste the Viewer token into GRAFANA_SERVICE_ACCOUNT_TOKEN

# In the proxy stack — the bearer token the dev machines will send:
cp docker/proxy/.env.example docker/proxy/.env
# set MCP_BEARER_TOKEN (openssl rand -hex 32)
```

### 3. Add the DNS rewrite
AdGuard (`http://dns.home`) → **Filters → DNS rewrites → Add** → domain `mcp.home`,
answer `10.0.0.201` (same as the other `*.home` names).

### 4. Bring it up
```sh
# monitoring stack must be running first (provides grafana + the network)
docker compose -f docker/mcp/docker-compose.yml up -d
docker compose -f docker/proxy/docker-compose.yml up -d   # reloads Caddy with mcp.home + the token
```

### 5. Point Claude Code at it (each dev machine)
Add to the Claude Code MCP config (`.mcp.json`):
```json
{
  "mcpServers": {
    "homelab": {
      "type": "http",
      "url": "http://mcp.home/mcp",
      "headers": { "Authorization": "Bearer <MCP_BEARER_TOKEN>" }
    }
  }
}
```
Use the same `MCP_BEARER_TOKEN` from `docker/proxy/.env`. Requires Tailscale up (that's
how `*.home` resolves and how the tailnet boundary is enforced).

## Verify
```sh
# Wrong/no token -> 401 from Caddy:
curl -s -o /dev/null -w '%{http_code}\n' http://mcp.home/mcp           # 401
# Correct token -> reaches mcp-grafana (not 401):
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $MCP_BEARER_TOKEN" http://mcp.home/mcp
```

## Notes
- **Read-only by construction:** `--disable-write` plus a Viewer token. `--disable-admin`,
  `--disable-oncall`, `--disable-dashboard` trim the surface to datasource queries + alerts.
- **No Docker socket, no Portainer.** Container status/logs come from cAdvisor metrics and
  the existing `alloy → loki` pipeline via Grafana.
- RAM footprint is negligible (~30 MB; stateless Go proxy). Heavy queries load Prometheus,
  not this container.
