"""The digest's dead man's switch.

The digest is a scheduled job, and the rule this lab runs on is that anything
scheduled needs a switch — *including the scheduler*. A job that only speaks
when it has something to say cannot report never having run, and a digest that
silently stops arriving looks exactly like a quiet morning. `repo-sync.sh` was
silent for three weeks that way.

The container being gone is already covered: `heartbeat.sh` publishes
`homelab_container_running{required="yes"}` for every container declared in any
compose file, and `hl-container-missing` alerts on it. What is not covered is
the narrower case — bot up, healthy, gateway connected, and 07:30 passes with
nothing posted. `tasks.loop(time=...)` has no catch-up, so a bot that was down
at 07:30 and back by 07:35 simply skips that day and says nothing about it.

So the scheduled run pings a Healthchecks check. Silence is then the signal,
observed from outside the box, and it reaches the same `#alerts` channel as
everything else.

This is the Python half of `scripts/healthchecks.sh`, and it deliberately
repeats that file's rules rather than inventing its own, because the lesson
behind them was expensive:

  A switch is armed only if it can be shown to be armed. On 2026-09-19 the
  nightly pg_dumpall's switch held `https://hc-ping.com/your-uuid`, copied out
  of .env.example and never replaced. The variable was not empty so nothing
  warned; the ping was swallowed so the 404 went nowhere; and no check existed
  so nothing was ever late. The one job standing between this lab and an
  unrecoverable mistake had a switch that pinged into the void, and it was
  indistinguishable from one that worked.

Hence: a value that is not a plausible ping URL counts as UNSET and says why, a
failed ping is logged rather than swallowed, and `/status` reports the armed
state so the answer is one command away rather than a thing you assume.

What is NOT here, and why: the shell version also leaves
`homelab_healthchecks_ping_success` behind for `hl-hc-ping-failing` to alert
on. This container cannot — it runs as uid 10001 and the textfile directory is
root-owned on the host, so writing there would need a mount and a uid change
for a metric the external check already covers. The Healthchecks check going
late is the alarm for this one.

Ping URLs are masked in every message. They are capability URLs: anyone holding
one can check the job in, which is precisely how you would hide a box that had
stopped.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger(__name__)

# The placeholder .env.example ships, and the shapes a half-finished
# copy-paste leaves behind. Listed explicitly because every one of them is a
# syntactically fine URL that pings nothing at all. Kept in step with the
# `case` list in scripts/healthchecks.sh.
PLACEHOLDERS = ("your-uuid", "<", ">", "changeme", "replace", "example.com")


def mask(url: str) -> str:
    """Scheme, host, and up to eight characters of path — enough to tell two
    checks apart in a log without writing the credential into it."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(unparseable)"
    if not parts.scheme or not parts.netloc:
        return f"{url[:16]}…"
    return f"{parts.scheme}://{parts.netloc}{parts.path[:9]}…"


def read(raw: str | None, var: str = "HEALTHCHECKS_DIGEST_URL") -> tuple[str | None, str]:
    """Validate a ping URL. Returns (url, "") or (None, reason).

    Same three gates as `hc_url`, in the same order:
    empty, then shape, then placeholder.
    """
    url = (raw or "").strip().strip('"').strip("'")
    if not url:
        return None, f"{var} is unset — the digest has no dead man's switch"

    # A scheme, a host, and a path with something in it. `https://hc-ping.com`
    # on its own serves the front page and answers 200: a ping that succeeds
    # forever while arming nothing, which is worse than one that fails.
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, f"{var} is not a URL"
    if parts.scheme not in ("http", "https") or not parts.netloc or len(parts.path) < 2:
        return None, f"{var} is not a ping URL ({mask(url)})"

    lowered = url.lower()
    if any(p in lowered for p in PLACEHOLDERS):
        return None, (
            f"{var} still holds a placeholder ({mask(url)}) "
            "— paste the check's real ping URL"
        )

    return url, ""


class Switch:
    """A Healthchecks check, pinged after each scheduled digest.

    Every method degrades to a no-op and never raises. A switch that could take
    the digest down with it would be a worse bargain than no switch: the digest
    is the product, the ping is the proof it happened.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str | None,
        *,
        reason: str = "",
        attempts: int = 3,
        delay_s: float = 5.0,
        timeout_s: int = 10,
    ) -> None:
        self._session = session
        self.url = url
        self.reason = reason
        self._attempts = attempts
        self._delay = delay_s
        self._timeout = timeout_s

    @property
    def armed(self) -> bool:
        return bool(self.url)

    def describe(self) -> str:
        """One line for `/status`. The question "is it armed" should be
        answerable without reading .env on the box."""
        if self.url:
            return f"armed ({mask(self.url)})"
        return f"⚠️ NOT ARMED — {self.reason}"

    async def ok(self) -> bool:
        return await self._ping("")

    async def fail(self) -> bool:
        """Tell Healthchecks now rather than letting the grace period expire —
        a digest that raised is a failure we already know about."""
        return await self._ping("/fail")

    async def _ping(self, suffix: str) -> bool:
        if not self.url:
            return False
        target = self.url.rstrip("/") + suffix
        last: Exception | None = None
        # Retries ride out a brief uplink blip, so a flaky connection does not
        # page you. Bounded, because this runs inside the digest's task loop.
        for attempt in range(1, self._attempts + 1):
            try:
                async with self._session.get(
                    target, timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as resp:
                    resp.raise_for_status()
                    return True
            except Exception as exc:  # noqa: BLE001 — reported, never raised
                last = exc
                if attempt < self._attempts:
                    await asyncio.sleep(self._delay)
        # Reported, not swallowed: a switch that stops arming is the failure
        # this whole file exists to make visible.
        log.warning("healthchecks ping failed (%s): %s", mask(target), last)
        return False
