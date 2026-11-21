"""Lock 3 of the secure gateway: check the Cloudflare Access token, again, per route.

Caddy (the `gateway` container) sends every request here first with
`forward_auth`, at `/verify/<route>`. The answer is either 204 with the caller's
identity in `X-Gateway-Identity`, or 403. Nothing in between, and anything this
code cannot be sure of is a 403: a route with no AUD or no allowlist, a team
domain that does not look like one, signing keys that cannot be fetched, a
token that is missing, expired, for another application, or for someone the
route does not name.

Why it exists when Access and cloudflared already check the same token: see
docs/design/tsd-secure-gateway.md, "Four independent locks". This is the one
that runs code from this repo, so it is the one that also checks *who*: the
Alexa service token is valid on the Alexa hostname and on nothing else, even if
someone attaches it to a second Access application by mistake.

Configuration is environment only, one set of variables per route, upper-cased:

    ACCESS_TEAM_DOMAIN   <team>.cloudflareaccess.com
    GW_HOST_<ROUTE>      the route's public hostname (exact match)
    GW_AUD_<ROUTE>       the route's Access application AUD tag
    GW_ALLOW_<ROUTE>     comma-separated identities: an email for a person,
                         `token:<client id>` for a service token

Logic carried over from docker/mcp-calendar/app/access.py (removed in #348).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping

import jwt

PORT = int(os.environ.get("VERIFIER_PORT", "9000"))
TEAM = re.compile(r"^[a-z0-9-]+\.cloudflareaccess\.com$")
ROUTE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# How far a token's exp/iat may disagree with this box's clock. Access tokens
# for service auth live minutes, so a small window costs nothing.
LEEWAY_SECONDS = 30
LOG = logging.getLogger("gateway.verifier")


class Refused(Exception):
    """A request that must not pass. The message goes to the log, never to the client."""


@dataclass(frozen=True)
class Route:
    name: str
    host: str
    aud: str
    allow: frozenset[str]


def _env_key(route: str, what: str) -> str:
    return f"GW_{what}_{route.upper()}"


def load_route(name: str, env: Mapping[str, str]) -> Route:
    """The route's config, or Refused. A half-configured route is no route."""
    if not ROUTE.match(name):
        raise Refused("bad route name")
    host = env.get(_env_key(name, "HOST"), "").strip().lower().rstrip(".")
    aud = env.get(_env_key(name, "AUD"), "").strip()
    allow = frozenset(
        a.strip().lower() for a in env.get(_env_key(name, "ALLOW"), "").split(",") if a.strip()
    )
    if not (host and aud and allow):
        raise Refused(f"route {name} is not configured (GW_HOST/GW_AUD/GW_ALLOW)")
    return Route(name, host, aud, allow)


def identity_of(claims: Mapping[str, object]) -> str:
    """`email` for a person, `token:<client id>` for a service token, or Refused."""
    email = str(claims.get("email") or "").strip().lower()
    if email:
        return email
    common_name = str(claims.get("common_name") or "").strip().lower()
    # A service token's JWT has an empty `sub` and its Client ID as common_name.
    if common_name and not str(claims.get("sub") or ""):
        return f"token:{common_name}"
    raise Refused("token names no identity")


class Verifier:
    def __init__(self, team: str, env: Mapping[str, str],
                 keys: Callable[[str], object] | None = None):
        team = team.strip().lower().removeprefix("https://").rstrip("/")
        self.team = team if TEAM.match(team) else ""
        self.env = env
        if keys is not None:
            self._key_for = keys
        elif self.team:
            client = jwt.PyJWKClient(
                f"https://{self.team}/cdn-cgi/access/certs",
                cache_keys=True, lifespan=300, timeout=5,
            )
            self._key_for = lambda token: client.get_signing_key_from_jwt(token).key
        else:
            self._key_for = None

    def check(self, route_name: str, headers: Mapping[str, str]) -> str:
        """The verified identity, or Refused. Blocking: it may fetch signing keys."""
        if not self._key_for or not self.team:
            raise Refused("ACCESS_TEAM_DOMAIN is not set or not a cloudflareaccess.com name")
        route = load_route(route_name, self.env)

        # Caddy sets X-Forwarded-Host from the request's own Host (no trusted
        # proxies are configured, so a client-sent value is replaced). It binds
        # the token check to the hostname the route was written for, so a route
        # block pasted under the wrong site address fails here.
        host = (headers.get("x-forwarded-host") or "").strip().lower().rstrip(".")
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if host != route.host:
            raise Refused("host does not match the route")

        token = (headers.get("cf-access-jwt-assertion") or "").strip()
        if not token:
            raise Refused("no Access token")
        try:
            key = self._key_for(token)
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=route.aud,
                issuer=f"https://{self.team}", leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise Refused(f"bad Access token: {exc}") from exc
        if claims.get("type", "app") != "app":
            raise Refused("not an application token")
        who = identity_of(claims)
        if who not in route.allow:
            raise Refused(f"{who} is not allowed on route {route.name}")
        return who


def _log(**fields: object) -> None:
    fields = {"ts": round(time.time(), 3), "logger": "gateway.verifier", **fields}
    print(json.dumps(fields, separators=(",", ":")), flush=True)


def make_handler(verifier: Verifier):
    class Handler(BaseHTTPRequestHandler):
        server_version = "verifier"
        sys_version = ""

        def log_message(self, *args) -> None:  # the JSON line below replaces it
            pass

        def _answer(self, status: int, identity: str = "") -> None:
            self.send_response(status)
            if identity:
                self.send_header("X-Gateway-Identity", identity)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "0" if status == 204 else "9")
            self.end_headers()
            if status != 204:
                self.wfile.write(b"Forbidden" if status == 403 else b"Not found")

        def _handle(self) -> None:
            if self.path == "/healthz":
                return self._answer(204)
            if not self.path.startswith("/verify/"):
                return self._answer(404)
            route = self.path[len("/verify/"):].split("?", 1)[0]
            headers = {k.lower(): v for k, v in self.headers.items()}
            ip = headers.get("cf-connecting-ip") or headers.get("x-forwarded-for", "").split(",")[0].strip()
            base = {
                "event": "verify",
                "route": route[:40],
                "request_id": headers.get("x-request-id", "")[:64],
                "method": headers.get("x-forwarded-method", "")[:10],
                "uri": headers.get("x-forwarded-uri", "")[:200],
                "ip": ip[:64],
                "country": headers.get("cf-ipcountry", "")[:8],
            }
            try:
                who = verifier.check(route, headers)
            except Refused as exc:
                _log(**base, ok=False, reason=str(exc)[:300])
                return self._answer(403)
            except Exception as exc:  # anything unexpected is a refusal, never a pass
                LOG.exception("verifier error")
                _log(**base, ok=False, reason=f"verifier error: {type(exc).__name__}")
                return self._answer(403)
            _log(**base, ok=True, identity=who)
            self._answer(204, who)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    verifier = Verifier(os.environ.get("ACCESS_TEAM_DOMAIN", ""), os.environ)
    if not verifier.team:
        _log(event="start", ok=False, reason="ACCESS_TEAM_DOMAIN unset or invalid: every request will be refused")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(verifier))
    server.daemon_threads = True
    _log(event="start", ok=True, port=PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
