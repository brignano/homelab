#!/usr/bin/env python3
"""What the gateway let in, what it refused, and who signed in to Access.

Two jobs, both run from cron every five minutes by thin shell wrappers that own
the lock, the Healthchecks ping, the Discord post and the textfile metrics:

  watch   scripts/gateway-watch.sh   reads the gateway's and the verifier's JSON
          logs (`docker logs`), and reports every request the gateway refused.
          Locks 1 and 2 (Access, cloudflared) should stop everything first, so a
          refusal here means something got further than it should have.

  poll    scripts/access-log-poll.sh  reads Cloudflare Access's authentication
          log through the API (read-only token), keeps 90 days of it on the box
          (the free plan keeps 24 hours), and reports a sign-in from a country
          or network not seen before, a service token used from outside its
          expected source, and a burst of refused logins. It also counts
          service tokens that expire within 30 days, for the digest.

Both write alerts (one per line, Markdown) to --alerts and Prometheus text
lines to --metrics, and keep their state in --state. Standard library only,
so it runs on the host's python3 with `-I`. Design:
docs/design/tsd-secure-gateway.md, "Seeing attacks". Runbook:
docs/runbook-gateway.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable

HOUR = 3600
DAY = 24 * HOUR
KEEP_DAYS = 90
MAX_ALERT_LINES = 10
# A refusal is a 4xx the gateway produced. 5xx is an upstream fault, counted
# but not an attack.
REFUSED = range(400, 500)
NON_IDENTITY = re.compile(r"^non_identity@", re.I)
AWS_RANGES_URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"
API = "https://api.cloudflare.com/client/v4"


# --- small helpers -------------------------------------------------------------

def read_env(path: pathlib.Path) -> dict[str, str]:
    """KEY=value lines, quotes stripped. Never sourced: these files hold secrets."""
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        out[key] = value.strip().strip('"').strip("'")
    return out


def routes(env: dict[str, str]) -> dict[str, str]:
    """{hostname: route} for every route whose GW_HOST_<ROUTE> is set."""
    out = {}
    for key, value in env.items():
        if key.startswith("GW_HOST_") and value.strip():
            out[value.strip().lower().rstrip(".")] = key[len("GW_HOST_"):].lower()
    return out


def safe(text: object, limit: int = 120) -> str:
    """Attacker-controlled text, made inert for a Discord embed."""
    s = "".join(c if c.isprintable() else "?" for c in str(text or ""))
    s = s.replace("`", "'").replace("@", "@\u200b")  # zero-width space: no mention
    return s if len(s) <= limit else s[: limit - 1] + "…"


def load_state(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_state(path: pathlib.Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def bump(buckets: dict, now: float, key: str, n: int = 1) -> None:
    """Hourly counters, so "last 24h" survives between runs without a database."""
    hour = str(int(now // HOUR * HOUR))
    buckets.setdefault(hour, {})
    buckets[hour][key] = buckets[hour].get(key, 0) + n


def last_24h(buckets: dict, now: float) -> dict[str, int]:
    cutoff = now - DAY
    for hour in [h for h in buckets if int(h) < cutoff - HOUR]:
        del buckets[hour]
    total: dict[str, int] = {}
    for hour, counts in buckets.items():
        if int(hour) + HOUR > cutoff:
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
    return total


def metric(lines: list[str], name: str, kind: str, help_: str, samples: Iterable[tuple[str, float]]) -> None:
    lines.append(f"# HELP {name} {help_}")
    lines.append(f"# TYPE {name} {kind}")
    for labels, value in samples:
        lines.append(f"{name}{labels} {int(value) if float(value).is_integer() else value}")


# --- watch: the gateway's own logs -------------------------------------------------

def docker_logs(container: str, since: float) -> list[str]:
    out = subprocess.run(
        ["docker", "logs", "--since", f"{since:.3f}", container],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"docker logs {container}: {out.stderr.strip()[:200]}")
    # Caddy writes its log to stdout; Python tracebacks go to stderr.
    return (out.stdout + "\n" + out.stderr).splitlines()


def parse_json_lines(lines: Iterable[str]) -> list[dict]:
    out = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def watch(env: dict[str, str], state: dict, now: float,
          logs: Callable[[str, float], list[str]], tunnel_up: bool | None) -> tuple[list[str], list[str]]:
    hosts = routes(env)
    since = float(state.get("since") or now - 600)
    caddy = [r for r in parse_json_lines(logs("gateway", since))
             if r.get("msg") == "handled request" and float(r.get("ts", 0)) > since]
    verdicts = {r.get("request_id"): r for r in parse_json_lines(logs("gateway-verifier", since))
                if r.get("event") == "verify" and r.get("request_id")}

    buckets = state.setdefault("buckets", {})
    refused: list[str] = []
    newest = since
    for r in sorted(caddy, key=lambda r: float(r.get("ts", 0))):
        ts = float(r["ts"])
        newest = max(newest, ts)
        status = int(r.get("status") or 0)
        if status in REFUSED:
            outcome = "refused"
        elif status >= 500:
            outcome = "error"
        else:
            outcome = "allowed"
        bump(buckets, ts, outcome)
        if outcome != "refused":
            continue
        req = r.get("request") or {}
        hdrs = req.get("headers") or {}
        host = str(req.get("host") or "").lower().rsplit(":", 1)[0]
        route = hosts.get(host)
        where = f"`{route}`" if route else f"no route (host `{safe(host, 60)}`)"
        ip = (hdrs.get("Cf-Connecting-Ip") or [req.get("remote_ip", "?")])[0]
        country = (hdrs.get("Cf-Ipcountry") or [""])[0]
        v = verdicts.get(r.get("uuid") or r.get("request_id"))
        if v is None:
            why = "no route for this host" if not route else "refused before the verifier"
        elif not v.get("ok"):
            why = v.get("reason", "refused by the verifier")
        else:
            why = f"path or method not on the route (caller {v.get('identity', '?')})"
        refused.append(
            f"{where} {status} {safe(req.get('method'), 10)} `{safe(req.get('uri'))}` "
            f"from {safe(ip, 45)}{f' ({safe(country, 4)})' if country else ''}: {safe(why, 160)}"
        )

    state["since"] = newest
    alerts = []
    if refused:
        head = f"**Gateway refused {len(refused)} request(s)** in the last run. Locks 1 and 2 should have stopped these first."
        alerts.append(head)
        alerts += [f"- {line}" for line in refused[:MAX_ALERT_LINES]]
        if len(refused) > MAX_ALERT_LINES:
            alerts.append(f"- …and {len(refused) - MAX_ALERT_LINES} more (`docker logs gateway`)")

    day = last_24h(buckets, now)
    lines: list[str] = []
    metric(lines, "homelab_gateway_requests_24h", "gauge",
           "Requests the gateway handled in the last 24h, by outcome",
           [(f'{{outcome="{o}"}}', day.get(o, 0)) for o in ("allowed", "refused", "error")])
    metric(lines, "homelab_gateway_routes_live", "gauge",
           "Gateway routes with a hostname set in docker/gateway/.env", [("", len(hosts))])
    if tunnel_up is not None:
        metric(lines, "homelab_gateway_tunnel_up", "gauge",
               "1 if cloudflared is running (0 after scripts/gateway-off.sh)", [("", int(tunnel_up))])
    metric(lines, "homelab_gateway_watch_last_run_seconds", "gauge",
           "When gateway-watch.sh last read the gateway's logs", [("", int(now))])
    return alerts, lines


# --- poll: Cloudflare Access's authentication log ----------------------------------

def http_json(url: str, token: str = "", timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "homelab-gateway-watch"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> float:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def network_of(ip: str) -> str:
    """A rough "network": /16 for IPv4, /32 for IPv6. Coarse on purpose, so a
    phone moving between towers is not a new network every hour."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    prefix = 16 if addr.version == 4 else 32
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def source_nets(spec: str, aws: Callable[[], list[str]]) -> list:
    """GW_SOURCE_<ROUTE>: `aws`, and/or CIDRs, comma-separated."""
    nets = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if part.lower() == "aws":
            nets += [ipaddress.ip_network(c) for c in aws()]
        else:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def aws_ranges(cache: pathlib.Path, now: float, fetch: Callable[[str], dict]) -> list[str]:
    """AWS's published ranges, refreshed daily, cached so an outage of that URL
    does not turn into an alert storm."""
    try:
        if now - cache.stat().st_mtime < DAY:
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    try:
        data = fetch(AWS_RANGES_URL)
        cidrs = [p["ip_prefix"] for p in data.get("prefixes", [])] + \
                [p["ipv6_prefix"] for p in data.get("ipv6_prefixes", [])]
        if cidrs:
            cache.write_text(json.dumps(cidrs))
            return cidrs
    except Exception as exc:  # network, JSON: fall back to the cache below
        print(f"access-log-poll: could not refresh AWS ranges: {exc}", file=sys.stderr)
    try:
        return json.loads(cache.read_text())
    except (OSError, ValueError):
        raise RuntimeError("AWS ranges unavailable and never cached")


