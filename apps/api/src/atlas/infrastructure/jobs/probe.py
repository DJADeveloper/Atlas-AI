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

    @property
    def online(self) -> bool:
        return len(self.workers) > 0

    @property
    def consumed_queues(self) -> frozenset[str]:
        return frozenset(queue for worker in self.workers for queue in worker.queues)

    @property
    def unconsumed_queues(self) -> tuple[str, ...]:
        """Required queues nobody is listening to — jobs there strand."""
        if not self.online:
            return REQUIRED_QUEUES
        consumed = self.consumed_queues
        return tuple(queue for queue in REQUIRED_QUEUES if queue not in consumed)


class CeleryWorkerProbe:
    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    async def snapshot(self, timeout_seconds: float = 1.0) -> WorkerFleet:
        try:
            async with asyncio.timeout(timeout_seconds + 0.5):
                replies = await asyncio.to_thread(self._active_queues, timeout_seconds)
        # A probe converts every failure mode into a state; nothing escapes.
        except Exception:
            _logger.warning("worker probe: control channel unreachable", exc_info=True)
            return WorkerFleet(workers=(), reachable=False)

        if not isinstance(replies, dict):  # None: no worker answered in time
            return WorkerFleet(workers=())
        return WorkerFleet(workers=_snapshots(replies))

    def _active_queues(self, timeout_seconds: float) -> object:
        """The raw control-channel reply; Celery types it loosely."""
        inspector = self._celery.control.inspect(timeout=timeout_seconds)
        return inspector.active_queues()


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
