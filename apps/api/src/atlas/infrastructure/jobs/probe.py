"""Is anything actually consuming the queues?

A job sits in `pending` for exactly one reason: no worker subscribed to
its queue has taken it. That is invisible from the database — the row
looks identical whether a worker is chewing through a backlog or no
worker exists at all — so the fleet has to be asked directly.

Celery's control channel is a synchronous broker round-trip, so every
call is pushed to a thread and bounded by a timeout: a probe reports a
state, it never hangs the request that asked.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass

from celery import Celery

from atlas.infrastructure.jobs.celery_app import CHAT_QUEUE, EMBED_QUEUE, INGEST_QUEUE

_logger = logging.getLogger(__name__)

# Every queue Atlas dispatches to. A worker that misses one strands that
# stage forever, which is the failure this probe exists to name.
REQUIRED_QUEUES: tuple[str, ...] = (INGEST_QUEUE, EMBED_QUEUE, CHAT_QUEUE)


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    name: str
    queues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkerFleet:
    """Who is listening, and to what."""

    workers: tuple[WorkerSnapshot, ...]
    reachable: bool = True
    # False until the fleet has actually been asked. "Not checked yet"
    # must never be presented as "nothing is running".
    known: bool = True

    @property
    def online(self) -> bool:
        return len(self.workers) > 0

    @property
    def consumed_queues(self) -> frozenset[str]:
        return frozenset(queue for worker in self.workers for queue in worker.queues)

    @property
    def unconsumed_queues(self) -> tuple[str, ...]:
        """Required queues nobody is listening to — jobs there strand."""
        if not self.known:
            return ()  # nothing to claim before the first answer
        if not self.online:
            return REQUIRED_QUEUES
        consumed = self.consumed_queues
        return tuple(queue for queue in REQUIRED_QUEUES if queue not in consumed)


UNKNOWN_FLEET = WorkerFleet(workers=(), known=False)


class CeleryWorkerProbe:
    """Cached, single-flight view of the worker fleet.

    The control call is synchronous and can outlast any timeout around
    it: cancelling the await does not cancel the thread, and each call
    borrows a broker connection. Polled from a page, that leaks threads
    and connections until nothing answers at all — so the probe is
    deliberately stingy. At most one call is ever in flight, a result is
    reused for `ttl_seconds`, and callers are served the last known
    answer (or "unknown") rather than queueing behind a slow one.
    """

    def __init__(
        self,
        celery: Celery,
        *,
        ttl_seconds: float = 20.0,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._celery = celery
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._cached: WorkerFleet | None = None
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()
        # Incremented before a call and decremented *by the worker
        # thread itself*, so a call that outlived its timeout still
        # counts as outstanding and cannot be joined by a second one.
        self._outstanding = 0
        self._counter_guard = threading.Lock()

    async def snapshot(self) -> WorkerFleet:
        if self._fresh():
            return self._served()
        # Never stack probes: a slow control channel must cost one
        # stalled thread in total, not one per request.
        if self._lock.locked() or self._busy():
            return self._served()
        async with self._lock:
            if self._fresh():  # refreshed while we waited for the lock
                return self._served()
            self._cached = await self._probe()
            self._fetched_at = time.monotonic()
            return self._cached

    def _fresh(self) -> bool:
        return self._fetched_at is not None and time.monotonic() - self._fetched_at < self._ttl

    def _served(self) -> WorkerFleet:
        return self._cached if self._cached is not None else UNKNOWN_FLEET

    def _busy(self) -> bool:
        with self._counter_guard:
            return self._outstanding > 0

    async def _probe(self) -> WorkerFleet:
        try:
            async with asyncio.timeout(self._timeout + 1.0):
                replies = await asyncio.to_thread(self._active_queues)
        # A probe converts every failure mode into a state; nothing escapes.
        except Exception:
            _logger.warning("worker probe: control channel did not answer", exc_info=True)
            return WorkerFleet(workers=(), reachable=False)

        if not isinstance(replies, dict):  # None: no worker answered in time
            return WorkerFleet(workers=())
        return WorkerFleet(workers=_snapshots(replies))

    def _active_queues(self) -> object:
        """The raw control-channel reply; Celery types it loosely."""
        with self._counter_guard:
            self._outstanding += 1
        try:
            # An explicit pooled connection, returned on exit: the
            # implicit one is acquired per call and is the thing that
            # accumulates under polling.
            with self._celery.connection_or_acquire() as connection:
                inspector = self._celery.control.inspect(
                    timeout=self._timeout, connection=connection
                )
                return inspector.active_queues()
        finally:
            with self._counter_guard:
                self._outstanding -= 1


def _snapshots(replies: dict[object, object]) -> tuple[WorkerSnapshot, ...]:
    """Normalize the control reply, which is external input like any other."""
    return tuple(
        WorkerSnapshot(name=str(name), queues=_queue_names(declared))
        for name, declared in sorted(replies.items(), key=lambda item: str(item[0]))
    )


def _queue_names(declared: object) -> tuple[str, ...]:
    if not isinstance(declared, list):
        return ()
    names = []
    for queue in declared:
        if isinstance(queue, dict) and "name" in queue:
            names.append(str(queue["name"]))
    return tuple(names)
