"""M03 acceptance: DB flag override wins and propagates without restart."""

import asyncio
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.config.feature_flags import FeatureFlags, LayeredFeatureFlags
from atlas.infrastructure.persistence.feature_flags import SqlFlagOverridesReader
from atlas.infrastructure.persistence.tables import FeatureFlagRow
from atlas.shared.ids import uuid7

pytestmark = pytest.mark.integration

PROPAGATION_BOUND_SECONDS = 30.0
REFRESH_SECONDS = 0.2


async def test_override_wins_and_flip_propagates_without_restart(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        flags = LayeredFeatureFlags(
            FeatureFlags({"voice": False}),  # config seed says OFF
            SqlFlagOverridesReader(factory),
            refresh_seconds=REFRESH_SECONDS,
        )
        assert await flags.is_enabled("voice") is False

        async with factory() as session:  # operator flips the DB row: ON
            session.add(FeatureFlagRow(id=uuid7(), key="voice", enabled=True))
            await session.commit()

        deadline = time.monotonic() + PROPAGATION_BOUND_SECONDS
        while time.monotonic() < deadline:
            if await flags.is_enabled("voice"):
                break
            await asyncio.sleep(REFRESH_SECONDS / 2)
        propagation = time.monotonic() - (deadline - PROPAGATION_BOUND_SECONDS)
        assert await flags.is_enabled("voice") is True, (
            f"override did not propagate within {PROPAGATION_BOUND_SECONDS}s"
        )
        assert propagation < PROPAGATION_BOUND_SECONDS
    finally:
        await engine.dispose()
