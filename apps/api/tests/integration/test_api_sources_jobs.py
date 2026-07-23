"""M04 API surface over real Postgres + Redis broker: sources CRUD,
reindex batches, and job visibility (nothing fails silently)."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from atlas.config.settings import Settings
from atlas.presentation.app import create_app
from tests.conftest import app_client

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(
    migrated_database_url: str, redis_url: str, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, Path]]:
    (tmp_path / "alpha.md").write_text("# Alpha\n\nFirst fixture doc.")
    (tmp_path / "beta.txt").write_text("Second fixture doc.")
    settings = Settings(
        database_url=migrated_database_url,
        redis_url=redis_url,
        readiness_timeout_seconds=5.0,
    )
    async for http in app_client(create_app(settings)):
        yield http, tmp_path


async def test_register_list_jobs_reindex_lifecycle(
    client: tuple[httpx.AsyncClient, Path],
) -> None:
    http, corpus = client

    created = await http.post("/api/v1/sources", json={"name": "Notes", "uri": str(corpus)})
    assert created.status_code == 201
    body = created.json()
    assert body["source"]["status"] == "active"
    assert body["enqueued"] == 2
    batch_id = body["batch_id"]
    assert batch_id == created.headers["X-Trace-Id"]  # batch id IS the trace id
    source_id = body["source"]["id"]

    listed = await http.get("/api/v1/sources")
    assert [s["id"] for s in listed.json()] == [source_id]

    jobs = (await http.get(f"/api/v1/jobs?trace_id={batch_id}")).json()
    assert len(jobs) == 2
    assert all(j["state"] == "pending" and j["dead_letter"] is False for j in jobs)

    one = await http.get(f"/api/v1/jobs/{jobs[0]['id']}")
    assert one.status_code == 200
    assert one.json()["source_id"] == source_id

    # Reindex while the same content already has active jobs: the
    # idempotency key collapses every file into a dedupe, not new work.
    reindexed = await http.post(f"/api/v1/sources/{source_id}/reindex")
    assert reindexed.status_code == 202
    assert reindexed.json()["enqueued"] == 0
    assert reindexed.json()["deduplicated"] == 2

    patched = await http.patch(
        f"/api/v1/sources/{source_id}", json={"name": "Renamed", "status": "paused"}
    )
    assert patched.status_code == 200
    assert (patched.json()["name"], patched.json()["status"]) == ("Renamed", "paused")

    deleted = await http.delete(f"/api/v1/sources/{source_id}")
    assert deleted.status_code == 204
    assert (await http.get("/api/v1/sources")).json() == []

    # Soft-deleted URI still holds the unique constraint: clean 409.
    conflict = await http.post("/api/v1/sources", json={"name": "Again", "uri": str(corpus)})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "conflict"


async def test_error_shapes(client: tuple[httpx.AsyncClient, Path]) -> None:
    http, _corpus = client

    missing_body = await http.post("/api/v1/sources", json={"name": "x"})
    assert missing_body.status_code == 422
    assert missing_body.json()["code"] == "validation_error"

    bad_folder = await http.post(
        "/api/v1/sources", json={"name": "Ghost", "uri": "/definitely/not/here"}
    )
    assert bad_folder.status_code == 422
    assert bad_folder.json()["code"] == "validation_error"
    assert "does not exist" in bad_folder.json()["detail"]

    bad_state = await http.get("/api/v1/jobs?state=exploded")
    assert bad_state.status_code == 422

    unknown_job = await http.get("/api/v1/jobs/019f8c55-2e3f-7a4b-8c5d-6e7f8a9b0c1d")
    assert unknown_job.status_code == 404
    assert unknown_job.json()["code"] == "not_found"

    unknown_reindex = await http.post(
        "/api/v1/sources/019f8c55-2e3f-7a4b-8c5d-6e7f8a9b0c1d/reindex"
    )
    assert unknown_reindex.status_code == 404
