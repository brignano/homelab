"""Run one Home Assistant scene. The concierge's only write to the house.

See docs/design/tsd-personal-assistant.md, Decision 3 and phase 4.

Why REST and not HA's MCP server
--------------------------------
home.py promises to call exactly one HA tool, `GetLiveContext`, and a test
holds it to that. Running a scene through MCP would mean `HassTurnOn`, the
same intent that unlocks a door when the door is exposed. So writes live here,
in a separate file, and reach one REST service: `scene.turn_on`.

The ceiling
-----------
Assist exposure does NOT govern the REST API, and HA has no read-only or
per-entity token. So the ceiling for this call is `HA_SCENES` in
docker/concierge/.env: an explicit list of `scene.*` entity IDs. This file
refuses anything not on it, and the service is fixed to `scene/turn_on`, so
no argument can reach a lock, an alarm or a garage door. A scene that itself
unlocks something is the one way around that, which is why the README says to
list only scenes you have read.

Called only from actions.execute(), after a person approved the exact entity
ID in Discord.
"""

from __future__ import annotations

import aiohttp

SERVICE_PATH = "/api/services/scene/turn_on"
TIMEOUT_S = 15


class SceneError(RuntimeError):
    """The scene could not be run."""


class Scenes:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str,
                 allowed: tuple[str, ...]) -> None:
        self._session = session
        self._url = base_url.rstrip("/") + SERVICE_PATH
        self._token = token
        self.allowed = allowed

    async def run(self, entity_id: str) -> None:
        if entity_id not in self.allowed:
            raise SceneError(f"{entity_id} is not in HA_SCENES")
        try:
            async with self._session.post(
                self._url, json={"entity_id": entity_id},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as resp:
                if resp.status == 401:
                    raise SceneError("token rejected (401) — create a new one for the concierge user")
                if resp.status != 200:
                    raise SceneError(f"HTTP {resp.status} from Home Assistant")
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise SceneError(f"could not reach Home Assistant ({exc.__class__.__name__})") from exc
