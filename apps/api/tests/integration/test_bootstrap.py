"""First-boot seeding races (M09).

On a fresh database a page load fires parallel requests, each of which
runs ensure_default_workspace. The seed must converge on one user and
one workspace no matter how the inserts interleave — the loser of the
unique-constraint race retries and reads the winner's rows.
"""

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.tables import UserRow, WorkspaceRow

pytestmark = pytest.mark.integration

CONCURRENCY = 8


class TestEnsureDefaultWorkspace:
    async def test_concurrent_first_boot_converges_on_one_workspace(
        self, migrated_database_url: str
    ) -> None:
        engine = create_async_engine(migrated_database_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            ids = await asyncio.gather(
                *(ensure_default_workspace(factory) for _ in range(CONCURRENCY))
            )

            assert len(set(ids)) == 1
            async with factory() as session:
                users = (await session.execute(select(func.count(UserRow.id)))).scalar_one()
                workspaces = (
                    await session.execute(select(func.count(WorkspaceRow.id)))
                ).scalar_one()
            assert users == 1
            assert workspaces == 1
        finally:
            await engine.dispose()

    async def test_repeat_call_returns_same_workspace(self, migrated_database_url: str) -> None:
        engine = create_async_engine(migrated_database_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            first = await ensure_default_workspace(factory)
            second = await ensure_default_workspace(factory)
            assert first == second
        finally:
            await engine.dispose()
