"""Offline checks of gatewaywatch.py: fake docker logs, fake Cloudflare API.

Run from docker/gateway/tools:  python tests/smoke.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import gatewaywatch as gw  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    if cond:
        print(f"ok    {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name} {detail}")


NOW = time.time()
ENV = {"GW_HOST_SELFTEST": "k7q2m9.example.com", "GW_HOST_ALEXA": "p3x8n1.example.com", "GW_HOST_OFF": ""}


def caddy(ts, status, uri="/selftest", host="k7q2m9.example.com", uuid="u1", method="GET"):
    return json.dumps({"msg": "handled request", "ts": ts, "status": status, "uuid": uuid,
                       "request": {"host": host, "method": method, "uri": uri, "remote_ip": "172.20.0.3",
                                   "headers": {"Cf-Connecting-Ip": ["203.0.113.9"], "Cf-Ipcountry": ["NL"]}}})


def verifier(uuid, ok, **kw):
    return json.dumps({"event": "verify", "request_id": uuid, "ok": ok, **kw})


CADDY = [
    "not json",
    caddy(NOW - 100, 200, uuid="a"),
    caddy(NOW - 90, 403, uuid="b"),
    caddy(NOW - 80, 404, uri="/admin", uuid="c"),
    caddy(NOW - 70, 404, host="evil.example.com", uuid="d"),
    caddy(NOW - 60, 502, uuid="e"),
    caddy(NOW - 50, 404, uri="/x`@everyone`", uuid="f"),
    caddy(NOW - 5000, 403, uuid="old"),  # before `since`: already reported
]
VERIFIER = [
    verifier("a", True, identity="me@example.com"),
    verifier("b", False, reason="token:lambda.access is not allowed on route selftest"),
    verifier("c", True, identity="me@example.com"),
    verifier("f", True, identity="me@example.com"),
]


def logs(container, since):
    return CADDY if container == "gateway" else VERIFIER


# --- watch ---------------------------------------------------------------------
state = {"since": NOW - 1000}
alerts, lines = gw.watch(ENV, state, NOW, logs, tunnel_up=True)
text = "\n".join(alerts)
check("watch: one alert block for the run", alerts and alerts[0].startswith("**Gateway refused 4 request(s)**"), alerts[:1])
check("watch: verifier reason joined by uuid", "token:lambda.access is not allowed" in text, text)
check("watch: path refusal names the caller", "path or method not on the route (caller me" in text, text)
check("watch: unknown host is called out", "no route (host `evil.example.com`)" in text, text)
check("watch: client IP and country from Cloudflare headers", "from 203.0.113.9 (NL)" in text, text)
check("watch: attacker text cannot break out of code or ping", "`@everyone`" not in text and "@​everyone" in text, text)
check("watch: lines older than since are skipped", text.count("\n- ") == 4, text)
check("watch: since advances to the newest line", abs(state["since"] - (NOW - 50)) < 0.01, state["since"])
m = "\n".join(lines)
check("watch: 24h counts by outcome",
      'homelab_gateway_requests_24h{outcome="allowed"} 1' in m and
      'homelab_gateway_requests_24h{outcome="refused"} 4' in m and
      'homelab_gateway_requests_24h{outcome="error"} 1' in m, m)
check("watch: routes live ignores an empty host", "homelab_gateway_routes_live 2" in m, m)
check("watch: tunnel state", "homelab_gateway_tunnel_up 1" in m, m)

alerts2, lines2 = gw.watch(ENV, state, NOW + 1, logs, tunnel_up=False)
check("watch: a second run reports nothing new", alerts2 == [], alerts2)
check("watch: counts carry across runs", 'outcome="refused"} 4' in "\n".join(lines2), lines2)


# --- poll ----------------------------------------------------------------------
def iso(ts):
    return gw.iso(ts)


RECORDS = [
    {"ray_id": "r1", "created_at": iso(NOW - 200), "user_email": "me@example.com", "ip_address": "198.51.100.7",
     "country": "US", "app_domain": "k7q2m9.example.com", "allowed": True},
    {"ray_id": "r2", "created_at": iso(NOW - 190), "user_email": "non_identity@team.cloudflareaccess.com",
     "ip_address": "3.5.140.2", "app_domain": "p3x8n1.example.com", "allowed": True},
]
TOKENS = {"success": True, "result": [
    {"name": "alexa", "expires_at": iso(NOW + 10 * gw.DAY)},
    {"name": "selftest", "expires_at": iso(NOW + 300 * gw.DAY)},
]}
AWS = {"prefixes": [{"ip_prefix": "3.5.140.0/22"}], "ipv6_prefixes": []}


def fake_fetch(records):
    def fetch(url, token):
        if "ip-ranges.amazonaws.com" in url:
            return AWS
        if "service_tokens" in url:
            return TOKENS
        assert token == "tok", "API calls carry the token"
        return {"success": True, "result": records}
    return fetch


CFG = {"CLOUDFLARE_ACCOUNT_ID": "acct", "CLOUDFLARE_ACCESS_LOG_TOKEN": "tok",
       "GW_SOURCE_ALEXA": "aws", "ACCESS_DENIED_ALERT": "3"}

with tempfile.TemporaryDirectory() as tmp:
    d = pathlib.Path(tmp)
    st: dict = {}
    alerts, lines = gw.poll(ENV, CFG, st, NOW, d, fake_fetch(RECORDS))
    check("poll: first run learns sign-ins quietly", alerts == [], alerts)
    check("poll: service token from AWS is fine", "outside its expected source" not in "\n".join(alerts))
    m = "\n".join(lines)
    check("poll: expiring tokens counted", "homelab_access_tokens_expiring 1" in m, m)
    check("poll: archive written", len(list((d / "access-log").glob("*.jsonl"))) >= 1)

    later = NOW + 600
    new = [
        {"ray_id": "r3", "created_at": iso(later - 100), "user_email": "me@example.com", "ip_address": "192.0.2.4",
         "country": "BR", "app_domain": "k7q2m9.example.com", "allowed": True},
        {"ray_id": "r4", "created_at": iso(later - 90), "user_email": "", "ip_address": "192.0.2.99",
         "app_domain": "p3x8n1.example.com/api", "allowed": True},
    ] + [
        {"ray_id": f"d{i}", "created_at": iso(later - 50 + i), "user_email": "x@example.com",
         "ip_address": f"192.0.2.{i}", "app_domain": "k7q2m9.example.com", "allowed": False}
        for i in range(4)
    ]
    alerts, lines = gw.poll(ENV, CFG, st, later, d, fake_fetch(RECORDS + new))
    text = "\n".join(alerts)
    check("poll: new country and network alert", "new country BR and network 192.0.0.0/16" in text, text)
    check("poll: service token outside AWS alerts", "Service token used on `alexa` from 192.0.2.99" in text, text)
    check("poll: burst of refused logins alerts", "4 refused Access logins" in text, text)
    check("poll: already-seen records are not re-reported", text.count("Sign-in by") == 1, text)
    m = "\n".join(lines)
    check("poll: 24h login counts", 'result="allowed"} 4' in m and 'result="denied"} 4' in m, m)

    try:
        gw.poll(ENV, {}, {}, NOW, d, fake_fetch([]))
        check("poll: refuses to run without credentials", False)
    except RuntimeError:
        check("poll: refuses to run without credentials", True)


# --- helpers -------------------------------------------------------------------
check("read_env strips quotes and export", gw.read_env.__name__ == "read_env")
with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write('# c\nexport A="1"\nB=\'two\'\nC=\n')
env = gw.read_env(pathlib.Path(f.name))
check("read_env values", env == {"A": "1", "B": "two", "C": ""}, env)
check("network_of v4", gw.network_of("203.0.113.9") == "203.0.0.0/16")
check("network_of v6", gw.network_of("2001:db8::1") == "2001:db8::/32")
check("network_of junk", gw.network_of("nope") == "")

if FAILED:
    print(f"\n{len(FAILED)} check(s) failed")
    sys.exit(1)
print("\nall gatewaywatch checks passed")