def fetch_access_log(account: str, token: str, since: float, until: float,
                     fetch: Callable[[str, str], dict]) -> list[dict]:
    """Every record in [since, until], oldest first, across pages."""
    out: list[dict] = []
    cursor = since
    for _ in range(20):
        q = urllib.parse.urlencode({"since": iso(cursor), "until": iso(until),
                                    "direction": "asc", "limit": 1000})
        data = fetch(f"{API}/accounts/{account}/access/logs/access_requests?{q}", token)
        if not data.get("success", False):
            raise RuntimeError(f"Access log API: {str(data.get('errors'))[:200]}")
        batch = data.get("result") or []
        out += batch
        if len(batch) < 1000:
            break
        cursor = max(parse_ts(r.get("created_at", "")) for r in batch)
    return out


def archive(records: list[dict], root: pathlib.Path, now: float) -> int:
    """Append to one JSONL file per UTC day; drop days past KEEP_DAYS. Returns days kept."""
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    by_day: dict[str, list[dict]] = {}
    for r in records:
        day = str(r.get("created_at", ""))[:10] or iso(now)[:10]
        by_day.setdefault(day, []).append(r)
    for day, rows in by_day.items():
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        with open(root / f"{day}.jsonl", "a") as f:
            for row in rows:
                f.write(json.dumps(row, sort_keys=True) + "\n")
    cutoff = iso(now - KEEP_DAYS * DAY)[:10]
    kept = 0
    for p in root.glob("*.jsonl"):
        if p.stem < cutoff:
            p.unlink()
        else:
            kept += 1
    return kept


