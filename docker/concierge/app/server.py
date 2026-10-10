"""The internal HTTP endpoint the assistant bot calls. Not published anywhere.

There is no `ports:` in docker-compose.yml. The only route here is the
`assistant_concierge` Docker network, which the bot and this container share
and nothing else joins. The bearer token is the second lock, so a container
that one day lands on that network by mistake still cannot spend the API budget.

One task at a time. Nobody but one person asks, and a second request arriving
mid-task waits rather than running two agents against the same spend limit.
"""

from __future__ import annotations

import asyncio
import hmac
import logging

import aiohttp
from aiohttp import web

from . import agent
from .config import Config
from .google import Google
from .home import Home

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


class Concierge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = asyncio.Lock()
        self._in_flight = 0
        self._session: aiohttp.ClientSession | None = None
        self.google: Google | None = None
        self.home: Home | None = None

    async def start(self, app: web.Application) -> None:
        self._session = aiohttp.ClientSession()
        if self.cfg.google_enabled:
            self.google = Google(self._session, self.cfg.google_client_id,
                                 self.cfg.google_client_secret, self.cfg.google_refresh_token)
        if self.cfg.home_enabled:
            self.home = Home(self.cfg.ha_url, self.cfg.ha_token)
        log.info("concierge ready: model=%s google=%s home=%s",
                 self.cfg.model, self.google is not None, self.home is not None)

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

        if self._in_flight >= self.cfg.max_queue:
            return _bad("busy", 429)
        self._in_flight += 1
        try:
            async with self._lock:
                outcome = await asyncio.wait_for(
                    agent.run(self.cfg, self.google, self.home,
                              prompt=prompt, history=history, memories=memories),
                    timeout=self.cfg.timeout_s,
                )
        except asyncio.TimeoutError:
            log.warning("task timed out after %ss", self.cfg.timeout_s)
            return _bad(f"timed out after {self.cfg.timeout_s}s", 504)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the server
            log.exception("task failed")
            return _bad(f"{exc.__class__.__name__}: {exc}", 502)
        finally:
            self._in_flight -= 1

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
        })


def make_app(cfg: Config) -> web.Application:
    concierge = Concierge(cfg)
    app = web.Application(client_max_size=256 * 1024)
    app.on_startup.append(concierge.start)
    app.on_cleanup.append(concierge.stop)
    app.router.add_get("/healthz", concierge.healthz)
    app.router.add_post("/v1/ask", concierge.ask)
    return app


def serve(cfg: Config) -> None:
    web.run_app(make_app(cfg), host="0.0.0.0", port=cfg.port, print=None, access_log=None)
