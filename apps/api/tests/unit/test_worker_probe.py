"""The worker probe turns "why is this job pending?" into an answer.

A `pending` row looks the same whether a worker is busy or absent, so
the probe is the only thing that can tell them apart — including the
partial case that actually bites: a worker running without `-Q`, which
consumes the default queue and strands every embed job.
"""

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

import pytest

from atlas.infrastructure.jobs.celery_app import CHAT_QUEUE, EMBED_QUEUE, INGEST_QUEUE
from atlas.infrastructure.jobs.probe import REQUIRED_QUEUES, CeleryWorkerProbe


class FakeInspector:
    def __init__(self, owner: "FakeCelery") -> None:
        self._owner = owner

    def active_queues(self) -> object:
        self._owner.calls += 1
        if self._owner.block is not None:
            self._owner.block.wait(timeout=5)
        if self._owner.error is not None:
            raise self._owner.error
        return self._owner.reply


class FakeControl:
    def __init__(self, owner: "FakeCelery") -> None:
        self._owner = owner

    def inspect(self, timeout: float, connection: object = None) -> FakeInspector:
        return FakeInspector(self._owner)


class FakeConnection:
    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeCelery:
    def __init__(self, reply: object, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls = 0
        self.block: threading.Event | None = None
        self.control = FakeControl(self)

    def connection_or_acquire(self) -> FakeConnection:
        return FakeConnection()


@dataclass
class Rig:
    celery: FakeCelery
    probe: CeleryWorkerProbe


def rig_over(
    reply: object,
    error: Exception | None = None,
    ttl_seconds: float = 20.0,
    timeout_seconds: float = 1.0,
) -> Rig:
    fake = FakeCelery(reply, error)
    celery: Any = fake
    return Rig(
        celery=fake,
        probe=CeleryWorkerProbe(celery, ttl_seconds=ttl_seconds, timeout_seconds=timeout_seconds),
    )


def probe_over(reply: object, error: Exception | None = None) -> CeleryWorkerProbe:
    return rig_over(reply, error).probe


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


class TestProbeIsCheap:
    """The control call is synchronous, borrows a broker connection, and
    can outlive its timeout. Polled from a page, an unguarded probe
    leaks a thread and a connection per request until the API stops
    answering — these are the guards against that."""

    async def test_repeat_calls_within_the_ttl_reuse_one_answer(self) -> None:
        rig = rig_over({"celery@host": queues(INGEST_QUEUE)})

        for _ in range(25):
            await rig.probe.snapshot()

        assert rig.celery.calls == 1

    async def test_the_answer_refreshes_once_the_ttl_lapses(self) -> None:
        rig = rig_over({"celery@host": queues(INGEST_QUEUE)}, ttl_seconds=0.0)

        await rig.probe.snapshot()
        await rig.probe.snapshot()

        assert rig.celery.calls == 2

    async def test_concurrent_callers_never_stack_control_calls(self) -> None:
        rig = rig_over({"celery@host": queues(INGEST_QUEUE)}, ttl_seconds=0.0)
        rig.celery.block = threading.Event()

        try:
            waiting = [asyncio.create_task(rig.probe.snapshot()) for _ in range(10)]
            await asyncio.sleep(0.05)  # let them all reach the probe
            rig.celery.block.set()
            await asyncio.gather(*waiting)
        finally:
            rig.celery.block.set()

        assert rig.celery.calls == 1, "a slow control channel must cost one call, not ten"

    async def test_a_hung_call_is_not_joined_by_another(self) -> None:
        """The timeout cancels the await, never the thread. A second
        probe would strand a second thread, so it must not start."""
        rig = rig_over({"celery@host": queues(INGEST_QUEUE)}, ttl_seconds=0.0, timeout_seconds=0.0)
        rig.celery.block = threading.Event()

        try:
            first = await rig.probe.snapshot()  # times out, thread lingers
            second = await rig.probe.snapshot()

            assert first.reachable is False
            assert second.known is False or second.reachable is False
            assert rig.celery.calls == 1
        finally:
            rig.celery.block.set()

    async def test_unknown_is_never_reported_as_nothing_running(self) -> None:
        """Before the first answer lands there is no claim to make —
        an unchecked fleet must not accuse the user of a dead worker."""
        rig = rig_over({"celery@host": queues(INGEST_QUEUE)}, ttl_seconds=0.0, timeout_seconds=0.0)
        rig.celery.block = threading.Event()

        try:
            await rig.probe.snapshot()  # first call times out
            fleet = await rig.probe.snapshot()  # served while still busy

            assert fleet.unconsumed_queues == () or fleet.reachable is False
        finally:
            rig.celery.block.set()