def poll(env: dict[str, str], cfg: dict[str, str], state: dict, now: float, state_dir: pathlib.Path,
         fetch: Callable[[str, str], dict]) -> tuple[list[str], list[str]]:
    account = cfg.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = cfg.get("CLOUDFLARE_ACCESS_LOG_TOKEN", "").strip()
    if not (account and token):
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_ACCESS_LOG_TOKEN must both be set")
    threshold = int(cfg.get("ACCESS_DENIED_ALERT", "10") or 10)
    hosts = routes(env)
    learning = "known" not in state  # first run: learn who signs in from where, quietly

    # Overlap the last window by ten minutes: the log is eventually consistent,
    # and `seen` drops what was already handled.
    since = float(state.get("since") or now - DAY + 60) - 600
    records = fetch_access_log(account, token, max(since, now - DAY + 60), now, fetch)
    seen = state.setdefault("seen", {})
    fresh = []
    for r in records:
        key = f"{r.get('ray_id', '')}|{r.get('created_at', '')}|{r.get('user_email', '')}|{r.get('allowed')}"
        if key in seen:
            continue
        seen[key] = now
        fresh.append(r)
    for key in [k for k, t in seen.items() if t < now - 2 * HOUR]:
        del seen[key]
    if records:
        state["since"] = max(parse_ts(r.get("created_at", "")) for r in records)
    else:
        state["since"] = now - 600

    days_kept = archive(fresh, state_dir / "access-log", now)

    known = state.setdefault("known", {})
    buckets = state.setdefault("buckets", {})
    alerts: list[str] = []
    denied: list[dict] = []
    aws_cache = state_dir / "aws-ranges.json"
    nets_by_route: dict[str, list] = {}

    for r in fresh:
        ts = parse_ts(r.get("created_at", "")) or now
        email = str(r.get("user_email") or "").lower()
        ip = str(r.get("ip_address") or "")
        country = str(r.get("country") or "").upper()
        app = str(r.get("app_domain") or "").lower().split("/", 1)[0]
        route = hosts.get(app, "")
        where = f"`{route}`" if route else f"`{safe(app, 60)}`"
        if not r.get("allowed"):
            bump(buckets, ts, "denied")
            denied.append(r)
            continue
        bump(buckets, ts, "allowed")

        if email and not NON_IDENTITY.match(email):
            seen_for = known.setdefault(email, {"countries": [], "networks": []})
            new = []
            if country and country not in seen_for["countries"]:
                seen_for["countries"].append(country)
                new.append(f"country {safe(country, 4)}")
            net = network_of(ip)
            if net and net not in seen_for["networks"]:
                seen_for["networks"].append(net)
                seen_for["networks"] = seen_for["networks"][-200:]
                new.append(f"network {net}")
            if new and not learning:
                alerts.append(f"- Sign-in by {safe(email)} to {where} from a new {' and '.join(new)} ({safe(ip, 45)}).")
            continue

        # A service token (Access logs it as non-identity). Only routes with a
        # GW_SOURCE_<ROUTE> are checked: that is where the expected source is known.
        spec = cfg.get(f"GW_SOURCE_{route.upper()}", "") if route else ""
        if not spec:
            continue
        if route not in nets_by_route:
            try:
                nets_by_route[route] = source_nets(spec, lambda: aws_ranges(aws_cache, now, lambda u: fetch(u, "")))
            except Exception as exc:
                alerts.append(f"- Could not check where {where}'s service token came from: {safe(exc, 160)}")
                nets_by_route[route] = []
                continue
        nets = nets_by_route[route]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            addr = None
        if nets and (addr is None or not any(addr in n for n in nets)):
            alerts.append(f"- Service token used on {where} from {safe(ip, 45)}"
                          f"{f' ({safe(country, 4)})' if country else ''}, outside its expected source ({safe(spec, 60)}).")

    if len(denied) >= threshold:
        ips = sorted({str(d.get("ip_address") or "?") for d in denied})
        alerts.append(f"- {len(denied)} refused Access logins in five minutes, from {len(ips)} address(es): "
                      + ", ".join(safe(i, 45) for i in ips[:5]) + ("…" if len(ips) > 5 else ""))

    if alerts:
        alerts.insert(0, "**Cloudflare Access** needs a look:")

    # Service tokens expiring soon. Needs "Access: Service Tokens Read" on the
    # same API token; without it the digest just leaves this number out.
    expiring = None
    try:
        data = fetch(f"{API}/accounts/{account}/access/service_tokens?per_page=100", token)
        if data.get("success"):
            expiring = sum(1 for t in data.get("result") or []
                           if 0 < parse_ts(t.get("expires_at", "")) - now < 30 * DAY)
    except Exception as exc:
        print(f"access-log-poll: could not list service tokens: {exc}", file=sys.stderr)

    day = last_24h(buckets, now)
    lines: list[str] = []
    metric(lines, "homelab_access_logins_24h", "gauge",
           "Cloudflare Access authentication events in the last 24h, by result",
           [(f'{{result="{k}"}}', day.get(k, 0)) for k in ("allowed", "denied")])
    metric(lines, "homelab_access_log_days_kept", "gauge",
           "Days of Access authentication log kept on the box (target 90)", [("", days_kept)])
    if expiring is not None:
        metric(lines, "homelab_access_tokens_expiring", "gauge",
               "Access service tokens that expire within 30 days", [("", expiring)])
    metric(lines, "homelab_access_log_poll_last_success_seconds", "gauge",
           "When access-log-poll.sh last read the Access log", [("", int(now))])
    return alerts, lines


