"""Worker attempt shell: trace binding and outcome mapping (no broker).

Tests are synchronous on purpose: `execute_ingestion_attempt` owns its
event loop via asyncio.run, exactly as it does inside a Celery worker.
"""

import asyncio
import io
import json

from sqlalchemy.ext.asyncio import create_async_engine

from atlas.ai import BreakerBoard, CostMeter, ModelRouter, ResilientExecutor
from atlas.ai.context import ContextAssembler
from atlas.application.chat import ChatRuntime, GenerateTitle, SummarizeConversation
from atlas.application.ingestion import DetectChanges, EmbedDocument, IngestDocument
from atlas.config.settings import Settings
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.jobs.runtime import WorkerRuntime, set_runtime
from atlas.infrastructure.jobs.worker import (
    execute_embed_attempt,
    execute_ingestion_attempt,
)
from atlas.infrastructure.parsing import default_registry
from atlas.observability.logging import configure_logging
from atlas.shared.ids import uuid7
from tests.fakes import (
    FakeDispatcher,
    FakeEmbeddingProvider,
    FakeFileStore,
    FakeState,
    FakeUnitOfWork,
)

URI = "/notes"

_CLOSED_PORT_SETTINGS = Settings(
    database_url="postgresql+asyncpg://atlas:atlas@127.0.0.1:9/atlas",
    redis_url="redis://127.0.0.1:9/0",
)


async def _never_sleep(_seconds: float) -> None:
    return None


def _chat_runtime() -> ChatRuntime:
    """Inert chat runtime: these tests exercise ingest tasks only, so
    the executor holds no providers and would fail closed if dialed."""
    return ChatRuntime(
        router=ModelRouter(),
        executor=ResilientExecutor({}, BreakerBoard(lambda: 0.0), sleep=_never_sleep),
        assembler=ContextAssembler(),
        cost_meter=CostMeter(),
        profile="local_only",
    )


def _fake_runtime(state: FakeState, files: FakeFileStore) -> WorkerRuntime:
    def uow_factory() -> FakeUnitOfWork:
        return FakeUnitOfWork(state)

    return WorkerRuntime(
        settings=_CLOSED_PORT_SETTINGS,
        engine=create_async_engine(_CLOSED_PORT_SETTINGS.database_url),  # lazy, no I/O
        ingest_document=IngestDocument(
            uow_factory=uow_factory,
            file_store=files,
            parser_for=default_registry().parser_for,
            dispatcher=FakeDispatcher(),
        ),
        embed_document=EmbedDocument(uow_factory=uow_factory, provider=FakeEmbeddingProvider()),
        summarize_conversation=SummarizeConversation(
            uow_factory=uow_factory, runtime=_chat_runtime()
        ),
        title_conversation=GenerateTitle(uow_factory=uow_factory, runtime=_chat_runtime()),
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

    assert outcome.result == "chunked"  # parse task hands off to the embed queue
    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    finished = [e for e in events if e["event"] == "ingestion.attempt_finished"]
    assert len(finished) == 1
    assert finished[0]["trace_id"] == "trace-batch-7"
    assert finished[0]["result"] == "chunked"


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


def test_embed_attempt_completes_the_pipeline() -> None:
    """The two worker shells drive one job to success: parse hands off
    ('chunked'), embed finishes ('succeeded') with stage-tagged logs."""
    state = FakeState()
    files = FakeFileStore({URI: {"a.md": b"# Hello\n\nWorld body."}})
    workspace_id, job_id = asyncio.run(_prepare(state, files))

    set_runtime(_fake_runtime(state, files))
    try:
        buffer = io.StringIO()
        configure_logging("INFO", stream=buffer)
        parse_outcome = execute_ingestion_attempt(workspace_id, job_id, "trace-embed-1")
        embed_outcome = execute_embed_attempt(workspace_id, job_id, "trace-embed-1")
    finally:
        set_runtime(None)

    assert parse_outcome.result == "chunked"
    assert embed_outcome.result == "succeeded"
    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    finished = [e for e in events if e["event"] == "ingestion.attempt_finished"]
    assert [e["stage"] for e in finished] == ["parse", "embed"]
    assert all(e["trace_id"] == "trace-embed-1" for e in finished)
