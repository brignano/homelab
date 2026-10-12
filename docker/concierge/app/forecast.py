"""The weather forecast for one Home Assistant weather entity. Read-only.

GetLiveContext (app/home.py) reports current states only. HA keeps forecasts
behind a service, `weather.get_forecasts`, which returns data and changes
nothing. This file calls that one service and nothing else, through the REST
API like app/scenes.py, so home.py keeps its promise of one MCP tool.

The ceiling
-----------
Assist exposure does not govern REST calls, so the ceiling here is
`HA_WEATHER_ENTITY` in docker/concierge/.env: one `weather.*` entity ID, checked
at startup. The service path is fixed, and nothing the model passes reaches the
URL or the entity. It picks only `daily` or `hourly`.
"""

from __future__ import annotations

from typing import Any

import aiohttp

SERVICE_PATH = "/api/services/weather/get_forecasts?return_response"
KINDS = ("daily", "hourly")
LIMITS = {"daily": 7, "hourly": 24}
FIELDS = ("datetime", "condition", "temperature", "templow", "precipitation_probability",
          "precipitation", "wind_speed", "humidity")
TIMEOUT_S = 15


class ForecastError(RuntimeError):
    """The forecast could not be read."""


def trim(payload: object, entity: str, kind: str) -> list[dict]:
    """HA's service response cut to known fields and a fixed length. Pure; tested."""
    if not isinstance(payload, dict):
        raise ForecastError("unexpected reply from Home Assistant")
    by_entity = payload.get("service_response", payload)
    data = by_entity.get(entity) if isinstance(by_entity, dict) else None
    rows = data.get("forecast") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ForecastError(f"Home Assistant returned no {kind} forecast for {entity}")
    out = []
    for row in rows[:LIMITS[kind]]:
        if not isinstance(row, dict):
            continue
        kept: dict[str, Any] = {}
        for key in FIELDS:
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                kept[key] = value
            elif isinstance(value, str):
                kept[key] = value[:40]
        if kept:
            out.append(kept)
    return out


class Forecast:
    def __init__(self, session: aiohttp.ClientSession | None, base_url: str, token: str, entity: str) -> None:
        self._session = session
        self._url = base_url.rstrip("/") + SERVICE_PATH
        self._token = token
        self.entity = entity

    async def get(self, kind: str) -> list[dict]:
        if kind not in KINDS:
            raise ForecastError(f"kind must be one of: {', '.join(KINDS)}")
        if self._session is None:
            raise ForecastError("no HTTP session")
        try:
            async with self._session.post(
                self._url, json={"entity_id": self.entity, "type": kind},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as resp:
                if resp.status == 401:
                    raise ForecastError("token rejected (401) — create a new one for the concierge user")
                if resp.status != 200:
                    raise ForecastError(f"HTTP {resp.status} from Home Assistant — is {self.entity} the right entity?")
                try:
                    payload = await resp.json(content_type=None)
                except ValueError as exc:
                    raise ForecastError("unexpected reply from Home Assistant (not JSON)") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ForecastError(f"could not reach Home Assistant ({exc.__class__.__name__})") from exc
        return trim(payload, self.entity, kind)
