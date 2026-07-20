"""Probe endpoint behavior that needs no live services."""

import httpx

from atlas.shared.version import get_version


async def test_health_is_ok_with_version(unreachable_client: httpx.AsyncClient) -> None:
    """Liveness never depends on downstream services being reachable."""
    response = await unreachable_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == get_version()
    assert body["version"] != ""


async def test_ready_returns_503_when_dependencies_unreachable(
    unreachable_client: httpx.AsyncClient,
) -> None:
    response = await unreachable_client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"postgres": "error", "redis": "error"}
