"""Shared test fixtures."""

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from atlas.config.settings import Settings
from atlas.presentation.app import create_app


@pytest.fixture
def unreachable_settings() -> Settings:
    """Connection targets pointing at a closed local port.

    Explicit keyword arguments take precedence over every pydantic-settings
    source, so these values are deterministic regardless of host env vars or
    a stray ``.env``. Port 9 (discard protocol) is closed on any dev machine;
    connections are refused immediately, keeping failure-path tests fast and
    offline.
    """
    return Settings(
        database_url="postgresql+asyncpg://atlas:atlas@127.0.0.1:9/atlas",
        redis_url="redis://127.0.0.1:9/0",
        readiness_timeout_seconds=1.0,
    )


async def app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client against ``app`` with its lifespan running."""
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
        ) as client,
    ):
        yield client


@pytest.fixture
async def unreachable_client(
    unreachable_settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    async for client in app_client(create_app(unreachable_settings)):
        yield client
