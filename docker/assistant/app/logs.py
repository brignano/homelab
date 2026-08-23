"""Log-line collection and pattern grouping for the weekly digest.

The daily digest reports *counts* — "grafana 432". That tells you something is
noisy but nothing about what it is, and after a week of seeing it you stop
looking. The weekly pass exists to answer the next question: 432 of *what*?

Almost always the answer is "5 distinct messages, repeated". Grouping them is
the whole value here, and it's plain string work — no model involved. What the
model eventually receives is a handful of deduplicated patterns with counts,
which is a summarisation task a 3B is actually good at, rather than a wall of
raw log lines it would have to reason over.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import aiohttp

# --- Pattern normalisation ---------------------------------------------------
# Applied in order; each replaces a class of per-occurrence noise with a
# placeholder so that otherwise-identical messages collapse together. Order
# matters: timestamps and UUIDs must go before the generic hex and number rules,
# or those would chew them up first and produce mush.
_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "<HEX>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|us|ns|kb|mb|gb|b)?\b", re.I), "<N>"),
)
_WHITESPACE = re.compile(r"\s+")

# Long enough to keep a message distinguishable, short enough that a stack trace
# or a dumped payload doesn't become its own unique "pattern" every time.
PATTERN_KEY_CHARS = 180
SAMPLE_CHARS = 220


def normalise(line: str) -> str:
    """Reduce a log line to a grouping key by stripping per-occurrence detail."""
    out = line
    for pattern, placeholder in _SUBSTITUTIONS:
        out = pattern.sub(placeholder, out)
    return _WHITESPACE.sub(" ", out).strip()[:PATTERN_KEY_CHARS]


def truncate(line: str, limit: int = SAMPLE_CHARS) -> str:
    """Shorten a sample line for display, marking that it was cut."""
    line = _WHITESPACE.sub(" ", line).strip()
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


@dataclass
class Pattern:
    """One deduplicated message shape, with how often it occurred."""
    key: str
    count: int
    sample: str      # first line seen with this shape, verbatim (truncated)


def group(lines: list[str], limit: int = 5) -> tuple[list[Pattern], int]:
    """Collapse raw lines into distinct patterns, most frequent first.

    Returns the top `limit` patterns and the total number of distinct patterns
    found, so the caller can say "showing 5 of 23" rather than implying it
    showed everything.
    """
    counts: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for line in lines:
        if not line or not line.strip():
            continue
        key = normalise(line)
        counts[key] += 1
        samples.setdefault(key, truncate(line))

    ordered = [
        Pattern(key=key, count=count, sample=samples[key])
        # Sort by count desc, then key for a stable order between runs.
        for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return ordered[:limit], len(ordered)


# --- Collection ---------------------------------------------------------------

ERROR_MATCH = '|~ "(?i)(error|fatal|panic)"'


def count_query(window: str) -> str:
    """Error-line counts per container over `window` (e.g. "7d")."""
    return f'sum by (container) (count_over_time({{job="docker"}} {ERROR_MATCH} [{window}]))'


def lines_query(container: str) -> str:
    """Raw matching lines for one container.

    The container name is inserted into a LogQL label matcher, so it is escaped
    and length-capped. It only ever comes from a Loki label we just read back,
    never from user input, but building a query by string concatenation is worth
    being careful with regardless.
    """
    safe = re.sub(r'[^A-Za-z0-9_.\-]', "", container)[:64]
    return f'{{job="docker", container="{safe}"}} {ERROR_MATCH}'


@dataclass
class ContainerLogs:
    container: str
    total: int                              # matching lines this period
    previous: int | None = None             # same window, one period earlier
    patterns: list[Pattern] = field(default_factory=list)
    distinct: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def delta(self) -> int | None:
        """Change vs the previous period. None when there's nothing to compare."""
        return None if self.previous is None else self.total - self.previous

    @property
    def is_new(self) -> bool:
        """Silent last period, noisy now — the highest-signal case."""
        return self.previous == 0 and self.total > 0


class LogCollector:
    def __init__(self, session: aiohttp.ClientSession, loki_url: str) -> None:
        self._session = session
        self._loki = loki_url.rstrip("/")

    async def _get(self, path: str, params: dict) -> dict:
        async with self._session.get(
            f"{self._loki}{path}", params=params, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def counts(self, window: str, offset_s: int = 0) -> dict[str, int]:
        """Per-container error counts. `offset_s` shifts the evaluation back in
        time, which is how the previous period is measured."""
        params: dict[str, str] = {"query": count_query(window)}
        if offset_s:
            import time
            params["time"] = str(int(time.time()) - offset_s)
        payload = await self._get("/loki/api/v1/query", params)
        from .facts import parse_vector
        return {
            (labels.get("container") or "?"): int(value)
            for labels, value in parse_vector(payload)
            if value >= 1
        }

    async def lines(self, container: str, start_s: int, end_s: int, limit: int) -> list[str]:
        """Raw matching lines for one container in a time range."""
        payload = await self._get("/loki/api/v1/query_range", {
            "query": lines_query(container),
            "start": str(start_s * 1_000_000_000),   # Loki wants nanoseconds
            "end": str(end_s * 1_000_000_000),
            "limit": str(limit),
            "direction": "backward",
        })
        return extract_lines(payload)


def extract_lines(payload: dict) -> list[str]:
    """Pull log lines out of a Loki query_range streams response.

    Malformed entries are skipped rather than raising — one bad line should not
    cost the whole report.
    """
    if payload.get("status") != "success":
        raise ValueError(f"loki query failed: {payload.get('error') or payload.get('status')}")
    out: list[str] = []
    for stream in payload.get("data", {}).get("result", []):
        for entry in stream.get("values", []):
            if isinstance(entry, (list, tuple)) and len(entry) >= 2 and isinstance(entry[1], str):
                out.append(entry[1])
    return out
