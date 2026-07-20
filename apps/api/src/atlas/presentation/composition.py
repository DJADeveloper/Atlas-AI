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

from atlas.config.feature_flags import FeatureFlags
from atlas.config.settings import Settings
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork


@dataclass(frozen=True)
class Container:
    """Long-lived process dependencies, built once per process."""

    settings: Settings
    feature_flags: FeatureFlags
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """One Unit of Work per use-case invocation (application port)."""
        return SqlAlchemyUnitOfWork(self.session_factory)


def build_container(settings: Settings) -> Container:
    """Construct the object graph.

    Pure construction: both clients connect lazily on first use, so this
    never performs I/O — the same guarantee `create_app` makes.
    """
    engine = create_async_engine(settings.database_url)
    return Container(
        settings=settings,
        feature_flags=FeatureFlags(settings.feature_flags),
        db_engine=engine,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        redis=Redis.from_url(settings.redis_url),
    )


async def close_container(container: Container) -> None:
    await container.redis.aclose()
    await container.db_engine.dispose()
