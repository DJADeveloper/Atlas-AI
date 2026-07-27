"""The worker probe over a real broker.

Nothing consumes the queues in this suite, which is exactly the case
worth pinning: the endpoint must answer promptly with "nobody is
listening" rather than hanging on the control channel.
"""

import time
from collections.abc import AsyncIterator

import httpx
import pytest

from atlas.config.settings import Settings
from atlas.infrastructure.jobs.probe import REQUIRED_QUEUES
from atlas.presentation.app import create_app
from tests.conftest import app_client

pytestmark = pytest.mark.integration

WORKERS_URL = "/api/v1/system/workers"


@pytest.fixture
async def client(migrated_database_url: str, redis_url: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=migrated_database_url,
        redis_url=redis_url,
        readiness_timeout_seconds=5.0,
    )
    async for http in app_client(create_app(settings)):
        yield http


async def test_reports_an_idle_fleet_without_hanging(client: httpx.AsyncClient) -> None:
    started = time.monotonic()
    response = await client.get(WORKERS_URL)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    body = response.json()
    assert body["online"] is False
    assert body["reachable"] is True
    assert body["workers"] == []
    assert body["required_queues"] == list(REQUIRED_QUEUES)
    # Every queue is stranded, which is what a pending job is telling you.
    assert body["unconsumed_queues"] == list(REQUIRED_QUEUES)
    assert elapsed < 5.0, "the probe must be bounded, not open-ended"