# --- CLI ------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("job", choices=["watch", "poll"])
    ap.add_argument("--env", required=True, help="docker/gateway/.env")
    ap.add_argument("--poll-env", help="poll only: the file holding the API token")
    ap.add_argument("--state", required=True, help="state directory")
    ap.add_argument("--alerts", required=True)
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--tunnel-up", choices=["0", "1"])
    args = ap.parse_args(argv)

    now = time.time()
    state_dir = pathlib.Path(args.state)
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    state_file = state_dir / f"{args.job}-state.json"
    state = load_state(state_file) or {}
    env = read_env(pathlib.Path(args.env))

    try:
        if args.job == "watch":
            alerts, lines = watch(env, state, now, docker_logs,
                                  None if args.tunnel_up is None else args.tunnel_up == "1")
        else:
            cfg = read_env(pathlib.Path(args.poll_env or ""))
            alerts, lines = poll(env, cfg, state, now, state_dir, http_json)
    except (RuntimeError, OSError, ValueError, urllib.error.URLError, subprocess.SubprocessError) as exc:
        # State is not saved, so the next run covers this window again.
        print(f"{args.job}: {exc}", file=sys.stderr)
        return 1

    save_state(state_file, state)
    pathlib.Path(args.alerts).write_text("\n".join(alerts) + ("\n" if alerts else ""))
    pathlib.Path(args.metrics).write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
