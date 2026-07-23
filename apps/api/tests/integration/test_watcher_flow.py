"""M04 acceptance: file change → ingestion_jobs row within 5 seconds.

Uses the production configuration end to end: real watchfiles with the
default 2 s debounce, real DetectChanges over real Postgres, real files
on disk. Only the queue transport is faked — job-row visibility is the
criterion, not Celery delivery.
"""

import asyncio
import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.application.ingestion import DetectChanges, RegisterSource
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.watcher.filesystem import LocalFileStore
from atlas.infrastructure.watcher.service import WatcherService
from atlas.infrastructure.watcher.targets import SqlWatchTargets
from atlas.shared.ids import uuid7
from tests.fakes import FakeDispatcher

pytestmark = pytest.mark.integration

LATENCY_BOUND_SECONDS = 5.0


async def test_created_file_has_a_job_row_within_five_seconds(
    migrated_database_url: str, tmp_path: Path
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        workspace_id = await ensure_default_workspace(factory, name="W")
        registry = default_registry()
        dispatcher = FakeDispatcher()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(factory)

        detect = DetectChanges(
            uow_factory=uow_factory,
            file_store=LocalFileStore(),
            dispatcher=dispatcher,
            supports=registry.supports,
        )
        register = RegisterSource(
            uow_factory=uow_factory, file_store=LocalFileStore(), detect_changes=detect
        )
        source, _report = await register.execute(
            workspace_id, name="Watched", uri=str(tmp_path), batch_id="initial"
        )

        async def on_changes(changed_source: Source, paths: frozenset[str]) -> None:
            await detect.execute(
                changed_source.workspace_id,
                changed_source.id,
                paths=sorted(paths),
                batch_id=f"watch-{uuid7()}",
            )

        service = WatcherService(SqlWatchTargets(factory), on_changes, refresh_seconds=0.5)
        stop = asyncio.Event()
        runner = asyncio.create_task(service.run(stop))
        try:
            await asyncio.sleep(1.5)  # watcher subscribes to the source

            started = time.monotonic()
            (tmp_path / "fresh-note.md").write_text("# Fresh\n\nJust created.")

            deadline = started + LATENCY_BOUND_SECONDS
            job_seen_at: float | None = None
            while time.monotonic() < deadline:
                async with SqlAlchemyUnitOfWork(factory) as uow:
                    document = await uow.documents.get_by_path(
                        workspace_id, source.id, "fresh-note.md"
                    )
                    if document is not None:
                        jobs = await uow.ingestion_jobs.list_by_state(workspace_id, "pending")
                        if any(job.document_id == document.id for job in jobs):
                            job_seen_at = time.monotonic()
                            break
                await asyncio.sleep(0.1)

            assert job_seen_at is not None, "no ingestion_jobs row within 5 s"
            latency = job_seen_at - started
            assert latency <= LATENCY_BOUND_SECONDS, f"latency {latency:.2f}s"
        finally:
            stop.set()
            await asyncio.wait_for(runner, timeout=10)
    finally:
        await engine.dispose()
