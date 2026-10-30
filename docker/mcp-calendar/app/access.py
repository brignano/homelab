"""The gate in front of the public listener: a Cloudflare Access JWT, checked here.

cloudflared reaches this server on PUBLIC_PORT, and only there. Cloudflare Access
already refuses anyone its policy doesn't name, but this server doesn't take that
on trust: every request on PUBLIC_PORT must carry a Cf-Access-Jwt-Assertion that
verifies against the team's signing keys, for this app's AUD, for an allowed
email. A missing or bad token, or an unconfigured gate, gets a 403. See README.md.
"""

from __future__ import annotations

import logging
import re

import anyio
import jwt

PUBLIC_PORT = 8001
HEADER = b"cf-access-jwt-assertion"
LOG = logging.getLogger("mcp-calendar.access")
TEAM = re.compile(r"^[a-z0-9-]+\.cloudflareaccess\.com$")


class AccessError(Exception):
    pass


class AccessCheck:
    """Verifies Cloudflare Access JWTs. Unconfigured, it verifies nothing and rejects all."""

    def __init__(self, team: str, aud: str, emails: str):
        team = team.strip().lower().removeprefix("https://").rstrip("/")
        self.team = team if TEAM.match(team) else ""
        self.aud = aud.strip()
        self.emails = frozenset(e.strip().lower() for e in emails.split(",") if e.strip())
        self._keys = jwt.PyJWKClient(f"https://{self.team}/cdn-cgi/access/certs") if self.team else None

    @property
    def configured(self) -> bool:
        return bool(self._keys and self.aud and self.emails)

    def verify(self, token: str) -> str:
        """The verified email, or AccessError. Blocking: it may fetch the signing keys."""
        if not self.configured:
            raise AccessError("Access is not configured (ACCESS_TEAM_DOMAIN, ACCESS_AUD, ACCESS_EMAILS)")
        if not token:
            raise AccessError("no Access token")
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.aud,
                                issuer=f"https://{self.team}", options={"require": ["exp", "iat", "aud"]})
        except jwt.PyJWTError as exc:
            raise AccessError(f"bad Access token: {exc}") from exc
        email = str(claims.get("email", "")).lower()
        if email not in self.emails:
            raise AccessError("email not allowed")
        return email


def gate(app, check: AccessCheck, internal_host: bytes):
    """ASGI wrapper: requests on PUBLIC_PORT need a valid Access JWT; others pass untouched.

    A verified request gets Host rewritten to the internal name, so the MCP app's
    Host allowlist never has to list the public hostname. Pointing the tunnel at
    the internal port by mistake therefore fails with a 421 instead of skipping
    this check.
    """

    async def wrapped(scope, receive, send):
        if scope["type"] != "http" or (scope.get("server") or (None, None))[1] != PUBLIC_PORT:
            return await app(scope, receive, send)
        token = next((v.decode("latin-1") for k, v in scope["headers"] if k == HEADER), "")
        try:
            await anyio.to_thread.run_sync(check.verify, token)
        except AccessError as exc:
            LOG.warning("public request refused: %s", exc)
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"text/plain")]})
            return await send({"type": "http.response.body", "body": b"Forbidden"})
        headers = [(k, v) for k, v in scope["headers"] if k != b"host"] + [(b"host", internal_host)]
        return await app({**scope, "headers": headers}, receive, send)

    return wrapped
