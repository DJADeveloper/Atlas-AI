"""System probes: liveness and readiness.

Per the API specification (`docs/12-api-specification.md` §endpoint
reference), `/health` and `/ready` live at the root path and are the only
unauthenticated endpoints. Probe responses are plain status JSON, not
problem+json — a probe result is a state report, not an API error.
"""

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from atlas.config.profiles import Profile
from atlas.config.settings import Settings
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container
from atlas.shared.version import get_version

router = APIRouter(tags=["system"])

_logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    """Liveness payload: the process is up and can identify itself."""

    status: Literal["ok"]
    version: str
    profile: Profile


class ReadinessResponse(BaseModel):
    """Readiness payload: per-dependency check results."""

    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckStatus]


@router.get("/health")
async def health(request: Request) -> HealthResponse:
    """Liveness probe — no dependencies are touched.

    Reads settings straight off app state (present before the lifespan
    runs) so liveness never depends on the container.
    """
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", version=get_version(), profile=settings.profile)


async def _check_postgres(engine: AsyncEngine, timeout_seconds: float) -> CheckStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    # A probe converts every failure mode into a status; nothing may escape.
    except Exception:
        _logger.warning("readiness: postgres check failed", exc_info=True)
        return "error"
    return "ok"


async def _check_redis(redis: Redis, timeout_seconds: float) -> CheckStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            await redis.ping()
    # A probe converts every failure mode into a status; nothing may escape.
    except Exception:
        _logger.warning("readiness: redis check failed", exc_info=True)
        return "error"
    return "ok"


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> ReadinessResponse:
    """Readiness probe — verifies Postgres and Redis are reachable.

    Checks run concurrently, each under its own timeout, so a hung
    dependency cannot stall the probe past ``readiness_timeout_seconds``.
    """
    engine = container.db_engine
    redis = container.redis
    timeout_seconds = container.settings.readiness_timeout_seconds

    postgres_status, redis_status = await asyncio.gather(
        _check_postgres(engine, timeout_seconds),
        _check_redis(redis, timeout_seconds),
    )
    checks: dict[str, CheckStatus] = {"postgres": postgres_status, "redis": redis_status}

    if all(check == "ok" for check in checks.values()):
        return ReadinessResponse(status="ready", checks=checks)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="not_ready", checks=checks)
