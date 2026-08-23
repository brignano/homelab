"""Deterministic homelab facts, collected from Prometheus and Loki.

The hard rule in this module: **Python decides what is true, the model only
describes it.** Every number below is fetched and thresholded by code. The model
never queries anything, never sees a raw log line, and never gets to decide
whether something is "fine" — it receives a finished facts block and writes two
sentences over it.

That constraint is what makes a 3B usable here. The previously shelved
tsd-ai-homelab-assistant.md concluded the same thing: the only version that
works on this hardware is one where the tool does all the work and the model
narrates. What changed since then is delivery — this arrives on its own
(docs/design/tsd-local-llm-discord-jobs.md), so a canned summary is worth having.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

log = logging.getLogger(__name__)

# --- Queries -----------------------------------------------------------------
# Kept as named constants so they're greppable and reviewable in one place.

Q_UP = "up"
Q_CPU = '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1h])) * 100)'
Q_MEM = "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"
Q_DISK = (
    '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"}'
    ' / node_filesystem_size_bytes{mountpoint="/"})'
)
# `changes()` over the container start timestamp counts restarts (cAdvisor).
Q_RESTARTS = 'sum by (name) (changes(container_start_time_seconds{name!=""}[24h]))'
# Loki metric query. `container` is the label Alloy sets from the Docker name
# (docker/monitoring/alloy/config.alloy).
Q_LOG_ERRORS = (
    'sum by (container) (count_over_time({job="docker"}'
    ' |~ "(?i)(error|fatal|panic)" [24h]))'
)

# Thresholds — the "is this fine?" judgement, made in code, not by the model.
CPU_WARN = 85.0
MEM_WARN = 90.0
DISK_WARN = 85.0


# --- Response parsing (pure; no I/O, unit-testable) ---------------------------

def parse_vector(payload: dict) -> list[tuple[dict, float]]:
    """Parse a Prometheus/Loki instant-query response into (labels, value) pairs.

    Both APIs return the same envelope shape, so one parser covers both. Samples
    whose value isn't a float (NaN, malformed) are dropped rather than raising —
    a single bad series shouldn't sink the whole digest.
    """
    if payload.get("status") != "success":
        raise ValueError(f"query failed: {payload.get('error') or payload.get('status')}")
    out: list[tuple[dict, float]] = []
    for sample in payload.get("data", {}).get("result", []):
        value = sample.get("value")
        if not value or len(value) < 2:
            continue
        try:
            out.append((sample.get("metric", {}), float(value[1])))
        except (TypeError, ValueError):
            continue
    return out


def first_scalar(payload: dict) -> float | None:
    """Single-value queries (CPU, RAM, disk) — take the first sample or None."""
    vec = parse_vector(payload)
    return vec[0][1] if vec else None


def target_name(labels: dict) -> str:
    """Human-readable identity for an `up` series."""
    job = labels.get("job") or "?"
    inst = labels.get("instance") or "?"
    return f"{job} ({inst})" if inst != "?" else job


def top_counts(vec: list[tuple[dict, float]], label: str, limit: int = 5) -> list[tuple[str, int]]:
    """Highest-count series first, dropping zeros. Used for restarts + log errors."""
    rows = [
        (labels.get(label) or "?", int(value))
        for labels, value in vec
        if value >= 1
    ]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:limit]


# --- Collected facts ----------------------------------------------------------

@dataclass
class Facts:
    collected_at: datetime
    targets_up: int = 0
    targets_total: int = 0
    targets_down: list[str] = field(default_factory=list)
    cpu_pct: float | None = None
    mem_pct: float | None = None
    disk_pct: float | None = None
    restarts: list[tuple[str, int]] = field(default_factory=list)
    log_errors: list[tuple[str, int]] = field(default_factory=list)
    # Collection failures — reported rather than silently rendered as "all good".
    problems: list[str] = field(default_factory=list)

    @property
    def concerns(self) -> list[str]:
        """Everything code considers worth flagging. The model does not add to this."""
        out: list[str] = []
        if self.targets_down:
            out.append(f"{len(self.targets_down)} monitored target(s) down: " + ", ".join(self.targets_down))
        if self.cpu_pct is not None and self.cpu_pct >= CPU_WARN:
            out.append(f"CPU averaged {self.cpu_pct:.0f}% over the last hour")
        if self.mem_pct is not None and self.mem_pct >= MEM_WARN:
            out.append(f"RAM is {self.mem_pct:.0f}% used")
        if self.disk_pct is not None and self.disk_pct >= DISK_WARN:
            out.append(f"root filesystem is {self.disk_pct:.0f}% full")
        if self.restarts:
            out.append(
                "container restarts in 24h: "
                + ", ".join(f"{n}×{c}" for n, c in self.restarts)
            )
        return out

    @property
    def all_clear(self) -> bool:
        return not self.concerns and not self.problems

    def compact(self) -> str:
        """One dense line of readings, for injecting into a chat prompt.

        Deliberately terser than the digest's block: this rides along on every
        conversational turn, and prompt evaluation is CPU-bound and roughly
        linear in tokens. Roughly 50-60 tokens against a 4096 window.

        Only facts appear here — no interpretation. The "needs attention" list
        is still computed in Python (see `concerns`) so the model is told the
        verdict rather than asked to reach one.
        """
        bits: list[str] = []
        if self.targets_total:
            bits.append(f"services {self.targets_up}/{self.targets_total} up")
        if self.targets_down:
            bits.append("DOWN: " + ", ".join(self.targets_down))
        for label, value in (("CPU", self.cpu_pct), ("RAM", self.mem_pct), ("disk /", self.disk_pct)):
            if value is not None:
                bits.append(f"{label} {value:.0f}%")
        bits.append(
            "restarts 24h: " + (", ".join(f"{n} x{c}" for n, c in self.restarts) if self.restarts else "none")
        )
        bits.append(
            "log errors 24h: " + (", ".join(f"{n} {c}" for n, c in self.log_errors) if self.log_errors else "none")
        )
        line = "; ".join(bits)
        if self.concerns:
            line += " | NEEDS ATTENTION: " + "; ".join(self.concerns)
        if self.problems:
            line += " | could not read: " + "; ".join(self.problems)
        return line


# --- Collection ---------------------------------------------------------------

class FactCollector:
    def __init__(self, session: aiohttp.ClientSession, prometheus_url: str, loki_url: str) -> None:
        self._session = session
        self._prom = prometheus_url
        self._loki = loki_url

    async def _query(self, base: str, path: str, query: str) -> dict:
        async with self._session.get(
            f"{base}{path}",
            params={"query": query},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            resp.raise_for_status()
            # Loki serves metric queries as application/json but has been known
            # to mislabel content types; don't let that raise.
            return await resp.json(content_type=None)

    async def collect(self, now: datetime) -> Facts:
        """Gather everything concurrently. These are cheap reads, not inference —
        they don't touch the LLM worker and don't need to be serialized."""
        facts = Facts(collected_at=now)

        async def prom(query: str) -> dict:
            return await self._query(self._prom, "/api/v1/query", query)

        async def loki(query: str) -> dict:
            return await self._query(self._loki, "/loki/api/v1/query", query)

        results = await asyncio.gather(
            prom(Q_UP), prom(Q_CPU), prom(Q_MEM), prom(Q_DISK),
            prom(Q_RESTARTS), loki(Q_LOG_ERRORS),
            return_exceptions=True,
        )
        up_r, cpu_r, mem_r, disk_r, restart_r, logs_r = results

        def unwrap(result, what: str):
            if isinstance(result, Exception):
                log.warning("collecting %s failed: %s", what, result)
                facts.problems.append(f"could not read {what}")
                return None
            return result

        if (payload := unwrap(up_r, "target health")) is not None:
            try:
                vec = parse_vector(payload)
                facts.targets_total = len(vec)
                facts.targets_up = sum(1 for _, v in vec if v == 1)
                facts.targets_down = sorted(target_name(m) for m, v in vec if v != 1)
            except ValueError as exc:
                facts.problems.append(f"could not read target health ({exc})")

        for payload, attr, what in (
            (unwrap(cpu_r, "CPU"), "cpu_pct", "CPU"),
            (unwrap(mem_r, "RAM"), "mem_pct", "RAM"),
            (unwrap(disk_r, "disk usage"), "disk_pct", "disk usage"),
        ):
            if payload is None:
                continue
            try:
                setattr(facts, attr, first_scalar(payload))
            except ValueError as exc:
                facts.problems.append(f"could not read {what} ({exc})")

        if (payload := unwrap(restart_r, "container restarts")) is not None:
            try:
                facts.restarts = top_counts(parse_vector(payload), "name")
            except ValueError as exc:
                facts.problems.append(f"could not read container restarts ({exc})")

        if (payload := unwrap(logs_r, "log errors")) is not None:
            try:
                facts.log_errors = top_counts(parse_vector(payload), "container")
            except ValueError as exc:
                facts.problems.append(f"could not read log errors ({exc})")

        return facts
