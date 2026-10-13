"""Read-only MCP server over the calendars Home Assistant can see.

Served at /calendar/mcp on mcp.$HOMELAB_DOMAIN, behind the same Caddy bearer
check as mcp-grafana. Two tools, both reads: list_calendars and
calendar_events. See docker/mcp-calendar/README.md.
"""

from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

import aiohttp
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from .calendars import MAX_DAYS, CalendarError, HaCalendars, parse_entities, window

PATH = "/calendar/mcp"
# Caddy checks the public name and the bearer token, then sends this internal
# name as Host (header_up in the Caddyfile), same as mcp-grafana's --allowed-hosts.
ALLOWED_HOST = "mcp-calendar:8000"

TZ = ZoneInfo(os.environ.get("TZ") or "America/New_York")
CALENDARS = HaCalendars(
    os.environ.get("HA_URL") or "http://host.docker.internal:8123",
    os.environ.get("HA_TOKEN", ""),
    parse_entities(os.environ.get("HA_CALENDARS", "")),
)

mcp = FastMCP(
    "homelab-calendar",
    instructions=(
        "Anthony's Apple/iCloud calendars, read through Home Assistant. Read-only. "
        f"Times are {TZ.key} unless an event says otherwise."
    ),
    host="0.0.0.0",
    port=8000,
    streamable_http_path=PATH,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[ALLOWED_HOST],
        allowed_origins=[],
    ),
)


@mcp.tool()
async def list_calendars() -> dict:
    """The calendars this server can read, and the timezone dates are in."""
    return {"calendars": list(CALENDARS.entities), "timezone": TZ.key}


@mcp.tool()
async def calendar_events(start: str | None = None, days: int = 7, calendar: str | None = None) -> dict:
    """Events from Anthony's Apple/iCloud calendars.

    start: first day as YYYY-MM-DD (default today), within a year of today.
    days: how many days from start, 1 to 31 (default 7).
    calendar: one ID from list_calendars, or omit for all of them.
    """
    try:
        entities = CALENDARS.pick(calendar)
        begin, end = window(dt.datetime.now(TZ).date(), start, days, TZ)
        async with aiohttp.ClientSession() as session:
            found = await CALENDARS.events(session, entities, begin, end)
    except CalendarError as exc:
        return {"error": str(exc)}
    return {"from": begin.date().isoformat(), "to": end.date().isoformat(),
            "max_days": MAX_DAYS, "calendars": found}


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    # Liveness only: HA being down shows up as a tool error, not a restart loop.
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
