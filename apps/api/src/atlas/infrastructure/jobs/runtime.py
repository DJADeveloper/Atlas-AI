"""Worker-process runtime: the worker's composition root (M02 promise).

A Celery worker process has exactly one composition seam — process
startup — so this module holds one lazily-built runtime per process.
This is the documented exception to "no module-level singletons":
`set_runtime` exists precisely so tests inject fakes instead.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from atlas.application.ingestion import IngestDocument
from atlas.config.settings import Settings, load_settings
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.watcher.filesystem import LocalFileStore
from atlas.observability.logging import configure_logging


@dataclass(frozen=True)
class WorkerRuntime:
    settings: Settings
    engine: AsyncEngine
    ingest_document: IngestDocument


def build_worker_runtime(settings: Settings | None = None) -> WorkerRuntime:
    resolved = settings if settings is not None else load_settings()
    configure_logging(resolved.log_level)
    engine = create_async_engine(resolved.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    registry = default_registry()
    return WorkerRuntime(
        settings=resolved,
        engine=engine,
        ingest_document=IngestDocument(
            uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            file_store=LocalFileStore(),
            parser_for=registry.parser_for,
        ),
    )


class _RuntimeHolder:
    """One runtime per worker process; mutable only through set_runtime."""

    def __init__(self) -> None:
        self.instance: WorkerRuntime | None = None


_holder = _RuntimeHolder()


def get_runtime() -> WorkerRuntime:
    if _holder.instance is None:
        _holder.instance = build_worker_runtime()
    return _holder.instance


def set_runtime(runtime: WorkerRuntime | None) -> None:
    """Test seam: inject a runtime built over fakes or a scratch database."""
    _holder.instance = runtime
