"""Server health from Prometheus, for "how's the server doing?". Read-only.

The same readings #chat used to get (docker/assistant/app/facts.py), plus two
this box has its own rules about: the thin pool, whose free space the guest
cannot see (AGENTS.md → Disk), and containers that heartbeat.sh says should be
running and are not.

Only instant queries (GET /api/v1/query), only the fixed queries below. The
model cannot send PromQL: it calls the tool with no arguments. Prometheus is
reached through the port it publishes on the host, so the concierge joins no
monitoring network.
"""

from __future__ import annotations

import asyncio

import aiohttp

Q_UP = "up"
Q_CPU = '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1h])) * 100)'
Q_MEM = "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"
Q_DISK = ('100 * (1 - node_filesystem_avail_bytes{mountpoint="/"}'
          ' / node_filesystem_size_bytes{mountpoint="/"})')
Q_THINPOOL = ('100 * pve_disk_usage_bytes{id=~"storage/.+/local-lvm"}'
              ' / pve_disk_size_bytes{id=~"storage/.+/local-lvm"}')
Q_RESTARTS = 'sum by (name) (changes(container_start_time_seconds{name!=""}[24h])) > 0'
Q_MISSING = 'homelab_container_running{required="yes"} == 0'
# Whether heartbeat.sh's inventory exists at all. Without it, an empty
# Q_MISSING would read as "nothing is down" when nothing is known.
Q_INVENTORY = "count(homelab_container_running)"

QUERIES = {"up": Q_UP, "cpu": Q_CPU, "mem": Q_MEM, "disk": Q_DISK, "thinpool": Q_THINPOOL,
           "restarts": Q_RESTARTS, "missing": Q_MISSING, "inventory": Q_INVENTORY}
TIMEOUT_S = 15
MAX_ITEMS = 10


class StatusError(RuntimeError):
    """Prometheus could not be read."""


def vector(payload: object) -> list[tuple[dict, float]]:
    """An instant-query reply as (labels, value) pairs. Pure; tested."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError(str(payload.get("error") if isinstance(payload, dict) else "bad reply"))
    out = []
    for item in (payload.get("data") or {}).get("result") or []:
        try:
            out.append((item.get("metric") or {}, float(item["value"][1])))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def _name(labels: dict) -> str:
    return str(labels.get("container") or labels.get("name") or labels.get("service")
               or labels.get("job") or labels.get("instance") or "?")[:60]


def summarize(results: dict[str, object]) -> dict:
    """Readings from each query's reply (or the exception it raised). Pure; tested.

    An empty result where a number belongs is reported as unread, never left
    out: a renamed metric must not read as "all clear" (AGENTS.md → Disk says
    the same of the thin-pool alert).
    """
    out: dict = {}
    unread = []

    def get(key: str):
        value = results.get(key)
        if isinstance(value, Exception) or value is None:
            unread.append(key)
            return None
        try:
            return vector(value)
        except ValueError:
            unread.append(key)
            return None

    if (v := get("up")) is not None:
        out["targets_up"] = f"{sum(1 for _, x in v if x == 1)}/{len(v)}"
        down = sorted({_name(m) for m, x in v if x != 1})
        if down:
            out["targets_down"] = down[:MAX_ITEMS]
    for key, label in (("cpu", "cpu_pct_1h"), ("mem", "ram_pct"), ("disk", "disk_root_pct"),
                       ("thinpool", "thinpool_pct")):
        if (v := get(key)) is not None:
            if v:
                out[label] = round(v[0][1], 1)
            else:
                unread.append(key)
    if (v := get("restarts")) is not None:
        out["restarts_24h"] = {_name(m): int(x) for m, x in sorted(v, key=lambda p: -p[1])[:MAX_ITEMS]}
    inventory = get("inventory")
    missing = get("missing")
    if inventory is not None and not inventory:
        unread.append("inventory")
    if inventory and missing is not None:
        out["required_containers_not_running"] = sorted({_name(m) for m, _ in missing})[:MAX_ITEMS]
    if unread:
        out["could_not_read"] = unread
    return out


class Homelab:
    def __init__(self, session: aiohttp.ClientSession | None, prometheus_url: str) -> None:
        self._session = session
        self._url = prometheus_url.rstrip("/") + "/api/v1/query"

    async def _query(self, query: str) -> object:
        if self._session is None:
            raise StatusError("no HTTP session")
        async with self._session.get(self._url, params={"query": query},
                                     timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def status(self) -> dict:
        keys = list(QUERIES)
        replies = await asyncio.gather(*(self._query(QUERIES[k]) for k in keys), return_exceptions=True)
        results = dict(zip(keys, replies))
        if all(isinstance(r, Exception) for r in replies):
            first = replies[0]
            raise StatusError(f"could not reach Prometheus ({first.__class__.__name__})")
        return summarize(results)
