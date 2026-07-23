"""Worker attempt shell: trace binding and outcome mapping (no broker).

Tests are synchronous on purpose: `execute_ingestion_attempt` owns its
event loop via asyncio.run, exactly as it does inside a Celery worker.
"""

import asyncio
import io
import json

from sqlalchemy.ext.asyncio import create_async_engine

from atlas.application.ingestion import DetectChanges, IngestDocument
from atlas.config.settings import Settings
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.jobs.runtime import WorkerRuntime, set_runtime
from atlas.infrastructure.jobs.worker import execute_ingestion_attempt
from atlas.infrastructure.parsing import default_registry
from atlas.observability.logging import configure_logging
from atlas.shared.ids import uuid7
from tests.fakes import FakeDispatcher, FakeFileStore, FakeState, FakeUnitOfWork

URI = "/notes"

_CLOSED_PORT_SETTINGS = Settings(
    database_url="postgresql+asyncpg://atlas:atlas@127.0.0.1:9/atlas",
    redis_url="redis://127.0.0.1:9/0",
)


def _fake_runtime(state: FakeState, files: FakeFileStore) -> WorkerRuntime:
    return WorkerRuntime(
        settings=_CLOSED_PORT_SETTINGS,
        engine=create_async_engine(_CLOSED_PORT_SETTINGS.database_url),  # lazy, no I/O
        ingest_document=IngestDocument(
            uow_factory=lambda: FakeUnitOfWork(state),
            file_store=files,
            parser_for=default_registry().parser_for,
        ),
    )


async def _prepare(state: FakeState, files: FakeFileStore) -> tuple[str, str]:
    workspace_id = uuid7()
    source = Source(workspace_id=workspace_id, kind="folder", name="N", uri=URI)
    state.sources[source.id] = source
    detect = DetectChanges(
        uow_factory=lambda: FakeUnitOfWork(state),
        file_store=files,
        dispatcher=FakeDispatcher(),
        supports=default_registry().supports,
    )
    report = await detect.execute(workspace_id, source.id, batch_id="trace-batch-7")
    return str(workspace_id), str(report.enqueued_job_ids[0])


def test_attempt_logs_carry_the_batch_trace_id() -> None:
    """M04 requirement: the API batch's trace id flows into worker logs."""
    state = FakeState()
    files = FakeFileStore({URI: {"a.md": b"# Hello"}})
    workspace_id, job_id = asyncio.run(_prepare(state, files))

    set_runtime(_fake_runtime(state, files))
    try:
        buffer = io.StringIO()
        configure_logging("INFO", stream=buffer)
        outcome = execute_ingestion_attempt(workspace_id, job_id, "trace-batch-7")
    finally:
        set_runtime(None)

    assert outcome.result == "succeeded"
    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    finished = [e for e in events if e["event"] == "ingestion.attempt_finished"]
    assert len(finished) == 1
    assert finished[0]["trace_id"] == "trace-batch-7"
    assert finished[0]["result"] == "succeeded"


def test_attempt_maps_retry_outcome_with_delay() -> None:
    state = FakeState()
    files = FakeFileStore({URI: {"bad.md": b"   "}})  # empty_document
    workspace_id, job_id = asyncio.run(_prepare(state, files))

    set_runtime(_fake_runtime(state, files))
    try:
        outcome = execute_ingestion_attempt(workspace_id, job_id, None)
    finally:
        set_runtime(None)
    assert outcome.result == "retry_scheduled"
    assert outcome.retry_delay_seconds == 30
