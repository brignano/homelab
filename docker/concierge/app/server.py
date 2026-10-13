"""The internal HTTP endpoint the assistant bot calls. Not published anywhere.

There is no `ports:` in docker-compose.yml. The bot reaches this over the
`assistant_concierge` network, which nothing else joins. The container is also
on `proxy_proxy`, to reach Radarr and Sonarr, so the containers there could
reach this port too: the bearer token is what stops them, and it is checked
on every route but /healthz.

One task at a time. Nobody but one person asks, and a second request arriving
mid-task waits rather than running two agents against the same spend limit.

Three routes: /v1/ask runs the agent and returns its answer and proposals;
/v1/brief runs the scheduled morning brief, a fixed request with read-only
tools that can return no proposals; /v1/execute carries out ONE proposal after
the bot saw a ✅ for it. The last never runs the model, and only acts on an id
this process handed out, with exactly the tool and arguments it stored
(app/actions.py).
"""

from __future__ import annotations

import asyncio
import hmac
import logging

import aiohttp
from aiohttp import web

from . import actions, agent, audit
from .config import Config
from .google import Google
from .home import Home
from .forecast import Forecast
from .hacalendar import HaCalendars
from .homelab import Homelab
from .media import Media, Radarr, Sonarr
from .scenes import Scenes

log = logging.getLogger(__name__)

MAX_PROMPT = 8000
MAX_HISTORY = 40
MAX_MEMORIES = 200


