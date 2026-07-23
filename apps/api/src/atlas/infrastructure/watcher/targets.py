"""SQL implementation of the watcher's system-level target view."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.persistence.mappers import source_from_row
from atlas.infrastructure.persistence.tables import SourceRow


class SqlWatchTargets:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def active_folder_sources(self) -> list[Source]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SourceRow)
                    .where(SourceRow.kind == "folder")
                    .where(SourceRow.status == "active")
                    .where(SourceRow.deleted_at.is_(None))
                )
            ).scalars()
            return [source_from_row(row) for row in rows]
