"""Home Assistant, read through its MCP server, one tool only.

See docs/design/tsd-personal-assistant.md, Decision 3.

Why a proxy instead of handing the agent HA's MCP server
---------------------------------------------------------
HA's MCP server publishes its whole Assist API: `GetLiveContext` to read, and
`HassTurnOn`, `HassTurnOff`, `HassLightSet`, `HassClimateSetTemperature` and the
rest to act. Connected directly, every one of those would be in the agent's
tool list, refused only by the tier check. The TSD's **never** tier means "not
registered at all", so the agent is given one tool of ours, `home_overview`,
and this module calls exactly one tool on HA's side: `GetLiveContext`, which
newer HA names `homeassistant__GetLiveContext`. There is no code path here that
names any other HA tool.

The two ceilings, in order
--------------------------
1. **Assist exposure, in HA** (Settings → Voice assistants → Expose). That is
   what GetLiveContext reports on, and it is a real per-entity ceiling, unlike
   the REST API the digest uses. Locks, alarm panels and garage covers are not
   exposed. That is the layer that holds even if this file is wrong.
2. **This file**, which reads and never acts.

The token belongs to a dedicated non-admin HA user (`concierge`), separate from
the digest's. HA cannot make a token read-only, so it is still a key to
whatever is exposed: keep it only in docker/concierge/.env.

HA runs on host networking, so it is reached through the host —
`host.docker.internal`, mapped by `extra_hosts` in docker-compose.yml.
"""

from __future__ import annotations

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# The ONLY HA tool this process ever calls, under either of its two exact
# names. Newer HA prefixes every MCP tool with the integration that provides
# it, so GetLiveContext became `homeassistant__GetLiveContext`. The name is
# looked up in HA's tool list and must match one of these exactly. A suffix
# match would also accept some other integration's `x__GetLiveContext`.
# Tested.
LIVE_CONTEXT_TOOL = "GetLiveContext"
LIVE_CONTEXT_NAMES = (f"homeassistant__{LIVE_CONTEXT_TOOL}", LIVE_CONTEXT_TOOL)

TIMEOUT_S = 30
MAX_CHARS = 12000


class HomeError(RuntimeError):
    """Home Assistant could not be read."""


def live_context_name(offered: list[str]) -> str | None:
    """The name HA offers GetLiveContext under, or None. Pure; tested."""
    return next((name for name in LIVE_CONTEXT_NAMES if name in offered), None)


class Home:
    def __init__(self, base_url: str, token: str) -> None:
        self._url = base_url.rstrip("/") + "/api/mcp"
        self._token = token

    async def live_context(self) -> str:
        """Every exposed entity's current state, as HA words it."""
        result = None
        try:
            with anyio.fail_after(TIMEOUT_S):
                async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {self._token}"}, timeout=TIMEOUT_S,
                ) as http, streamable_http_client(self._url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        offered = [t.name for t in (await session.list_tools()).tools]
                        # Raised after the session closes, not in it: an
                        # exception inside reaches us wrapped in an
                        # ExceptionGroup, and the reason with it.
                        name = live_context_name(offered)
                        if name is not None:
                            result = await session.call_tool(name, {})
        except TimeoutError as exc:
            raise HomeError(f"no answer from Home Assistant in {TIMEOUT_S}s") from exc
        except HomeError:
            raise
        except Exception as exc:  # noqa: BLE001 — httpx, MCP and anyio all raise their own
            raise HomeError(f"could not reach Home Assistant's MCP server ({exc.__class__.__name__}: {exc})") from exc
        if result is None:
            raise HomeError(f"Home Assistant offers no {LIVE_CONTEXT_TOOL} tool. Expose at least one "
                            "entity to Assist and select the Assist API on the MCP Server integration.")

        text = "\n".join(getattr(c, "text", "") for c in result.content
                         if getattr(c, "type", "") == "text").strip()
        if result.isError:
            raise HomeError(text or "Home Assistant returned an error")
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS].rstrip() + f"\n[… cut at {MAX_CHARS} characters]"
        return text or "(Home Assistant returned no entities — nothing is exposed to Assist)"
