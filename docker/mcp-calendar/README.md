# mcp-calendar — read-only calendars for Claude Code

A small MCP server that lets Claude Code on your own machines read the Apple/iCloud
calendars Home Assistant already sees through its CalDAV integration. Nothing about
the calendars is made public: there is no iCloud share link, and the server is only
reachable over the LAN or tailnet.

```
dev machine (Claude Code, on Tailscale)
   │  https://mcp.$HOMELAB_DOMAIN/calendar/mcp   Authorization: Bearer <MCP_BEARER_TOKEN>
   ▼
Caddy (mcp.)  ──bearer check──▶  mcp-calendar:8000  ──GET /api/calendars/<entity>──▶  Home Assistant ──CalDAV──▶ iCloud
```

## Tools

| Tool | What it returns |
|------|-----------------|
| `list_calendars` | The `calendar.*` IDs in `HA_CALENDARS`, and the timezone |
| `calendar_events` | Events from `start` (YYYY-MM-DD, default today, within a year) for `days` (1–31, default 7), from one calendar or all of them. Title, start, end and location only |

## What it can never do

- **Change a calendar.** The only Home Assistant call is `GET /api/calendars/<entity>`.
  `tests/smoke.py` fails if any other HTTP method appears in `app/calendars.py`.
- **Read anything else in Home Assistant.** Only IDs in `HA_CALENDARS` reach that URL,
  and they must match `calendar.<name>`.
- **Return descriptions or attendees.** Events are cut to title, start, end and location.
- **Be reached from outside CT 100 without the token.** Caddy answers 401 before
  routing, and the server rejects any `Host` other than `mcp-calendar:8000` with a 421.
  Containers on `monitoring_monitoring` can still call it directly without a token,
  the same posture mcp-grafana has.

The HA token is still a full HA login, so give it its own non-admin HA user, not yours.

## Setup

1. **HA token.** In Home Assistant, add a user (Settings → People), log in as it,
   and create a long-lived access token (Profile → Security).
2. **Secrets.** On CT 100:
   ```sh
   cp docker/mcp-calendar/.env.example docker/mcp-calendar/.env
   # paste the token into HA_TOKEN; HA_CALENDARS defaults to the three iCloud calendars
   ```
   `MCP_BEARER_TOKEN` in `docker/proxy/.env` is shared with the Grafana MCP server;
   nothing new is needed there.
3. **Bring it up.** `/deploy` does this. By hand:
   ```sh
   docker compose -f docker/mcp-calendar/docker-compose.yml up -d --build
   docker compose -f docker/proxy/docker-compose.yml up -d --force-recreate caddy   # Caddyfile is a single-file mount
   ```
4. **Point Claude Code at it** (each dev machine, next to the Grafana entry):
   ```sh
   claude mcp add --transport http --scope user calendar \
     https://mcp.<your-domain>/calendar/mcp \
     --header "Authorization: Bearer <MCP_BEARER_TOKEN>"
   ```
   Tailscale must be up: the name resolves publicly but the address is private.

## Verify

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://mcp.$HOMELAB_DOMAIN/calendar/mcp   # 401
docker exec mcp-calendar python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read())"
```
Then ask Claude Code "what's on my calendar this week?". A tool reply of
`HA_TOKEN is not set` or `token rejected (401)` points at step 1 or 2.

## Where it works

Only where the machine running Claude can reach your tailnet: Claude Code on your
laptop or desktop, or a Claude session started on that device. Claude in the cloud
(claude.ai chats, cloud Claude Code sessions, the Life project) cannot reach it,
because `mcp.$HOMELAB_DOMAIN` has no public route. That is the price of not
publishing the calendar anywhere.
