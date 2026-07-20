"""DB override source for feature flags (`feature_flags` table)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.infrastructure.persistence.tables import FeatureFlagRow


class SqlFlagOverridesReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def read_all(self) -> dict[str, bool]:
        async with self._session_factory() as session:
            rows = await session.execute(select(FeatureFlagRow.key, FeatureFlagRow.enabled))
            return dict(rows.tuples().all())
