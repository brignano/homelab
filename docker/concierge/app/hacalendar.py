"""Calendars Home Assistant can see (Apple/iCloud via CalDAV, and the like). Read-only.

The Google tools only see Google. Anthony's Apple calendar reaches this house
through Home Assistant's CalDAV integration, privately, which the digest's
Today section already uses. This gives the concierge the same view, so the
morning brief can see the week on both, with no public iCloud link anywhere.

The ceiling
-----------
Like app/forecast.py this is REST, where Assist exposure does not apply, so
the ceiling is `HA_CALENDARS` in docker/concierge/.env: `calendar.*` entity
IDs, checked at startup. The path is fixed (`GET /api/calendars/<entity>`),
only those entities reach the URL, and the model picks nothing but how many
days ahead to look. GET only: nothing here can change a calendar.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote

import aiohttp

PATH = "/api/calendars/"
MAX_DAYS = 14
MAX_EVENTS = 60
TIMEOUT_S = 15


class CalendarError(RuntimeError):
    """A Home Assistant calendar could not be read."""


def _when(value: object) -> str | None:
    """HA's start/end: {"dateTime": ...} or {"date": ...}, like Google's."""
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date")
    return value[:40] if isinstance(value, str) else None


def trim(items: object) -> list[dict]:
    """HA's events cut to the fields the brief needs and a fixed count. Pure; tested."""
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
    def __init__(self, session: aiohttp.ClientSession | None, base_url: str, token: str,
                 entities: tuple[str, ...]) -> None:
        self._session = session
        self._base = base_url.rstrip("/") + PATH
        self._token = token
        self.entities = entities

    async def _one(self, entity: str, start: dt.datetime, end: dt.datetime) -> list[dict]:
        if self._session is None:
            raise CalendarError("no HTTP session")
        try:
            async with self._session.get(
                self._base + quote(entity, safe=""),
                params={"start": start.isoformat(), "end": end.isoformat()},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as resp:
                if resp.status == 401:
                    raise CalendarError("token rejected (401) — create a new one for the concierge user")
                if resp.status != 200:
                    raise CalendarError(f"HTTP {resp.status} from Home Assistant — is {entity} the right entity?")
                try:
                    payload = await resp.json(content_type=None)
                except ValueError as exc:
                    raise CalendarError("unexpected reply from Home Assistant (not JSON)") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise CalendarError(f"could not reach Home Assistant ({exc.__class__.__name__})") from exc
        return trim(payload)

    async def events(self, now: dt.datetime, days: int) -> dict[str, object]:
        """Every configured calendar from the start of today through `days` ahead."""
        days = max(0, min(days, MAX_DAYS))
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=days + 1)
        out: dict[str, object] = {}
        for entity in self.entities:
            # One bad calendar (renamed, timed out) must not hide the others.
            try:
                out[entity] = await self._one(entity, start, end)
            except CalendarError as exc:
                out[entity] = {"error": str(exc)}
        if all(isinstance(v, dict) for v in out.values()):
            raise CalendarError("; ".join(f"{e}: {v['error']}" for e, v in out.items()))
        return out
