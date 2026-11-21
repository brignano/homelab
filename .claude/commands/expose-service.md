Expose an endpoint to the internet, through the secure gateway and nothing else.

The pattern was decided once (docs/design/tsd-secure-gateway.md, decisions/0002).
This command walks its checklist; it does not redesign anything. The full
click-by-click is docs/runbook-gateway.md, "Adding an endpoint: the checklist".

1. **Ask whether public is needed at all.** LAN or tailnet, or the `life` Remote
   Control session for cloud Claude, wins whenever it works. If it does, stop
   here and say so.
2. **Check the denylist.** Never: Actual Budget or its MCP, Portainer, Grafana,
   Prometheus, Proxmox, AdGuard, Jellyfin (video over the tunnel risks the
   Cloudflare account), qBittorrent and the *arr apps, Matterbridge, Kali, Home
   Assistant's UI. CI (`docker/gateway/tools/check_gateway.py`) fails on these too.
3. **Ask me for:** the route name (lowercase, e.g. `alexa`), who calls it (me,
   or which machine), the exact paths and methods, and what the upstream is.
4. **Open an issue** in the fleet spec shape for the endpoint, then on a branch:
   - copy the commented example route at the bottom of `docker/gateway/Caddyfile`:
     `http://{$GW_HOST_<ROUTE>:<route>.invalid}:8080`, `import verify <route>`
     first, exact `path` and `method`, `request_body max_size`,
     `import strip_caller_credentials`, the upstream credential from
     `{$GW_UPSTREAM_TOKEN_<ROUTE>}`;
   - add the route's variables to `docker/gateway/docker-compose.yml`
     (`GW_HOST_*` to both services, `GW_AUD_*` and `GW_ALLOW_*` to the verifier
     only, the upstream credential to the gateway only) and, empty, to
     `docker/gateway/.env.example`;
   - join the upstream's network (or `extra_hosts` for host-networked HA);
   - run `python docker/gateway/tools/check_gateway.py --caddy <caddy>` and the
     gateway tests.
5. **List my dashboard steps in the PR's `## Deploy`:** random hostname, one
   service token per machine caller, the Access application (before the DNS
   route; 401 for Service Auth; short session if it changes state; AUD into
   `.env`), the tunnel route to `http://gateway:8080` with Protect with Access,
   the `.env` lines, and `GW_SOURCE_<ROUTE>` in `gateway-poll.env` for a machine.
   Never write a hostname, AUD or Client ID into the repo, an issue or a PR.
6. **After deploy:** self-tests 1–5 and 8 from the runbook against the new
   hostname, one bad request to see the alert in `#alerts`, then a
   `docs/setup-log.md` entry and an AGENTS.md "Stack overview" row.