def _bad(reason: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": reason}, status=status)


def validate(body: object) -> tuple[str, list[dict], list[str]] | str:
    """The request, or a reason it is refused. Pure; tested."""
    if not isinstance(body, dict):
        return "body must be a JSON object"
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return "prompt must be a non-empty string"
    if len(prompt) > MAX_PROMPT:
        return f"prompt is over {MAX_PROMPT} characters"
    history = body.get("history", [])
    if not isinstance(history, list) or len(history) > MAX_HISTORY:
        return f"history must be a list of at most {MAX_HISTORY} turns"
    turns = []
    for t in history:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant") \
                or not isinstance(t.get("content"), str):
            return "each history turn needs role (user|assistant) and content"
        turns.append({"role": t["role"], "content": t["content"]})
    memories = body.get("memories", [])
    if not isinstance(memories, list) or len(memories) > MAX_MEMORIES \
            or not all(isinstance(m, str) for m in memories):
        return f"memories must be a list of at most {MAX_MEMORIES} strings"
    return prompt.strip(), turns, memories


def validate_brief(body: object) -> list[str] | str:
    """The brief takes approved memories and nothing else: its request is fixed
    in agent.brief_prompt, so no caller text can become the task. Pure; tested."""
    if not isinstance(body, dict):
        return "body must be a JSON object"
    if set(body) - {"memories"}:
        return "the brief takes only `memories`"
    memories = body.get("memories", [])
    if not isinstance(memories, list) or len(memories) > MAX_MEMORIES \
            or not all(isinstance(m, str) for m in memories):
        return f"memories must be a list of at most {MAX_MEMORIES} strings"
    return memories


def media_from(cfg: Config, session: aiohttp.ClientSession | None) -> Media:
    """Radarr and/or Sonarr, whichever has a key."""
    return Media(
        Radarr(session, "radarr", cfg.radarr_url, cfg.radarr_api_key) if cfg.radarr_api_key else None,
        Sonarr(session, "sonarr", cfg.sonarr_url, cfg.sonarr_api_key) if cfg.sonarr_api_key else None,
        cfg.media_quality_profile, cfg.radarr_root, cfg.sonarr_root,
    )


class Concierge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = asyncio.Lock()
        self._in_flight = 0
        self._session: aiohttp.ClientSession | None = None
        self.google: Google | None = None
        self.home: Home | None = None
        self.scenes: Scenes | None = None
        self.media: Media | None = None
        self.forecast: Forecast | None = None
        self.ha_calendars: HaCalendars | None = None
        self.homelab: Homelab | None = None
        self.pending = actions.Pending()

    async def start(self, app: web.Application) -> None:
        self._session = aiohttp.ClientSession()
        if self.cfg.google_enabled:
            self.google = Google(self._session, self.cfg.google_client_id,
                                 self.cfg.google_client_secret, self.cfg.google_refresh_token)
        if self.cfg.home_enabled:
            self.home = Home(self.cfg.ha_url, self.cfg.ha_token)
        if self.cfg.scenes_enabled:
            self.scenes = Scenes(self._session, self.cfg.ha_url, self.cfg.ha_token, self.cfg.ha_scenes)
        if self.cfg.media_enabled:
            self.media = media_from(self.cfg, self._session)
        if self.cfg.forecast_enabled:
            self.forecast = Forecast(self._session, self.cfg.ha_url, self.cfg.ha_token, self.cfg.ha_weather_entity)
        if self.cfg.ha_calendars_enabled:
            self.ha_calendars = HaCalendars(self._session, self.cfg.ha_url, self.cfg.ha_token,
                                            self.cfg.ha_calendars)
        if self.cfg.prometheus_url:
            self.homelab = Homelab(self._session, self.cfg.prometheus_url)
        log.info("concierge ready: model=%s google=%s home=%s scenes=%d radarr=%s sonarr=%s "
                 "forecast=%s ha_calendars=%d homelab=%s",
                 self.cfg.model, self.google is not None, self.home is not None,
                 len(self.cfg.ha_scenes) if self.scenes else 0,
                 bool(self.media and self.media.radarr), bool(self.media and self.media.sonarr),
                 self.forecast is not None, len(self.cfg.ha_calendars) if self.ha_calendars else 0,
                 self.homelab is not None)

    async def stop(self, app: web.Application) -> None:
        if self._session is not None:
            await self._session.close()

    def _authorised(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        expected = f"Bearer {self.cfg.token}"
        return hmac.compare_digest(header.encode(), expected.encode())

    async def healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "busy": self._in_flight})

    async def ask(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            log.warning("refused /v1/ask from %s: bad or missing token", request.remote)
            return _bad("unauthorised", 401)
        try:
            body = await request.json()
        except ValueError:
            return _bad("body is not JSON")
        parsed = validate(body)
        if isinstance(parsed, str):
            return _bad(parsed)
        prompt, history, memories = parsed

        outcome = await self._run(prompt=prompt, history=history, memories=memories)
        if isinstance(outcome, web.Response):
            return outcome

        for p in outcome.proposals:
            if p["tool"] in actions.EXECUTABLE:
                self.pending.add(p)
        return self._reply(outcome)

    async def brief(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            log.warning("refused /v1/brief from %s: bad or missing token", request.remote)
            return _bad("unauthorised", 401)
        try:
            body = await request.json()
        except ValueError:
            return _bad("body is not JSON")
        memories = validate_brief(body)
        if isinstance(memories, str):
            return _bad(memories)
        outcome = await self._run(prompt="", history=[], memories=memories, brief=True)
        if isinstance(outcome, web.Response):
            return outcome
        # Nothing from a brief is ever made executable: it has no proposal
        # tools, and this does not consult outcome.proposals at all.
        return self._reply(outcome)

    async def _run(self, **kwargs) -> agent.Outcome | web.Response:
        """One agent task under the lock and the timeout, or the error response."""
        if self._in_flight >= self.cfg.max_queue:
            return _bad("busy", 429)
        async def locked_run() -> agent.Outcome:
            async with self._lock:
                return await agent.run(self.cfg, self.google, self.home,
                                       media=self.media, forecast=self.forecast,
                                       ha_calendars=self.ha_calendars,
                                       homelab=self.homelab, **kwargs)

        self._in_flight += 1
        try:
            # The timeout covers waiting for the lock as well as the run. The
            # bot gives up a minute after CONCIERGE_TIMEOUT_S, and aiohttp does
            # not cancel a handler whose client left, so a run that started
            # after a long wait would spend money on an answer nobody receives.
            outcome = await asyncio.wait_for(locked_run(), timeout=self.cfg.timeout_s)
        except asyncio.TimeoutError:
            log.warning("task timed out after %ss", self.cfg.timeout_s)
            return _bad(f"timed out after {self.cfg.timeout_s}s", 504)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the server
            log.exception("task failed")
            return _bad(f"{exc.__class__.__name__}: {exc}", 502)
        finally:
            self._in_flight -= 1
        return outcome

    @staticmethod
    def _reply(outcome: agent.Outcome) -> web.Response:
        return web.json_response({
            "ok": outcome.ok,
            "task": outcome.task_id,
            "answer": outcome.answer,
            "proposals": outcome.proposals,
            "tool_calls": outcome.tool_calls,
            "turns": outcome.turns,
            "cost_usd": outcome.cost_usd,
            "seconds": round(outcome.seconds, 1),
            "model": outcome.model,
            "stop": outcome.stop,
            "reads_ok": outcome.reads_ok,
        })


    async def execute(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            log.warning("refused /v1/execute from %s: bad or missing token", request.remote)
            return _bad("unauthorised", 401)
        try:
            body = await request.json()
        except ValueError:
            return _bad("body is not JSON")
        if not isinstance(body, dict):
            return _bad("body must be a JSON object")
        taken = self.pending.take(body.get("id"), body.get("tool"), body.get("args"))
        if isinstance(taken, str):
            audit.emit("execute", id=str(body.get("id"))[:32], tool=str(body.get("tool"))[:40],
                       ok=False, error=taken)
            return _bad(taken, 410 if taken.startswith("expired") else 409)
        tool, args = taken
        try:
            summary = await actions.execute(tool, args, self.google, self.scenes, self.media)
        except actions.ActionError as exc:
            audit.emit("execute", id=body["id"], tool=tool, ok=False, error=str(exc))
            return _bad(str(exc), 502)
        except Exception as exc:  # noqa: BLE001 — a ✅ must always get an answer
            log.exception("execute failed")
            audit.emit("execute", id=body["id"], tool=tool, ok=False, error=repr(exc))
            return _bad(f"outcome unknown ({exc.__class__.__name__}) — check before asking again", 502)
        audit.emit("execute", id=body["id"], tool=tool, ok=True)
        return web.json_response({"ok": True, "summary": summary})


def make_app(cfg: Config) -> web.Application:
    concierge = Concierge(cfg)
    app = web.Application(client_max_size=256 * 1024)
    app.on_startup.append(concierge.start)
    app.on_cleanup.append(concierge.stop)
    app.router.add_get("/healthz", concierge.healthz)
    app.router.add_post("/v1/ask", concierge.ask)
    app.router.add_post("/v1/brief", concierge.brief)
    app.router.add_post("/v1/execute", concierge.execute)
    return app


def serve(cfg: Config) -> None:
    web.run_app(make_app(cfg), host="0.0.0.0", port=cfg.port, print=None, access_log=None)
