"""A single-worker priority queue in front of Ollama.

Why this exists — it is the whole point of the stack:

This box is CPU-only, and the tuned `llama3.2:3b` pins `num_thread 4` out of the
LXC's 6-core quota (see AGENTS.md). One generation at a time uses that budget
exactly. Two concurrent generations don't run twice as fast — they contend for
the same cores and the same memory bandwidth, so *both* crawl, and the 2 cores
left for the monitoring/proxy stacks get eaten too. Ollama will happily accept
the parallel requests and thrash.

So every LLM call in this process — scheduled or interactive — funnels through
one worker. Requests wait in line instead of fighting. Because the delivery
medium is Discord (push, asynchronous), waiting in line is invisible: nobody is
watching a cursor blink.

Interactive work jumps ahead of scheduled work: if the morning digest is mid-run
when you type `/ask`, your question is next in line rather than behind a batch
job you didn't ask for.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

# Lower number = runs sooner.
PRIORITY_INTERACTIVE = 0
PRIORITY_SCHEDULED = 10


class QueueFull(Exception):
    """Raised when the backlog is at capacity — shed load instead of piling up."""


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    # Everything below is payload, excluded from ordering comparisons.
    label: str = field(compare=False)
    fn: Callable[[], Awaitable[Any]] = field(compare=False)
    future: asyncio.Future = field(compare=False)
    queued_at: float = field(compare=False, default_factory=time.monotonic)


class JobQueue:
    """Serializes async callables through exactly one worker task."""

    def __init__(self, max_size: int = 8) -> None:
        self._q: asyncio.PriorityQueue[_Item] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._max_size = max_size
        self._worker: asyncio.Task | None = None
        self._running: str | None = None

    # --- lifecycle ---

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="jobqueue-worker")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    # --- introspection (powers /status and the "you're Nth in line" message) ---

    @property
    def depth(self) -> int:
        return self._q.qsize()

    @property
    def running(self) -> str | None:
        return self._running

    # --- submission ---

    async def submit(
        self,
        label: str,
        fn: Callable[[], Awaitable[Any]],
        priority: int = PRIORITY_INTERACTIVE,
    ) -> Any:
        """Queue `fn`, wait for its turn, and return its result (or re-raise)."""
        if self._q.qsize() >= self._max_size:
            raise QueueFull(f"queue is full ({self._max_size} waiting)")

        loop = asyncio.get_running_loop()
        item = _Item(
            priority=priority,
            seq=next(self._seq),
            label=label,
            fn=fn,
            future=loop.create_future(),
        )
        await self._q.put(item)
        self.start()
        return await item.future

    # --- worker ---

    async def _run(self) -> None:
        while True:
            item = await self._q.get()
            waited = time.monotonic() - item.queued_at
            self._running = item.label
            started = time.monotonic()
            try:
                result = await item.fn()
            except asyncio.CancelledError:
                if not item.future.done():
                    item.future.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 — surfaced to the caller
                log.warning("job %s failed after %.1fs: %s", item.label, time.monotonic() - started, exc)
                if not item.future.done():
                    item.future.set_exception(exc)
            else:
                log.info(
                    "job %s done (waited %.1fs, ran %.1fs)",
                    item.label, waited, time.monotonic() - started,
                )
                if not item.future.done():
                    item.future.set_result(result)
            finally:
                self._running = None
                self._q.task_done()
