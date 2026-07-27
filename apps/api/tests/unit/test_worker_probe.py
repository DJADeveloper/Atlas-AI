"""The worker probe turns "why is this job pending?" into an answer.

A `pending` row looks the same whether a worker is busy or absent, so
the probe is the only thing that can tell them apart — including the
partial case that actually bites: a worker running without `-Q`, which
consumes the default queue and strands every embed job.
"""

from typing import Any

import pytest

from atlas.infrastructure.jobs.celery_app import CHAT_QUEUE, EMBED_QUEUE, INGEST_QUEUE
from atlas.infrastructure.jobs.probe import REQUIRED_QUEUES, CeleryWorkerProbe


class FakeInspector:
    def __init__(self, reply: object, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error

    def active_queues(self) -> object:
        if self._error is not None:
            raise self._error
        return self._reply


class FakeControl:
    def __init__(self, inspector: FakeInspector) -> None:
        self._inspector = inspector

    def inspect(self, timeout: float) -> FakeInspector:
        return self._inspector


class FakeCelery:
    def __init__(self, reply: object, error: Exception | None = None) -> None:
        self.control = FakeControl(FakeInspector(reply, error))


def probe_over(reply: object, error: Exception | None = None) -> CeleryWorkerProbe:
    celery: Any = FakeCelery(reply, error)
    return CeleryWorkerProbe(celery)


def queues(*names: str) -> list[dict[str, object]]:
    return [{"name": name} for name in names]


class TestWorkerProbe:
    async def test_a_fully_subscribed_worker_leaves_nothing_stranded(self) -> None:
        fleet = await probe_over(
            {"celery@host": queues(INGEST_QUEUE, EMBED_QUEUE, CHAT_QUEUE)}
        ).snapshot()

        assert fleet.online is True
        assert fleet.reachable is True
        assert fleet.unconsumed_queues == ()
        assert fleet.workers[0].name == "celery@host"

    async def test_no_worker_strands_every_queue(self) -> None:
        fleet = await probe_over(None).snapshot()

        assert fleet.online is False
        assert fleet.unconsumed_queues == REQUIRED_QUEUES

    async def test_worker_without_queue_flags_strands_the_embed_stage(self) -> None:
        """The real-world failure: `celery worker` with no -Q consumes
        only the default queue, so parse runs and embed never does."""
        fleet = await probe_over({"celery@host": queues(INGEST_QUEUE)}).snapshot()

        assert fleet.online is True
        assert set(fleet.unconsumed_queues) == {EMBED_QUEUE, CHAT_QUEUE}

    async def test_queues_are_pooled_across_workers(self) -> None:
        fleet = await probe_over(
            {
                "celery@a": queues(INGEST_QUEUE),
                "celery@b": queues(EMBED_QUEUE, CHAT_QUEUE),
            }
        ).snapshot()

        assert fleet.unconsumed_queues == ()
        assert [w.name for w in fleet.workers] == ["celery@a", "celery@b"]

    async def test_unreachable_broker_reports_state_instead_of_raising(self) -> None:
        fleet = await probe_over(None, error=OSError("redis is down")).snapshot()

        assert fleet.reachable is False
        assert fleet.online is False
        assert fleet.unconsumed_queues == REQUIRED_QUEUES

    @pytest.mark.parametrize("reply", ["nonsense", 42, {"celery@host": "not-a-list"}])
    async def test_malformed_replies_never_crash_the_probe(self, reply: object) -> None:
        fleet = await probe_over(reply).snapshot()

        assert fleet.unconsumed_queues == REQUIRED_QUEUES
