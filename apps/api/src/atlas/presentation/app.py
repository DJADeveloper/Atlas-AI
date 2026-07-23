"""FastAPI application factory.

Construction is pure: no network, database, or filesystem access happens at
import time or inside :func:`create_app`. The object graph is assembled by
the composition root (:mod:`atlas.presentation.composition`) inside the
lifespan; clients construct lazily and only open connections when first
used (i.e. when `/ready` probes them).

Run with: ``uvicorn --factory atlas.presentation.app:create_app``.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from atlas.config.settings import Settings, load_settings
from atlas.infrastructure.persistence.migrations import run_migrations_sync
from atlas.observability.logging import configure_logging
from atlas.presentation.composition import build_container, close_container
from atlas.presentation.errors import register_exception_handlers
from atlas.presentation.middleware import TraceIdMiddleware
from atlas.presentation.routes.jobs import router as jobs_router
from atlas.presentation.routes.sources import router as sources_router
from atlas.presentation.routes.system import router as system_router
from atlas.shared.version import get_version


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = build_container(app.state.settings)
    app.state.container = container
    if container.settings.run_migrations_on_startup:
        await asyncio.to_thread(run_migrations_sync, container.settings.database_url)
    try:
        yield
    finally:
        await close_container(container)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the Atlas API application.

    Args:
        settings: Optional explicit settings; tests inject these. When
            omitted (the uvicorn factory path), process settings are read
            from the environment.
    """
    resolved = settings if settings is not None else load_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="Atlas API",
        version=get_version(),
        lifespan=_lifespan,
    )
    app.state.settings = resolved
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(system_router)
    app.include_router(sources_router)
    app.include_router(jobs_router)
    return app
