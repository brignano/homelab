# mcp-budget — Actual Budget for Claude

[`s-stefanov/actual-mcp`](https://github.com/s-stefanov/actual-mcp) (MIT, built on
`@actual-app/api`) in front of the Actual Budget server in [`../budget/`](../budget/README.md).
Claude Code, Otto and other assistants can read accounts, transactions, categories,
budget-vs-actual, net worth and cash flow, and, only when writes are switched on, add
and edit transactions, categories, payees and rules.

```
client (Claude Code / Otto)  Authorization: Bearer <MCP_BUDGET_TOKEN>
   │  https://budget-mcp.home.brignano.io/sse        LAN + tailnet only
   ▼
Caddy ──▶ mcp-budget:3000 ──@actual-app/api──▶ budget:5006 (Actual server)
```

## Why this server

Of the two Actual MCP servers found, it has the three things this lab needs: an
official npm/Docker release (v1.15.1, 2026-10-20), a **read-only default** (write
tools exist only with `--enable-write`), and **built-in bearer auth** for its HTTP mode
(`--enable-bearer`). A fork (`giorgiobrullo/actual-mcp`) adds delete and batch tools but
has no image, needs a sibling Actual source checkout to build, and has no HTTP auth.
It has no transaction-delete and no bank-sync tool, which suits a budget that has bank
sync off on purpose.

## Setup

1. On CT 100 run `./scripts/mcp-budget-env.sh`. It asks for each value (passwords hidden),
   generates the token, and writes `docker/mcp-budget/.env` (mode 600). By hand instead:
   `cp docker/mcp-budget/.env.example docker/mcp-budget/.env` and fill it:
   the Actual server password, the Sync ID (*Settings → Show advanced settings*), the
   budget's end-to-end encryption password, and a new `MCP_BUDGET_TOKEN`
   (`openssl rand -hex 32`). Save the token in the password manager. These never go in
   git or in chat. The Homepage tile (#313) reads the same Actual values.
2. `docker compose -f docker/mcp-budget/docker-compose.yml up -d`
3. Reload Caddy so `budget-mcp.home.brignano.io` is served (confirm-first, it is the
   proxy stack).
4. Point a client at it (the endpoint is `/sse`, checked on the first deploy):
   ```json
   { "mcpServers": { "budget": {
       "type": "sse",
       "url": "https://budget-mcp.home.brignano.io/sse",
       "headers": { "Authorization": "Bearer <MCP_BUDGET_TOKEN>" } } } }
   ```

## Turning writes on

Writes are off until you ask. To enable them:

```bash
docker compose -f docker/mcp-budget/docker-compose.yml -f docker/mcp-budget/docker-compose.write.yml up -d
```

Back to read-only: run the same command without the second `-f`. Take an export first
(*Settings → Export data*); the nightly backup also covers `budget_data`. Even with
writes on, there is no delete-transaction or bank-sync tool.

## Verify

```bash
docker compose -f docker/mcp-budget/docker-compose.yml ps                  # Up
curl -s -o /dev/null -w '%{http_code}\n' https://budget-mcp.home.brignano.io/   # 401 without a token
ss -tlnp | grep 3000                                                       # nothing: no published port
```

## Not covered

Claude in the cloud (the Life project) cannot reach a tailnet-only host. Giving it
access would need a Cloudflare Tunnel route with Access in front, as `mcp-calendar`
has, and an explicit decision, because this is financial data.
