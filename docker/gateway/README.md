# gateway — the one way in from the internet

Lock 3 of the secure gateway ([TSD](../../docs/design/tsd-secure-gateway.md),
[ADR 0002](../../decisions/0002-secure-gateway.md)). Setup, self-tests, the
per-endpoint checklist and the kill switch: [`docs/runbook-gateway.md`](../../docs/runbook-gateway.md).

| Path | What |
|---|---|
| `Caddyfile` | One site block per route. Every block starts with `import verify <route>`; exact paths and methods; everything else 404. |
| `docker-compose.yml` | `gateway` (Caddy, no ports, no internet) and `gateway-verifier`. Networks `gateway_edge` (cloudflared ↔ gateway, internal), `gateway_verifier` (internal), `gateway_egress` (verifier → Access signing keys). |
| `verifier/` | The `forward_auth` service: Access JWT signature, issuer, expiry, AUD, hostname and identity per route. Anything uncertain is 403. |
| `tools/gatewaywatch.py` | What `scripts/gateway-watch.sh` and `scripts/access-log-poll.sh` run. Standard library only. |
| `tools/check_gateway.py` | The CI guard on the Caddyfile and the tunnel's networks. |
| `.env.example` | Names only. Hostnames, AUD tags and allowlists live in `.env` on the box: this repo is public. |

Tests, all offline:

```sh
python docker/gateway/verifier/tests/smoke.py            # needs verifier/requirements.txt
python -I docker/gateway/tools/tests/smoke.py
CADDY=$(which caddy) python docker/gateway/tools/tests/check_smoke.py
python docker/gateway/tools/check_gateway.py --caddy "$(which caddy)"
```
