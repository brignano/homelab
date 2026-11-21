#!/usr/bin/env python3
"""CI's guard on the secure gateway's config. Fails the build when:

  - a gateway route reaches a denylisted upstream (budget, admin UIs, media,
    Matterbridge, Kali: decided in #360, never public);
  - anything in a route can answer or proxy before `forward_auth` has checked
    the caller (lock 3);
  - a route proxies without exact paths, a method, a body-size limit and the
    caller's credentials stripped;
  - a site address is anything but `http://{$GW_HOST_<ROUTE>:<route>.invalid}:8080`
    or the catch-all, so no hostname is ever committed;
  - an .env.example in docker/gateway or docker/tunnel holds a value;
  - cloudflared joins any network but its own and gateway_edge.

The route checks run on Caddy's own adapted JSON (`caddy adapt`), not on the
Caddyfile's text, so a snippet or a nested `route` cannot hide a handler.

    python3 docker/gateway/tools/check_gateway.py [--caddy /path/to/caddy] [--caddyfile F]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

import yaml  # PyYAML: in CI's runner image and in python3-yaml on the box

REPO = pathlib.Path(__file__).resolve().parents[3]
VERIFIER = "gateway-verifier:9000"

# Upstreams that never go through the gateway (TSD "Where it can and can't
# reach"). Matched against the dial's host part, and against container names,
# so `actual_server:5006` and `budget:5006` both fail.
DENY = re.compile(
    r"(budget|actual|portainer|grafana|prometheus|pve|proxmox|10\.0\.0\.200|adguard|jellyfin|"
    r"qbittorrent|gluetun|sonarr|radarr|prowlarr|cleanuparr|flaresolverr|matterbridge|kali|"
    r"sablier|postgres|caddy|webtop)",
    re.I,
)
SITE = re.compile(r"^http://\{\$GW_HOST_([A-Z][A-Z0-9_]*):([a-z][a-z0-9_]*)\.invalid\}:8080 \{$")
CATCH_ALL = "http://:8080 {"
STRIPPED = {"cf-access-jwt-assertion", "authorization", "cookie"}


def adapt(caddy: str, caddyfile: pathlib.Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GW_")}
    out = subprocess.run([caddy, "adapt", "--config", str(caddyfile), "--adapter", "caddyfile"],
                         capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise SystemExit(f"FAIL  caddy adapt: {out.stderr.strip()[:500]}")
    return json.loads(out.stdout)


def site_lines(text: str) -> list[str]:
    """Top-level site addresses: unindented lines opening a block, minus the
    global options block and snippets."""
    return [line.rstrip() for line in text.splitlines()
            if line.endswith("{") and not line.startswith(("\t", " ", "#", "(")) and line.strip() != "{"]


def is_verifier(h: dict) -> bool:
    if h.get("handler") != "reverse_proxy":
        return False
    return any(u.get("dial") == VERIFIER for u in h.get("upstreams", []))


def walk(handlers: list, ctx: dict, problems: list[str], site: str) -> None:
    """Visit handlers in the order Caddy runs them, carrying what has happened
    so far on this path: verified, matchers, body limit, headers stripped."""
    for h in handlers:
        kind = h.get("handler")
        if kind == "subroute":
            for r in h.get("routes", []):
                sub = dict(ctx)
                sub["matchers"] = ctx["matchers"] + r.get("match", [])
                walk(r.get("handle", []), sub, problems, site)
                # Handlers in an unmatched route still ran on the matched path
                # only; a sibling route starts from the parent's context.
                if not r.get("match"):
                    ctx.update({k: sub[k] for k in ("verified", "body", "stripped")})
            continue
        if is_verifier(h):
            ctx["verified"] = True
            want = f"/verify/{ctx['route']}"
            rewrite = (h.get("rewrite") or {}).get("uri", "")
            if rewrite != want:
                problems.append(f"{site}: forward_auth checks `{rewrite}`, expected `{want}`")
            continue
        if kind == "request_body":
            ctx["body"] = bool(h.get("max_size"))
            continue
        if kind == "headers":
            for name in (h.get("request") or {}).get("delete", []):
                ctx["stripped"] = ctx["stripped"] | {name.lower()}
            continue
        if kind in ("static_response", "reverse_proxy", "file_server", "templates"):
            status = int(str(h.get("status_code") or 200)) if kind == "static_response" else 200
            if not ctx["verified"] and not (kind == "static_response" and status >= 400):
                problems.append(f"{site}: a {kind} answers before forward_auth has checked the caller")
            if kind != "reverse_proxy":
                continue
            for u in h.get("upstreams", []):
                if DENY.search(u.get("dial", "")):
                    problems.append(f"{site}: upstream `{u.get('dial')}` is on the denylist")
            paths = [p for m in ctx["matchers"] for p in m.get("path", [])]
            methods = [x for m in ctx["matchers"] for x in m.get("method", [])]
            if not paths or any("*" in p for p in paths):
                problems.append(f"{site}: reverse_proxy without exact paths ({paths or 'none'})")
            if not methods:
                problems.append(f"{site}: reverse_proxy without a method matcher")
            if not ctx["body"]:
                problems.append(f"{site}: reverse_proxy without request_body max_size")
            missing = STRIPPED - ctx["stripped"]
            if missing:
                problems.append(f"{site}: caller credentials reach the upstream ({', '.join(sorted(missing))});"
                                " import strip_caller_credentials")


def check_caddy(config: dict, text: str) -> list[str]:
    problems: list[str] = []
    for line in site_lines(text):
        if line == CATCH_ALL:
            continue
        m = SITE.match(line)
        if not m:
            problems.append(f"site address `{line}` must be http://{{$GW_HOST_<ROUTE>:<route>.invalid}}:8080")
        elif m.group(1).lower() != m.group(2):
            problems.append(f"site `{line}`: GW_HOST_{m.group(1)} and `{m.group(2)}.invalid` name different routes")

    servers = config.get("apps", {}).get("http", {}).get("servers", {})
    if not servers:
        problems.append("no HTTP server in the adapted config")
    for srv in servers.values():
        if srv.get("listen") != [":8080"]:
            problems.append(f"gateway listens on {srv.get('listen')}, expected only :8080")
        # handle_errors blocks run after a refusal: they may only answer, never
        # proxy or serve files, or a 403 could become a way in.
        for r in srv.get("errors", {}).get("routes", []):
            for h in json.dumps(r.get("handle", [])).split('"handler": "')[1:]:
                kind = h.split('"', 1)[0]
                if kind in ("reverse_proxy", "file_server", "templates"):
                    problems.append(f"handle_errors uses {kind}; error routes may only respond")
        for r in srv.get("routes", []):
            hosts = [h for m in r.get("match", []) for h in m.get("host", [])]
            route = hosts[0].removesuffix(".invalid") if len(hosts) == 1 and hosts[0].endswith(".invalid") else ""
            site = f"route `{route}`" if route else "catch-all"
            if hosts and not route:
                problems.append(f"host matcher {hosts}: a committed hostname; use the .env placeholder")
                continue
            ctx = {"route": route, "verified": False, "matchers": list(r.get("match", [])),
                   "body": False, "stripped": frozenset()}
            walk(r.get("handle", []), ctx, problems, site)
    return problems


def check_env_examples() -> list[str]:
    problems = []
    for rel in ("docker/gateway/.env.example", "docker/tunnel/.env.example"):
        for n, line in enumerate((REPO / rel).read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            if value.strip():
                problems.append(f"{rel}:{n}: {key} has a value; examples stay empty (the repo is public)")
    return problems


def check_compose_hosts() -> list[str]:
    """Every GW_HOST_* the gateway gets defaults to <route>.invalid. Caddy's
    {$VAR:default} only covers an UNSET variable; compose's `:-` makes an empty
    one look unset to Caddy too. An empty host would be a second
    `http://:8080` and Caddy would refuse to start at all."""
    compose = yaml.safe_load((REPO / "docker/gateway/docker-compose.yml").read_text())
    problems = []
    env = compose["services"]["gateway"].get("environment", {})
    for key, value in env.items():
        if not key.startswith("GW_HOST_"):
            continue
        route = key[len("GW_HOST_"):].lower()
        if str(value) != f"${{{key}:-{route}.invalid}}":
            problems.append(f"docker/gateway/docker-compose.yml: gateway's {key} must be "
                            f"${{{key}:-{route}.invalid}}, not {value!r}")
    return problems


def check_tunnel() -> list[str]:
    compose = yaml.safe_load((REPO / "docker/tunnel/docker-compose.yml").read_text())
    nets = compose["services"]["cloudflared"].get("networks", [])
    nets = sorted(nets if isinstance(nets, list) else nets.keys())
    if nets != ["gateway_edge", "tunnel"]:
        return [f"cloudflared joins {nets}; it may join only its own network and gateway_edge"]
    if compose["networks"]["gateway_edge"].get("name") != "gateway_edge":
        return ["docker/tunnel's gateway_edge must be the external network named gateway_edge"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caddy", default="caddy")
    ap.add_argument("--caddyfile", default=str(REPO / "docker/gateway/Caddyfile"))
    ap.add_argument("--only-caddy", action="store_true", help="skip the repo-wide checks (tests)")
    args = ap.parse_args()
    path = pathlib.Path(args.caddyfile)
    problems = check_caddy(adapt(args.caddy, path), path.read_text())
    if not args.only_caddy:
        problems += check_env_examples() + check_compose_hosts() + check_tunnel()
    for p in problems:
        print(f"FAIL  {p}")
    if problems:
        return 1
    print("ok    gateway: every route verified first, no denylisted upstream, no committed hostname")
    return 0


if __name__ == "__main__":
    sys.exit(main())
