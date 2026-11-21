"""Offline checks of the verifier: no Cloudflare, no network.

Pins the property the gateway depends on: anything short of a valid token, for
this route's AUD, from this team, for an identity this route names, on this
route's hostname, is refused. Run from docker/gateway/verifier:

    python tests/smoke.py
"""

from __future__ import annotations

import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import verifier as v  # noqa: E402

TEAM = "example.cloudflareaccess.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ENV = {
    "GW_HOST_SELFTEST": "k7q2m9.example.com",
    "GW_AUD_SELFTEST": "aud-selftest",
    "GW_ALLOW_SELFTEST": "Me@Example.com, token:abc123.access",
    "GW_HOST_ALEXA": "p3x8n1.example.com",
    "GW_AUD_ALEXA": "aud-alexa",
    "GW_ALLOW_ALEXA": "token:lambda.access",
}


def token(key=KEY, **claims) -> str:
    now = int(time.time())
    body = {"type": "app", "aud": ["aud-selftest"], "iss": f"https://{TEAM}",
            "iat": now, "exp": now + 300, "email": "me@example.com", "sub": "u1"}
    body.update(claims)
    body = {k: val for k, val in body.items() if val is not None}
    return jwt.encode(body, key, algorithm="RS256")


def headers(tok: str | None = None, host: str = "k7q2m9.example.com") -> dict:
    h = {"x-forwarded-host": host}
    if tok is not None:
        h["cf-access-jwt-assertion"] = tok
    return h


VERIFIER = v.Verifier(TEAM, ENV, keys=lambda _t: KEY.public_key())
FAILED: list[str] = []


def refused(name: str, route: str, hdrs: dict, verifier: v.Verifier = VERIFIER) -> None:
    try:
        who = verifier.check(route, hdrs)
    except v.Refused:
        print(f"ok    refused: {name}")
        return
    FAILED.append(name)
    print(f"FAIL  {name}: passed as {who}")


def allowed(name: str, route: str, hdrs: dict, expect: str) -> None:
    try:
        who = VERIFIER.check(route, hdrs)
    except v.Refused as exc:
        FAILED.append(name)
        print(f"FAIL  {name}: refused ({exc})")
        return
    if who != expect:
        FAILED.append(name)
        print(f"FAIL  {name}: identity {who!r}, expected {expect!r}")
        return
    print(f"ok    allowed: {name}")


allowed("a person on their route", "selftest", headers(token()), "me@example.com")
allowed("host with a port", "selftest", headers(token(), host="k7q2m9.example.com:8080"), "me@example.com")
allowed("a service token on its route", "selftest",
        headers(token(email=None, sub="", common_name="abc123.access")), "token:abc123.access")

refused("no token", "selftest", headers())
refused("empty token", "selftest", headers(""))
refused("garbage token", "selftest", headers("not.a.jwt"))
refused("signed by another key", "selftest", headers(token(key=OTHER_KEY)))
refused("expired", "selftest", headers(token(exp=int(time.time()) - 120, iat=int(time.time()) - 600)))
refused("no exp", "selftest", headers(token(exp=None)))
refused("another app's AUD", "selftest", headers(token(aud=["aud-alexa"])))
refused("another team", "selftest", headers(token(iss="https://evil.cloudflareaccess.com")))
refused("org token, not app", "selftest", headers(token(type="org")))
refused("someone else's email", "selftest", headers(token(email="stranger@example.com")))
refused("no identity at all", "selftest", headers(token(email=None, sub="")))
refused("service token not on the list", "selftest",
        headers(token(email=None, sub="", common_name="other.access")))
refused("common_name with a user sub", "selftest",
        headers(token(email=None, sub="u1", common_name="abc123.access")))
refused("right token, wrong hostname", "selftest", headers(token(), host="p3x8n1.example.com"))
refused("no X-Forwarded-Host", "selftest", {"cf-access-jwt-assertion": token()})
refused("alexa's token on the selftest route", "selftest",
        headers(token(email=None, sub="", common_name="lambda.access")))
refused("selftest token on the alexa route", "alexa",
        headers(token(aud=["aud-alexa"]), host="p3x8n1.example.com"))
refused("unknown route", "calendar", headers(token()))
refused("route name with tricks", "../selftest", headers(token()))
refused("half-configured route", "selftest", headers(token()),
        v.Verifier(TEAM, {**ENV, "GW_ALLOW_SELFTEST": ""}, keys=lambda _t: KEY.public_key()))
refused("no team domain", "selftest", headers(token()), v.Verifier("", ENV))
refused("team domain that is not Access", "selftest", headers(token()),
        v.Verifier("evil.example.com", ENV, keys=lambda _t: KEY.public_key()))


def boom(_t):
    raise jwt.PyJWKClientError("could not fetch keys")


refused("signing keys unreachable", "selftest", headers(token()), v.Verifier(TEAM, ENV, keys=boom))


# --- over HTTP: the status codes Caddy acts on ---------------------------------
server = v.ThreadingHTTPServer(("127.0.0.1", 0), v.make_handler(VERIFIER))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"


def status(path: str, hdrs: dict) -> tuple[int, str]:
    req = urllib.request.Request(base + path, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("X-Gateway-Identity", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("X-Gateway-Identity", "")


for name, path, hdrs, want in (
    ("HTTP: valid → 204 + identity", "/verify/selftest",
     {"X-Forwarded-Host": "k7q2m9.example.com", "Cf-Access-Jwt-Assertion": token()}, (204, "me@example.com")),
    ("HTTP: no token → 403", "/verify/selftest", {"X-Forwarded-Host": "k7q2m9.example.com"}, (403, "")),
    ("HTTP: unknown path → 404", "/", {}, (404, "")),
    ("HTTP: health", "/healthz", {}, (204, "")),
):
    got = status(path, hdrs)
    if got == want:
        print(f"ok    {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}: got {got}, expected {want}")
server.shutdown()

if FAILED:
    print(f"\n{len(FAILED)} check(s) failed")
    sys.exit(1)
print("\nall verifier checks passed")
