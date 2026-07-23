"""Composition root: the one place the object graph is assembled.

Clean Architecture becomes enforceable here (ADR-0002): adapters are
constructed at the process edge and handed to consumers as values.
Nothing in the codebase imports a live singleton — tests build as many
independent containers as they like. The worker bootstrap (M04) will
assemble the same ``Container`` from its own entrypoint.
"""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.config.feature_flags import FeatureFlags, LayeredFeatureFlags
from atlas.config.settings import Settings
from atlas.infrastructure.jobs.celery_app import create_celery_app
from atlas.infrastructure.jobs.dispatcher import CeleryIngestionDispatcher
from atlas.infrastructure.parsing import ParserRegistry, default_registry
from atlas.infrastructure.persistence.feature_flags import SqlFlagOverridesReader
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.watcher.filesystem import LocalFileStore


@dataclass(frozen=True)
class Container:
    """Long-lived process dependencies, built once per process."""

    settings: Settings
    feature_flags: LayeredFeatureFlags
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    dispatcher: CeleryIngestionDispatcher
    file_store: LocalFileStore
    parser_registry: ParserRegistry

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """One Unit of Work per use-case invocation (application port)."""
        return SqlAlchemyUnitOfWork(self.session_factory)


def build_container(settings: Settings) -> Container:
    """Construct the object graph.

    Pure construction: both clients connect lazily on first use, so this
    never performs I/O — the same guarantee `create_app` makes.
    """
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return Container(
        settings=settings,
        feature_flags=LayeredFeatureFlags(
            FeatureFlags(settings.feature_flags),
            SqlFlagOverridesReader(session_factory),
        ),
        db_engine=engine,
        session_factory=session_factory,
        redis=Redis.from_url(settings.redis_url),
        dispatcher=CeleryIngestionDispatcher(create_celery_app(settings)),
        file_store=LocalFileStore(),
        parser_registry=default_registry(),
    )


async def close_container(container: Container) -> None:
    await container.redis.aclose()
    await container.db_engine.dispose()
