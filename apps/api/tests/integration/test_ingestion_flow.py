"""M04/M05 end-to-end over real Postgres and real files: round trip
with staged chunks and embeddings, hash-gate rerun, and the retry →
dead-letter ladder with an injected failing parser."""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import UUID

import pymupdf
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.application.ingestion import (
    DetectChanges,
    EmbedDocument,
    IngestDocument,
    ReindexSource,
)
from atlas.domain.knowledge.entities import Source
from atlas.domain.knowledge.parsing import ParsedDocument
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.watcher.filesystem import LocalFileStore
from tests.fakes import FakeDispatcher, FakeEmbeddingProvider

pytestmark = pytest.mark.integration


class ExplodingParser:
    """Injected failing parser (M04 integration-test requirement)."""

    name = "exploding"

    def supports(self, path: str) -> bool:
        return True

    def parse(self, raw: bytes, path: str) -> ParsedDocument:
        msg = "injected parser failure"
        raise RuntimeError(msg)


def _write_corpus(root: Path) -> None:
    (root / "notes").mkdir()
    (root / "notes" / "plan.md").write_text("# Plan\n\nShip M04.")
    (root / "readme.txt").write_text("plain text content")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "PDF fixture for ingestion.")
    pdf.save(root / "paper.pdf")


class Flow:
    def __init__(self, database_url: str, root: Path) -> None:
        self.engine = create_async_engine(database_url)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.root = root
        self.dispatcher = FakeDispatcher()
        self.provider = FakeEmbeddingProvider(dimensions=768)
        registry = default_registry()
        self.uow: Callable[[], SqlAlchemyUnitOfWork] = lambda: SqlAlchemyUnitOfWork(self.factory)
        self.detect = DetectChanges(
            uow_factory=self.uow,
            file_store=LocalFileStore(),
            dispatcher=self.dispatcher,
            supports=registry.supports,
        )
        self.ingest = IngestDocument(
            uow_factory=self.uow,
            file_store=LocalFileStore(),
            parser_for=registry.parser_for,
            dispatcher=self.dispatcher,
        )
        self.embed = EmbedDocument(uow_factory=self.uow, provider=self.provider)

    async def run_pipeline(self, workspace_id: UUID, job_id: UUID) -> str:
        outcome = await self.ingest.execute(workspace_id, job_id)
        if outcome.result == "chunked":
            outcome = await self.embed.execute(workspace_id, job_id)
        return outcome.result


@pytest.fixture
async def flow(migrated_database_url: str, tmp_path: Path) -> AsyncIterator[Flow]:
    _write_corpus(tmp_path)
    instance = Flow(migrated_database_url, tmp_path)
    yield instance
    await instance.engine.dispose()


async def test_round_trip_then_rerun_creates_zero_new_versions(flow: Flow) -> None:
    workspace_id = await ensure_default_workspace(flow.factory, name="Flow")
    source = Source(workspace_id=workspace_id, kind="folder", name="Corpus", uri=str(flow.root))
    async with flow.uow() as uow:
        await uow.sources.add(source)
        await uow.commit()

    report = await flow.detect.execute(workspace_id, source.id, batch_id="run-1")
    assert len(report.enqueued_job_ids) == 3  # md + txt + pdf

    for job_id in report.enqueued_job_ids:
        assert await flow.run_pipeline(workspace_id, job_id) == "succeeded"

    async with flow.uow() as uow:
        documents = await uow.documents.list_for_source(workspace_id, source.id)
        versions = [
            v
            for d in documents
            for v in await uow.document_versions.list_for_document(workspace_id, d.id)
        ]
        succeeded = await uow.ingestion_jobs.list_by_state(workspace_id, "succeeded")
        chunks = [
            c
            for d in documents
            if d.current_version_id is not None
            for c in await uow.chunks.list_for_version(workspace_id, d.current_version_id)
        ]
    assert sorted(d.path for d in documents) == ["notes/plan.md", "paper.pdf", "readme.txt"]
    assert len(versions) == 3
    assert all(d.current_version_id is not None for d in documents)
    assert len(succeeded) == 3
    assert all(job.stage == "index" for job in succeeded)

    # M05 acceptance: every chunk of a successfully ingested document
    # carries a 768-dimensional, non-null embedding.
    assert chunks
    assert all(c.embedding is not None and len(c.embedding) == 768 for c in chunks)
    assert all(c.embedding_model == "fake-embed" for c in chunks)

    # M04 acceptance: unchanged corpus re-run — zero new versions, all
    # skipped; M05: and ZERO embedding provider calls (cache criterion).
    calls_before_rerun = flow.provider.total_texts_embedded
    reindex = ReindexSource(detect_changes=flow.detect)
    rerun = await reindex.execute(workspace_id, source.id, batch_id="run-2")
    assert rerun.enqueued_job_ids == ()
    assert rerun.skipped_unchanged == 3
    assert flow.provider.total_texts_embedded == calls_before_rerun
    async with flow.uow() as uow:
        documents = await uow.documents.list_for_source(workspace_id, source.id)
        versions_after = [
            v
            for d in documents
            for v in await uow.document_versions.list_for_document(workspace_id, d.id)
        ]
        skipped = await uow.ingestion_jobs.list_by_state(workspace_id, "skipped")
    assert len(versions_after) == 3
    assert len(skipped) == 3
    assert all(job.trace_id == "run-2" for job in skipped)


async def test_injected_parser_failure_dead_letters_after_three_attempts(flow: Flow) -> None:
    workspace_id = await ensure_default_workspace(flow.factory, name="FlowFail")
    source = Source(workspace_id=workspace_id, kind="folder", name="Bad", uri=str(flow.root))
    async with flow.uow() as uow:
        await uow.sources.add(source)
        await uow.commit()

    report = await flow.detect.execute(workspace_id, source.id, batch_id="fail-run")
    target = report.enqueued_job_ids[0]

    failing = IngestDocument(
        uow_factory=flow.uow,
        file_store=LocalFileStore(),
        parser_for=lambda _path: ExplodingParser(),
        dispatcher=FakeDispatcher(),
    )
    results = [(await failing.execute(workspace_id, target)) for _ in range(3)]
    assert [r.result for r in results] == [
        "retry_scheduled",
        "retry_scheduled",
        "dead_lettered",
    ]
    assert [r.retry_delay_seconds for r in results] == [30, 120, None]

    # Queryable via state=failed (GET /jobs?state=failed backs onto this).
    async with flow.uow() as uow:
        failed = await uow.ingestion_jobs.list_by_state(workspace_id, "failed")
    assert [j.id for j in failed] == [target]
    assert failed[0].is_dead_lettered
    assert failed[0].error is not None
    assert "injected parser failure" in failed[0].error
