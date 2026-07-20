"""M02 acceptance: every error is problem+json with a stable code."""

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from atlas.config.settings import Settings
from atlas.presentation.app import create_app
from atlas.shared.errors import (
    AtlasError,
    Conflict,
    NotFound,
    PermissionDenied,
    ProviderUnavailable,
    ValidationFailed,
)
from tests.conftest import app_client

TAXONOMY_CASES = [
    (NotFound("document 0198 does not exist"), 404, "not_found"),
    (Conflict("source path already registered"), 409, "conflict"),
    (PermissionDenied("no grant covers fs.read on that path"), 403, "permission_denied"),
    (
        ValidationFailed("bad input", errors=[{"field": "path", "message": "must be absolute"}]),
        422,
        "validation_error",
    ),
    (ProviderUnavailable("all providers in the chain are down"), 503, "provider_unavailable"),
]


def _app_with_error_routes(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/raise-domain-error")
    async def raise_domain_error() -> None:
        raise app.state.error_to_raise

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail that must never leak")

    @app.get("/typed/{item_id}")
    async def typed(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app


@pytest.fixture
async def error_client(
    unreachable_settings: Settings,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = _app_with_error_routes(unreachable_settings)
    async for client in app_client(app):
        yield app, client


@pytest.mark.parametrize(("error", "expected_status", "expected_code"), TAXONOMY_CASES)
async def test_taxonomy_maps_to_problem_json(
    error_client: tuple[FastAPI, httpx.AsyncClient],
    error: AtlasError,
    expected_status: int,
    expected_code: str,
) -> None:
    app, client = error_client
    app.state.error_to_raise = error
    response = await client.get("/raise-domain-error")
    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == expected_code
    assert body["type"] == f"https://atlas.dev/errors/{expected_code}"
    assert body["status"] == expected_status
    assert body["detail"] == error.detail
    assert body["trace_id"] == response.headers["X-Trace-Id"]


async def test_validation_failed_carries_field_errors(
    error_client: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = error_client
    app.state.error_to_raise = TAXONOMY_CASES[3][0]
    body = (await client.get("/raise-domain-error")).json()
    assert body["errors"] == [{"field": "path", "message": "must be absolute"}]


async def test_request_validation_error_is_problem_json(
    error_client: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    _app, client = error_client
    response = await client.get("/typed/not-an-int")
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"], "field-level errors must be present"
    assert body["trace_id"] == response.headers["X-Trace-Id"]


async def test_unknown_route_is_problem_json_not_found(
    error_client: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    _app, client = error_client
    response = await client.get("/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "not_found"


async def test_wrong_method_is_problem_json_method_not_allowed(
    error_client: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    _app, client = error_client
    response = await client.post("/health")
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


async def test_unhandled_exception_never_leaks_internals(
    unreachable_settings: Settings,
) -> None:
    """M02 acceptance: 500 → problem+json with code + trace_id, no stack."""
    app = _app_with_error_routes(unreachable_settings)
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client,
    ):
        response = await client.get("/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["trace_id"] == response.headers["X-Trace-Id"]
    text = response.text
    assert "secret internal detail" not in text
    assert "Traceback" not in text
    assert "RuntimeError" not in text
