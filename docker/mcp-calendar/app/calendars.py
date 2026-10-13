"""Calendars Home Assistant can see (Apple/iCloud via CalDAV, and the like). Read-only.

Same ceiling as docker/concierge/app/hacalendar.py, for the same reasons:

- The only HA call is `GET /api/calendars/<entity>`. The path is fixed and
  nothing here sends any other method, so a calendar cannot be changed.
- Only the `calendar.*` IDs in `HA_CALENDARS` ever reach that URL. A caller can
  pick one of them, a start date and a number of days, and nothing else.
- Events are cut to title, start, end and location. Descriptions and attendee
  lists are text other people wrote, and nothing here needs them.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import quote

import aiohttp

PATH = "/api/calendars/"
MAX_DAYS = 31
MAX_EVENTS = 100
# How far from today a window may start, either way. A year covers "when did I
# last..." and "what's on in the spring" without turning this into an export.
MAX_OFFSET_DAYS = 366
TIMEOUT_S = 15

_ENTITY = re.compile(r"^calendar\.[a-z0-9_]+$")


class CalendarError(RuntimeError):
    """A Home Assistant calendar could not be read, or the request was refused."""


def parse_entities(raw: str) -> tuple[str, ...]:
    """HA_CALENDARS as a tuple of calendar.* IDs. Anything else is refused at startup."""
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not _ENTITY.match(part):
            raise ValueError(f"HA_CALENDARS: {part!r} is not a calendar.* entity ID")
        if part not in out:
            out.append(part)
    return tuple(out)


def window(today: dt.date, start: str | None, days: int, tz: dt.tzinfo) -> tuple[dt.datetime, dt.datetime]:
    """The [start, end) range to ask HA for, clamped. Pure; tested."""
    if start:
        try:
            first = dt.date.fromisoformat(start)
        except ValueError as exc:
            raise CalendarError(f"start must be a date like 2026-10-17, not {start!r}") from exc
    else:
        first = today
    if abs((first - today).days) > MAX_OFFSET_DAYS:
        raise CalendarError(f"start must be within {MAX_OFFSET_DAYS} days of today")
    days = max(1, min(int(days), MAX_DAYS))
    begin = dt.datetime.combine(first, dt.time.min, tzinfo=tz)
    return begin, begin + dt.timedelta(days=days)


def _when(value: object) -> str | None:
    """HA's start/end: {"dateTime": ...} or {"date": ...}, like Google's."""
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date")
    return value[:40] if isinstance(value, str) else None


def trim(items: object) -> list[dict]:
    """HA's events cut to the fields worth returning and a fixed count. Pure; tested."""
    if not isinstance(items, list):
        raise CalendarError("unexpected reply from Home Assistant")
    out = []
    for item in items[:MAX_EVENTS]:
        if not isinstance(item, dict):
            continue
        start, end = _when(item.get("start")), _when(item.get("end"))
        if start is None:
            continue
        event = {"title": str(item.get("summary") or "(no title)")[:120], "start": start}
        if end:
            event["end"] = end
        if isinstance(item.get("location"), str) and item["location"]:
            event["location"] = item["location"][:80]
        out.append(event)
    return out


class HaCalendars:
    def __init__(self, base_url: str, token: str, entities: tuple[str, ...]) -> None:
        self._base = base_url.rstrip("/") + PATH
        self._token = token
        self.entities = entities

    def pick(self, calendar: str | None) -> tuple[str, ...]:
        """All configured calendars, or the one asked for if it is configured."""
        if not self._token:
            raise CalendarError("HA_TOKEN is not set in docker/mcp-calendar/.env")
        if not self.entities:
            raise CalendarError("HA_CALENDARS is empty in docker/mcp-calendar/.env")
        if calendar is None or calendar == "":
            return self.entities
        if calendar not in self.entities:
            raise CalendarError(f"unknown calendar {calendar!r}; use one of {', '.join(self.entities)}")
        return (calendar,)

    async def _one(self, session: aiohttp.ClientSession, entity: str,
                   begin: dt.datetime, end: dt.datetime) -> list[dict]:
        try:
            async with session.get(
                self._base + quote(entity, safe=""),
                params={"start": begin.isoformat(), "end": end.isoformat()},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as resp:
                if resp.status == 401:
                    raise CalendarError("token rejected (401) by Home Assistant")
                if resp.status != 200:
                    raise CalendarError(f"HTTP {resp.status} from Home Assistant — is {entity} the right entity?")
                try:
                    payload = await resp.json(content_type=None)
                except ValueError as exc:
                    raise CalendarError("unexpected reply from Home Assistant (not JSON)") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise CalendarError(f"could not reach Home Assistant ({exc.__class__.__name__})") from exc
        return trim(payload)

    async def events(self, session: aiohttp.ClientSession, entities: tuple[str, ...],
                     begin: dt.datetime, end: dt.datetime) -> dict[str, object]:
        out: dict[str, object] = {}
        for entity in entities:
            # One bad calendar (renamed, timed out) must not hide the others.
            try:
                out[entity] = await self._one(session, entity, begin, end)
            except CalendarError as exc:
                out[entity] = {"error": str(exc)}
        if all(isinstance(v, dict) for v in out.values()):
            raise CalendarError("; ".join(f"{e}: {v['error']}" for e, v in out.items()))
        return out
