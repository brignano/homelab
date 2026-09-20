"""When the last scheduled digest actually posted.

The digest pushes, so nobody checks it — which means the failure that matters
is not forgetting to look, it is nothing arriving and that looking identical to
a quiet morning. `tasks.loop(time=...)` has no catch-up, so a bot that was down
at DIGEST_AT and back five minutes later skips that day in silence.

A dead container is already covered (`homelab_container_running` →
`hl-container-missing`). This closes the narrower case: bot up, healthy,
connected, and the schedule simply not firing.

Why a file rather than a Healthchecks ping
------------------------------------------
Because the box-is-down case is already watched from outside, by
`scripts/heartbeat.sh`. That is the one thing nothing on CT 100 can report
about itself, and it has its own external switch. What is left is narrow enough
to watch from inside the lab: the bot writes a timestamp, `heartbeat.sh` reads
it into `homelab_digest_timestamp_seconds`, and `hl-digest-missing` alerts
through the same `#alerts` path as everything else. No fourth external check to
create, and nothing that can sit unarmed while looking configured.

The file lives on a host bind mount, not in the container's own /tmp, for two
reasons. It survives `up -d --build` — otherwise every rebuild would erase the
history and silence the alert for a day. And `heartbeat.sh` can read it while
the container is **stopped**, which is exactly when the alert should still be
able to fire.

Both functions degrade to a no-op. Writing this must never be able to fail the
digest: the digest is the product, the timestamp is only proof it happened.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def write(path: str, when: float) -> bool:
    """Record a successful scheduled digest. Never raises."""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write-and-rename, so a reader never sees a half-written file. The
        # reader here is a cron job running every five minutes, so the window
        # is small but it is not zero.
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".digest-")
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{int(when)}\n")
        os.replace(tmp, target)
        return True
    except OSError as exc:
        # The usual cause is the mount missing or owned by the wrong uid — this
        # container runs as 10001. Say so; do not take the digest down for it.
        log.warning("could not record the digest timestamp at %s: %s", path, exc)
        return False


def read(path: str) -> int | None:
    """The recorded time, or None if there isn't one worth trusting.

    A missing file is a legitimate answer — a fresh install has never posted a
    digest — and must not be confused with a broken one. Garbage is treated the
    same way rather than published as a number: a metric with a nonsense value
    is worse than no metric, because something downstream will subtract it from
    `time()` and alert on the result.
    """
    try:
        raw = Path(path).read_text().strip()
    except OSError:
        return None
    try:
        value = int(raw)
    except ValueError:
        log.warning("digest timestamp at %s is not a number: %r", path, raw[:32])
        return None
    return value if value > 0 else None


def clear(path: str) -> None:
    """Forget the last digest. Used when the schedule is turned off: with no
    digests expected, there is nothing to be stale about, and an alert that
    fires forever because a feature is deliberately disabled is noise that
    teaches you to ignore the channel."""
    try:
        Path(path).unlink()
    except OSError:
        pass
