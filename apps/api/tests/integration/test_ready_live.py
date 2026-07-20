"""Readiness probe against real services via testcontainers.

Marked ``integration``: requires a container runtime. The CI integration
job runs these; locally use ``uv run pytest -m integration``. Images match
the compose `core` profile (`docs/40-deployment-architecture.md` §2.1)
so the probe is exercised against exactly what developers run.
"""

from collections.abc import Iterator

import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from atlas.config.settings import Settings
from atlas.presentation.app import create_app
from tests.conftest import app_client

pytestmark = pytest.mark.integration

POSTGRES_IMAGE = "pgvector/pgvector:pg16"
REDIS_IMAGE = "redis:7-alpine"
PROBE_TIMEOUT_SECONDS = 5.0


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="module")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer(REDIS_IMAGE) as container:
        yield container


def _redis_url(container: RedisContainer) -> str:
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


async def test_ready_returns_200_when_all_dependencies_up(
    postgres_container: PostgresContainer,
    redis_container: RedisContainer,
) -> None:
    settings = Settings(
        database_url=postgres_container.get_connection_url(),
        redis_url=_redis_url(redis_container),
        readiness_timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )
    async for client in app_client(create_app(settings)):
        response = await client.get("/ready")
        assert response.status_code == 200
        assert response.headers["X-Trace-Id"]
        assert response.json() == {
            "status": "ready",
            "checks": {"postgres": "ok", "redis": "ok"},
        }


async def test_health_reflects_profile_switch(
    postgres_container: PostgresContainer,
    redis_container: RedisContainer,
) -> None:
    """M02 integration smoke: the profile setting surfaces end to end."""
    settings = Settings(
        profile="local-only",
        database_url=postgres_container.get_connection_url(),
        redis_url=_redis_url(redis_container),
        readiness_timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )
    async for client in app_client(create_app(settings)):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["profile"] == "local-only"
        assert response.headers["X-Trace-Id"]


async def test_ready_returns_503_when_redis_stopped(
    postgres_container: PostgresContainer,
) -> None:
    with RedisContainer(REDIS_IMAGE) as ephemeral_redis:
        redis_url = _redis_url(ephemeral_redis)
    # Context exit stopped the container; its port is now closed.
    settings = Settings(
        database_url=postgres_container.get_connection_url(),
        redis_url=redis_url,
        readiness_timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )
    async for client in app_client(create_app(settings)):
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "checks": {"postgres": "ok", "redis": "error"},
        }


async def test_ready_returns_503_when_postgres_stopped(
    redis_container: RedisContainer,
) -> None:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as ephemeral_postgres:
        database_url = ephemeral_postgres.get_connection_url()
    # Context exit stopped the container; its port is now closed.
    settings = Settings(
        database_url=database_url,
        redis_url=_redis_url(redis_container),
        readiness_timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )
    async for client in app_client(create_app(settings)):
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "checks": {"postgres": "error", "redis": "ok"},
        }
