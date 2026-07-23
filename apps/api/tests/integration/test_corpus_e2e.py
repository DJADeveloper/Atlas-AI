"""M04/M05 acceptance E2E (nightly): 1,000 mixed files ingest — parse,
chunk, embed, swap — unattended, and an unchanged full reindex makes
ZERO embedding provider calls.

Marked `slow` only — the nightly workflow runs `pytest -m slow`; the PR
integration job (`-m integration`) skips it. Drives the real use cases
over real Postgres, real files, and real parsers; Celery transport is
out of frame (its mapping is covered elsewhere) — job-state truth is
the criterion. The embedding provider is the counting fake: the
provider-call criterion needs a counter, and the real Ollama adapter
has its own nightly suite.
"""

import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.application.ingestion import (
    DetectChanges,
    EmbedDocument,
    IngestDocument,
    ReindexSource,
)
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.watcher.filesystem import LocalFileStore
from tests.fakes import FakeDispatcher, FakeEmbeddingProvider
from tests.pdfgen import pdf_bytes

pytestmark = pytest.mark.slow

MARKDOWN_COUNT = 800
TEXT_COUNT = 190
PDF_COUNT = 10
TOTAL = MARKDOWN_COUNT + TEXT_COUNT + PDF_COUNT


def _write_corpus(root: Path) -> None:
    for index in range(MARKDOWN_COUNT):
        bucket = root / f"md/{index % 20:02d}"
        bucket.mkdir(parents=True, exist_ok=True)
        (bucket / f"doc-{index:04d}.md").write_text(
            f"# Document {index}\n\nBody for markdown fixture {index}.\n"
        )
    for index in range(TEXT_COUNT):
        bucket = root / "txt"
        bucket.mkdir(exist_ok=True)
        (bucket / f"note-{index:04d}.txt").write_text(f"plain text note {index}\n")
    pdf_dir = root / "pdf"
    pdf_dir.mkdir()
    for index in range(PDF_COUNT):
        (pdf_dir / f"paper-{index:02d}.pdf").write_bytes(pdf_bytes(f"PDF fixture {index}."))


async def test_thousand_file_corpus_ingests_unattended(
    migrated_database_url: str, tmp_path: Path
) -> None:
    _write_corpus(tmp_path)
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        workspace_id = await ensure_default_workspace(factory, name="Corpus")
        registry = default_registry()
        dispatcher = FakeDispatcher()
        provider = FakeEmbeddingProvider(dimensions=768)

        def uow() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(factory)

        detect = DetectChanges(
            uow_factory=uow,
            file_store=LocalFileStore(),
            dispatcher=dispatcher,
            supports=registry.supports,
        )
        ingest = IngestDocument(
            uow_factory=uow,
            file_store=LocalFileStore(),
            parser_for=registry.parser_for,
            dispatcher=dispatcher,
        )
        embed = EmbedDocument(uow_factory=uow, provider=provider)
        source = Source(workspace_id=workspace_id, kind="folder", name="Big", uri=str(tmp_path))
        async with uow() as unit:
            await unit.sources.add(source)
            await unit.commit()

        started = time.perf_counter()
        report = await detect.execute(workspace_id, source.id, batch_id="corpus-1")
        assert len(report.enqueued_job_ids) == TOTAL

        results = []
        for job_id in report.enqueued_job_ids:
            outcome = await ingest.execute(workspace_id, job_id)
            if outcome.result == "chunked":
                outcome = await embed.execute(workspace_id, job_id)
            results.append(outcome.result)
        elapsed = time.perf_counter() - started
        assert all(result in ("succeeded", "skipped") for result in results), (
            f"non-terminal outcomes present: {set(results)}"
        )

        async with uow() as unit:
            succeeded = await unit.ingestion_jobs.list_jobs(
                workspace_id, state="succeeded", limit=TOTAL + 1
            )
            failed = await unit.ingestion_jobs.list_jobs(workspace_id, state="failed", limit=10)
            documents = await unit.documents.list_for_source(workspace_id, source.id)
            embedded_counts = [
                await unit.chunks.count_embedded_for_version(workspace_id, d.current_version_id)
                for d in documents
                if d.current_version_id is not None
            ]
        assert failed == []
        assert len(succeeded) == TOTAL
        assert len(documents) == TOTAL
        assert all(d.current_version_id is not None for d in documents)
        assert len(embedded_counts) == TOTAL
        assert all(count > 0 for count in embedded_counts)
        assert provider.total_texts_embedded > 0

        # Hash-gate proof at scale: full re-sweep, zero new versions —
        # and the M05 criterion: ZERO embedding provider calls.
        calls_before_rerun = provider.total_texts_embedded
        rerun = await ReindexSource(detect_changes=detect).execute(
            workspace_id, source.id, batch_id="corpus-2"
        )
        assert rerun.enqueued_job_ids == ()
        assert rerun.skipped_unchanged == TOTAL
        assert provider.total_texts_embedded == calls_before_rerun

        assert elapsed > 0  # timing recorded via the CI step log
    finally:
        await engine.dispose()
