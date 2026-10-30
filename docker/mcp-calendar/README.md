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
- **Be reached from outside CT 100 without the token**, or through the tunnel
  without a verified Cloudflare Access login (see "Reaching it from Claude in the
  cloud" below). Caddy answers 401 before routing, and the server rejects any `Host` other than `mcp-calendar:8000` with a 421.
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

Over the tailnet, as above: Claude Code on your laptop or desktop. Claude in the
cloud (claude.ai chats, the mobile app, cloud Claude Code sessions, the Life
project) can't reach `mcp.$HOMELAB_DOMAIN`, because it has no public route.

The optional public listener below fixes that without publishing the calendar.

## Reaching it from Claude in the cloud (optional)

```
Claude (Anthropic cloud) ──OAuth token──▶ Cloudflare Access ──▶ Tunnel ──▶ cloudflared
                                           (your Google login,              │ mcp_calendar_public
                                            only your email)                ▼  (just these two)
                                                         mcp-calendar:8001 ── verifies the Access JWT ──▶ same two tools
```

Claude's connectors call from Anthropic's servers, so the server needs a public
address. That address is guarded twice:

1. **Cloudflare Access** with Managed OAuth. Claude gets a token only after you
   sign in through Access with an email the policy names. Tokens are short-lived
   and Access re-checks the policy on every refresh.
2. **This server** (`app/access.py`). Port 8001 serves nothing unless the request
   carries a `Cf-Access-Jwt-Assertion` that verifies against your team's signing
   keys, for this application's AUD, for an email in `ACCESS_EMAILS`. Anything
   else gets a 403. Leave the `ACCESS_*` settings blank and port 8001 never opens.

Nothing else changes: the same two read-only tools, the same four fields per event.
cloudflared shares a network with this container only (`mcp_calendar_public`), so
the tunnel can't be pointed at anything else on `monitoring` by accident. Pointing
it at port 8000 instead of 8001 gets a 421, not a way around the check.

**Compared with an iCloud public link:** that link is a password in a URL, with no
login, no expiry and no log, and it shares notes and attendees too. Here a leaked
URL is worthless without your Google sign-in. The new risk is anyone who controls
your claude.ai account, which can now read your calendar. Remove the connector or
the Access policy to shut it off.

### Setup (about 10 minutes, Cloudflare dashboard)

1. **Tunnel.** Zero Trust → Networks → Tunnels → Create a tunnel (Cloudflared).
   Copy its token into `CLOUDFLARE_TUNNEL_TOKEN` in `docker/tunnel/.env` on CT 100.
   This is the first tunnel on the box (see [`../tunnel/README.md`](../tunnel/README.md)).
2. **Public hostname**, on that tunnel: `cal-mcp.brignano.io` (any name outside
   the `*.home` wildcard, so tailnet DNS never sends it to Caddy), service
   `HTTP` → `mcp-calendar:8001`. No other hostnames.
3. **Access application.** Zero Trust → Access controls → Applications → Add →
   Self-hosted, domain `cal-mcp.brignano.io`.
   - Policy: **Allow**, Include → Emails → your Google address. Nothing else.
   - Login method: Google only, with **Instant authentication** on. Make sure the
     Google account has 2-step verification.
   - Advanced settings → turn on **Managed OAuth**. Add
     `https://claude.ai/api/mcp/auth_callback` to **Allowed redirect URIs**. Set
     the access token lifetime to 15 minutes and the grant session to 2 weeks.
   - Copy the **Application Audience (AUD) tag**.
4. **Server settings.** In `docker/mcp-calendar/.env`, fill `ACCESS_TEAM_DOMAIN`
   (`<team>.cloudflareaccess.com`), `ACCESS_AUD` and `ACCESS_EMAILS`, then:
   ```sh
   docker compose -f docker/mcp-calendar/docker-compose.yml up -d --build
   docker compose -f docker/tunnel/docker-compose.yml up -d
   ```
5. **Connector.** claude.ai → Settings → Connectors → Add custom connector, URL
   `https://cal-mcp.brignano.io/calendar/mcp`, sign in when asked. It then shows
   up in claude.ai chats, the mobile app and cloud Claude Code sessions.

### Verify

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://cal-mcp.brignano.io/calendar/mcp   # 401 from Access
docker logs mcp-calendar 2>&1 | grep 'public request refused'                       # should stay empty
```
If the connector won't finish signing in, the Access error page names the
redirect URI Claude used. Add that exact URI under Allowed redirect URIs.
