"""Watcher runner: composition + lifecycle inside the worker process.

The watcher runs one persistent event loop in a daemon thread started by
the Celery `worker_ready` signal (M04 deliverable: "started with the
worker process"). Its engine lives entirely inside that loop, so it uses
a normal pool — unlike the task runtime's NullPool.
"""

import asyncio
import threading

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.application.ingestion import DetectChanges
from atlas.application.ports import IngestionDispatcher
from atlas.config.settings import Settings
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.watcher.filesystem import LocalFileStore
from atlas.infrastructure.watcher.service import WatcherService
from atlas.infrastructure.watcher.targets import SqlWatchTargets
from atlas.shared.ids import uuid7

_logger = structlog.get_logger("atlas.watcher")


async def watcher_main(
    settings: Settings, dispatcher: IngestionDispatcher, stop: asyncio.Event
) -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    registry = default_registry()
    detect = DetectChanges(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        file_store=LocalFileStore(),
        dispatcher=dispatcher,
        supports=registry.supports,
    )

    async def on_changes(source: Source, paths: frozenset[str]) -> None:
        batch_id = f"watch-{uuid7()}"
        report = await detect.execute(
            source.workspace_id, source.id, paths=sorted(paths), batch_id=batch_id
        )
        _logger.info(
            "watcher.batch_detected",
            source_id=str(source.id),
            batch_id=batch_id,
            enqueued=len(report.enqueued_job_ids),
            skipped=report.skipped_unchanged,
            deduplicated=report.deduplicated,
        )

    service = WatcherService(SqlWatchTargets(session_factory), on_changes)
    try:
        await service.run(stop)
    finally:
        await engine.dispose()


class WatcherThread:
    """Daemon-thread lifecycle for the watcher loop."""

    def __init__(self, settings: Settings, dispatcher: IngestionDispatcher) -> None:
        self._settings = settings
        self._dispatcher = dispatcher
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._thread is not None:
            return

        def _run() -> None:
            asyncio.run(self._main())

        self._thread = threading.Thread(target=_run, name="atlas-watcher", daemon=True)
        self._thread.start()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        await watcher_main(self._settings, self._dispatcher, self._stop)

    def stop(self, timeout_seconds: float = 5.0) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)
