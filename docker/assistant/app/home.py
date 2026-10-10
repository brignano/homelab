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

  * this code only ever sends GET to /api/states/<entity_id>, for the entities
    named in HA_ENTITIES, and has no code path that writes;
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


def notable(entity_id: str, state: str, attributes: dict) -> str | None:
    """The single rule for "worth saying". Pure, so it is the thing tested."""
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
    return None


def parse_state(entity_id: str, payload: dict) -> Reading:
    attributes = payload.get("attributes") or {}
    state = str(payload.get("state", "unknown"))
    since = None
    if raw := payload.get("last_changed"):
        try:
            since = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            since = None
    return Reading(
        entity_id=entity_id,
        name=str(attributes.get("friendly_name") or entity_id),
        state=state,
        since=since,
        notable=notable(entity_id, state, attributes),
    )


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

    async def _state(self, entity_id: str) -> Reading:
        async with self._session.get(
            f"{self._base}/api/states/{quote(entity_id, safe='')}",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 404:
                return missing(entity_id)
            if resp.status == 401:
                raise HomeError("token rejected (401) — create a new one for the digest user")
            if resp.status != 200:
                raise HomeError(f"HTTP {resp.status}")
            return parse_state(entity_id, await resp.json(content_type=None))

    async def readings(self, entity_ids: tuple[str, ...]) -> list[Reading]:
        """All or nothing. One entity failing for a reason other than "not
        found" means HA itself is unwell, and half a house rendered as quiet
        is the one wrong answer this must never give."""
        try:
            return list(await asyncio.gather(*(self._state(e) for e in entity_ids)))
        except aiohttp.ClientError as exc:
            raise HomeError(f"unreachable ({exc.__class__.__name__})") from exc
        except asyncio.TimeoutError as exc:
            raise HomeError("timed out") from exc
