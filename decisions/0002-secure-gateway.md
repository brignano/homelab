# 0002: One secure gateway for everything the homelab exposes

**Status:** accepted, 2026-11-09 · **TSD:** [`tsd-secure-gateway.md`](../docs/design/tsd-secure-gateway.md) · **Issue:** #360

## Decision

Anything on CT 100 that must be reachable from the internet goes through one
path, and only this path:

Cloudflare Access (one application per hostname) → Cloudflare Tunnel →
cloudflared (checks the Access token, can reach only the gateway) → a dedicated
gateway Caddy (checks the token again, the caller's identity per route, exact
paths and methods) → the upstream with its own least-privilege credential,
added by the gateway.

- No inbound ports. Hostnames are random first-level names, kept out of the
  public repo and out of certificate logs (zone wildcard).
- People sign in with Access; machines use one service token each.
- Refusals at the box, unusual sign-ins and tunnel changes alert in `#alerts`;
  the box keeps 90 days of Access logs. `scripts/gateway-off.sh` is the kill
  switch.
- A CI denylist keeps budget, admin UIs and media out for good.
- Every new endpoint follows the checklist in the TSD.

## Why

Each public use case (calendar MCP, Alexa) was designed from scratch and none
felt safe. One pattern with four independent checks, failing closed on our own
mistakes, makes the next endpoint a checklist instead of a new risk.

## Replaces

Per-service ad hoc exposure through the tunnel (`/expose-service`) and the
calendar's own Access listener removed in #348. "Prefer LAN/tailnet or Remote
Control" stays the first step of the checklist.
