"""M02 acceptance: 100% of responses carry X-Trace-Id; logs correlate."""

import io
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from atlas.config.settings import Settings
from atlas.observability.logging import configure_logging
from atlas.presentation.app import create_app
from tests.conftest import app_client

UUID_LENGTH = 36


@pytest.fixture
async def client(unreachable_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for value in app_client(create_app(unreachable_settings)):
        yield value


async def test_every_registered_route_and_method_carries_trace_id(
    client: httpx.AsyncClient,
) -> None:
    """Sweep every path+method in the OpenAPI schema — no exceptions."""
    schema = (await client.get("/openapi.json")).json()
    swept = 0
    for path, operations in schema["paths"].items():
        for method in operations:
            response = await client.request(method.upper(), path)
            assert "X-Trace-Id" in response.headers, f"{method.upper()} {path}"
            swept += 1
    assert swept >= 2  # /health and /ready at minimum


@pytest.mark.parametrize(
    ("method", "path", "expected_status"),
    [
        ("GET", "/definitely-not-a-route", 404),
        ("POST", "/health", 405),
    ],
)
async def test_error_responses_carry_trace_id(
    client: httpx.AsyncClient, method: str, path: str, expected_status: int
) -> None:
    response = await client.request(method, path)
    assert response.status_code == expected_status
    assert "X-Trace-Id" in response.headers


async def test_valid_inbound_trace_id_is_honored(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Trace-Id": "client-supplied-0001"})
    assert response.headers["X-Trace-Id"] == "client-supplied-0001"


async def test_malformed_inbound_trace_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Trace-Id": "bad id!\nwith junk"})
    replaced = response.headers["X-Trace-Id"]
    assert replaced != "bad id!\nwith junk"
    assert len(replaced) == UUID_LENGTH


async def test_request_completed_log_carries_the_response_trace_id(
    unreachable_settings: Settings,
) -> None:
    """The header the client sees and the trace_id in the logs agree."""
    app = create_app(unreachable_settings)  # reconfigures logging itself,
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)  # so the buffer attaches after
    async for client in app_client(app):
        response = await client.get("/health")
    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    completed = [event for event in events if event["event"] == "request.completed"]
    assert len(completed) == 1
    assert completed[0]["trace_id"] == response.headers["X-Trace-Id"]
    assert completed[0]["path"] == "/health"
    assert completed[0]["status_code"] == 200
    assert "duration_ms" in completed[0]
