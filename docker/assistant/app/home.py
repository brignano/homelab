"""Home Assistant states, for the digest's "Home" line.

See docs/design/tsd-personal-assistant.md (phase 2: Home Assistant, read-only).

Python decides what is notable
------------------------------
The digest names only what deserves attention. "Garage door open 2h", "Front
door unlocked 6h", "Basement leak sensor unavailable 3d". Which states count is
a table in this file, keyed on Home Assistant's own domain and device_class.
It is never a model's judgement, and never a raw dump of states. Everything
else is a count, so an all-quiet house costs one short line.

What this token can do, stated plainly
--------------------------------------
Unlike Google's scopes, Home Assistant has no read-only token. Any long-lived
access token, even a non-admin user's, can also call services: unlock, open,
disarm. So "read-only" here rests on three things, none of them Google-grade:

  * this code only ever sends GET: to /api/states/<entity_id> for the entities
    named in HA_ENTITIES, to /api/calendars/<entity_id> for those named in
    HA_CALENDARS, and to /api/config for the temperature unit. It has no code
    path that writes;
  * no model is involved, so nothing this module reads can steer what it does;
  * the token belongs to a dedicated non-admin HA user, so it cannot change
    HA's configuration, add integrations, or mint more tokens.

A leaked HA_TOKEN is still a key to the house. Treat it like one, keep it only
in docker/assistant/.env, and revoke it in HA (Profile → Security) if in doubt.
The act lane (phase 3) uses HA's Assist exposure settings instead, which is a
real per-entity ceiling. This lane cannot, because the REST API ignores it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from .google import Event, _when

log = logging.getLogger(__name__)

# binary_sensor device classes where "on" is the thing worth saying, and what
# "on" means for each. motion, occupancy, presence and the like are left out
# on purpose: someone being home is not an event.
BINARY_ON = {
    "door": "open",
    "garage_door": "open",
    "window": "open",
    "opening": "open",
    "lock": "unlocked",       # HA's lock binary_sensor: on means unlocked
    "moisture": "detecting water",
    "smoke": "detecting smoke",
    "gas": "detecting gas",
    "carbon_monoxide": "detecting CO",
    "safety": "unsafe",
    "problem": "reporting a problem",
    "tamper": "tampered",
    "battery": "battery low",  # HA's battery binary_sensor: on means low
}

COVER_NOTABLE = {"open", "opening"}
LOCK_NOTABLE = {"unlocked", "unlocking", "open", "opening", "jammed"}
OFFLINE = {"unavailable", "unknown"}
BATTERY_LOW_PCT = 20.0
# A thermostat reading outside this band means the heating or cooling is not
# keeping up (or has failed), whatever mode it is in. Fahrenheit, because the
# thresholds were chosen in it; a Celsius house is converted, never compared raw.
COLD_F = 55.0
HOT_F = 85.0


class HomeError(RuntimeError):
    """Home Assistant could not be read at all."""


@dataclass(frozen=True)
class Reading:
    entity_id: str
    name: str
    state: str
    # When the state last changed, so the digest can say "for 2h".
    since: dt.datetime | None
    # What makes it worth a mention, or None when it is quiet.
    notable: str | None


def _fahrenheit(value: float, unit: str | None) -> float | None:
    if unit == "°F":
        return value
    if unit == "°C":
        return value * 9 / 5 + 32
    return None


def _climate(state: str, attributes: dict, temp_unit: str | None) -> str | None:
    """A thermostat is worth a mention when it is off, or when the room is
    outside COLD_F..HOT_F. An unknown unit judges nothing about temperature:
    guessing °F in a °C house would call every room freezing."""
    said: list[str] = []
    raw = attributes.get("current_temperature")
    try:
        temp = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        temp = None
    if temp is not None:
        f = _fahrenheit(temp, temp_unit)
        if f is not None and (f < COLD_F or f > HOT_F):
            said.append(f"at {temp:.0f}{temp_unit}")
    elif state != "off":
        # No reading is not a reading in range. A dead sensor in a cold house
        # is the case this rule exists for, so it is said, not skipped.
        said.append("temperature not read")
    if state == "off":
        said.append("off")
    return " and ".join(said) or None


def notable(entity_id: str, state: str, attributes: dict, temp_unit: str | None = None) -> str | None:
    """The single rule for "worth saying". Pure, so it is the thing tested.

    `temp_unit` is HA's configured temperature unit ("°F" or "°C"), which
    climate entities do not carry themselves. None means it was not read."""
    if state in OFFLINE:
        return "unavailable"
    domain = entity_id.split(".", 1)[0]
    device_class = attributes.get("device_class")
    if domain == "binary_sensor":
        return BINARY_ON.get(device_class) if state == "on" else None
    if domain == "cover":
        return "open" if state in COVER_NOTABLE else None
    if domain == "lock":
        return state if state in LOCK_NOTABLE else None
    if domain == "alarm_control_panel":
        return "TRIGGERED" if state == "triggered" else None
    if domain == "sensor" and device_class == "battery":
        try:
            pct = float(state)
        except ValueError:
            return None
        return f"battery {pct:.0f}%" if pct < BATTERY_LOW_PCT else None
    if domain == "climate":
        return _climate(state, attributes, temp_unit)
    return None


def parse_state(entity_id: str, payload: dict, temp_unit: str | None = None) -> Reading:
    attributes = payload.get("attributes") or {}
    state = str(payload.get("state", "unknown"))
    said = notable(entity_id, state, attributes, temp_unit)
    since = None
    if raw := payload.get("last_changed"):
        try:
            since = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            since = None
    # HA sends +00:00 today. A naive value would make the "for 2h" arithmetic
    # raise in render_lines, outside the collector's never-raises boundary,
    # and take the whole digest down over one timestamp. Read it as UTC.
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=dt.timezone.utc)
    # last_changed is when the HVAC mode changed, not the temperature. "at 52°F
    # 3d" would claim the room has been cold for three days, so a thermostat
    # gets a duration only when it is the mode that is being reported.
    if entity_id.startswith("climate.") and state != "off":
        since = None
    return Reading(
        entity_id=entity_id,
        name=str(attributes.get("friendly_name") or entity_id),
        state=state,
        since=since,
        notable=said,
    )


def parse_calendar(items: list) -> list[Event]:
    """Events from HA's GET /api/calendars/<entity_id>, which uses Google's
    start/end shape ({"dateTime": …} or {"date": …}). HA has no free/busy, so
    every event counts as busy, and an unparseable one is dropped."""
    out: list[Event] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        start, end = _when(item.get("start") or {}), _when(item.get("end") or {})
        if start is None or end is None:
            continue
        # A naive time cannot be sorted or compared with Google's aware ones,
        # and that TypeError would surface in render_lines, outside the
        # collector's never-raises boundary. Read it as UTC, as parse_state does.
        start_at, end_at = (
            w.replace(tzinfo=dt.timezone.utc) if isinstance(w, dt.datetime) and w.tzinfo is None else w
            for w in (start[0], end[0]))
        out.append(Event(
            title=str(item.get("summary") or "(no title)").strip(),
            start=start_at,
            end=end_at,
            all_day=start[1],
            busy=True,
        ))
    return out


def missing(entity_id: str) -> Reading:
    """An entity HA does not know. Reported, because it is almost always a typo
    in HA_ENTITIES or a device that was renamed. Silently skipping it would
    leave a door the digest has stopped watching without anyone deciding so."""
    return Reading(entity_id, entity_id, "missing", None, "not found in Home Assistant")


def for_how_long(since: dt.datetime | None, now: dt.datetime) -> str:
    if since is None:
        return ""
    minutes = max(0, int((now - since).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 48 * 60:
        return f"{minutes // 60}h"
    return f"{minutes // (24 * 60)}d"


class Home:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._token = token

    async def _get(self, path: str, params: dict | None = None):
        """(status, body), the body only for a 200. Only ever a GET; see the
        module docstring."""
        async with self._session.get(
            f"{self._base}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=15),
            params=params,
        ) as resp:
            if resp.status == 401:
                raise HomeError("token rejected (401) — create a new one for the digest user")
            if resp.status == 200:
                return 200, await resp.json(content_type=None)
            return resp.status, None

    async def _state(self, entity_id: str, temp_unit: str | None) -> Reading:
        status, body = await self._get(f"/api/states/{quote(entity_id, safe='')}")
        if status == 404:
            return missing(entity_id)
        if status != 200:
            raise HomeError(f"HTTP {status}")
        return parse_state(entity_id, body, temp_unit)

    async def _temp_unit(self) -> str:
        status, body = await self._get("/api/config")
        if status != 200:
            raise HomeError(f"config: HTTP {status}")
        unit = ((body or {}).get("unit_system") or {}).get("temperature")
        if unit not in ("°F", "°C"):
            raise HomeError(f"unexpected temperature unit {unit!r}")
        return unit

    async def readings(self, entity_ids: tuple[str, ...]) -> list[Reading]:
        """All or nothing. One entity failing for a reason other than "not
        found" means HA itself is unwell, and half a house rendered as quiet
        is the one wrong answer this must never give. The temperature unit is
        part of that: a thermostat judged without it would be a guess."""
        try:
            unit = await self._temp_unit() if any(e.startswith("climate.") for e in entity_ids) else None
            return list(await asyncio.gather(*(self._state(e, unit) for e in entity_ids)))
        except aiohttp.ClientError as exc:
            raise HomeError(f"unreachable ({exc.__class__.__name__})") from exc
        except asyncio.TimeoutError as exc:
            raise HomeError("timed out") from exc

    async def _calendar(self, entity_id: str, start: dt.datetime, end: dt.datetime) -> list[Event]:
        # UTC with a Z: a "+00:00" offset is one unencoded "+" away from
        # arriving as a space.
        def z(when: dt.datetime) -> str:
            return when.astimezone(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
        status, body = await self._get(f"/api/calendars/{quote(entity_id, safe='')}",
                                       {"start": z(start), "end": z(end)})
        # HA answers 400 "Entity not found" for an unknown calendar. Fail the
        # source rather than skip it: a calendar the digest silently stopped
        # reading looks exactly like a free day.
        if status in (400, 404):
            raise HomeError(f"{entity_id} not found in Home Assistant (HTTP {status})")
        if status != 200:
            raise HomeError(f"HTTP {status}")
        return parse_calendar(body)

    async def calendars(self, entity_ids: tuple[str, ...], start: dt.datetime,
                        end: dt.datetime) -> list[list[Event]]:
        """Each calendar's events between start and end. All or nothing, like
        readings(): half a calendar rendered as a free afternoon is wrong."""
        try:
            return list(await asyncio.gather(*(self._calendar(e, start, end) for e in entity_ids)))
        except aiohttp.ClientError as exc:
            raise HomeError(f"unreachable ({exc.__class__.__name__})") from exc
        except asyncio.TimeoutError as exc:
            raise HomeError("timed out") from exc

    async def history(self, entity_ids: tuple[str, ...], start: dt.datetime) -> list[list[dict]]:
        """Each entity's state changes since `start` (the evening review's
        "what happened at home today"). One GET to HA's recorder for the
        entities named in REVIEW_HA_ENTITIES, nothing else."""
        z = start.astimezone(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
        try:
            status, body = await self._get(
                f"/api/history/period/{z}",
                {"filter_entity_id": ",".join(entity_ids), "minimal_response": "",
                 "significant_changes_only": ""})
        except aiohttp.ClientError as exc:
            raise HomeError(f"unreachable ({exc.__class__.__name__})") from exc
        except asyncio.TimeoutError as exc:
            raise HomeError("timed out") from exc
        if status != 200 or not isinstance(body, list):
            raise HomeError(f"history: HTTP {status}")
        return [rows for rows in body if isinstance(rows, list)]


HISTORY_SHOWN = 8


def _num(state: str) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def summarize_history(rows: list[dict], tz: dt.tzinfo) -> str | None:
    """One line for one entity's day, computed here so the model only reads it.

    A number (a battery level) becomes start → end with its low and high; a
    state (charging, cycling) becomes how often each state began and the last
    few changes with their times. None for an entity with no rows.
    """
    if not rows or not isinstance(rows[0], dict):
        return None
    first = rows[0]
    attrs = first.get("attributes") or {}
    name = " ".join(str(attrs.get("friendly_name") or first.get("entity_id") or "?").split())[:80]
    unit = str(attrs.get("unit_of_measurement") or "")
    changes: list[tuple[str, dt.datetime | None]] = []
    offline = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = " ".join(str(row.get("state", "")).split())[:40]
        if state in OFFLINE:
            offline += 1
            continue
        if changes and changes[-1][0] == state:
            continue
        when = _when_changed(row.get("last_changed"), tz)
        changes.append((state, when))
    if not changes:
        return f"{name}: unavailable all day" if offline else None
    tail = f"; unavailable {offline}×" if offline else ""
    nums = [_num(s) for s, _ in changes]
    if all(n is not None for n in nums):
        lo, hi = min(nums), max(nums)
        return (f"{name}: {changes[0][0]} → {changes[-1][0]}{unit and ' ' + unit}"
                f" (low {lo:g}, high {hi:g}){tail}")
    if len(changes) == 1:
        return f"{name}: {changes[0][0]} all day{tail}"
    counts: dict[str, int] = {}
    for state, _ in changes[1:]:
        counts[state] = counts.get(state, 0) + 1
    began = ", ".join(f"{s} ×{n}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    shown = changes[-HISTORY_SHOWN:]
    steps = ", ".join(f"{s} {w.strftime('%H:%M') if w else '?'}" for s, w in shown)
    more = f" (last {HISTORY_SHOWN} of {len(changes)})" if len(changes) > HISTORY_SHOWN else ""
    return f"{name}: {began}; {steps}{more}{tail}"


def _when_changed(raw: object, tz: dt.tzinfo) -> dt.datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.astimezone(tz) if when.tzinfo else None
