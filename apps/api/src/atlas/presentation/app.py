"""FastAPI application factory.

Construction is pure: no network, database, or filesystem access happens at
import time or inside :func:`create_app`. Client objects for Postgres and
Redis are created in the lifespan context — both construct lazily and only
open connections when first used (i.e. when `/ready` probes them).

Run with: ``uvicorn --factory atlas.presentation.app:create_app``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from atlas.config.settings import Settings, get_settings
from atlas.presentation.routes.system import router as system_router
from atlas.shared.version import get_version


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_async_engine(settings.database_url)
    redis: Redis = Redis.from_url(settings.redis_url)
    app.state.db_engine = engine
    app.state.redis = redis
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the Atlas API application.

    Args:
        settings: Optional explicit settings; tests inject these. When
            omitted (the uvicorn factory path), process settings are read
            from the environment.
    """
    app = FastAPI(
        title="Atlas API",
        version=get_version(),
        lifespan=_lifespan,
    )
    app.state.settings = settings if settings is not None else get_settings()
    app.include_router(system_router)
    return app
