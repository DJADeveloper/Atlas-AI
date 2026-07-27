"""Dropped-file uploads end to end over real Postgres, a real multipart
request, and a real filesystem: bytes land inside the managed root (and
only there), the source is created once, and the jobs are queued."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from atlas.application.ingestion import UPLOADS_SOURCE_NAME
from atlas.config.settings import Settings
from atlas.presentation.app import create_app
from tests.conftest import app_client

pytestmark = pytest.mark.integration

UPLOAD_URL = "/api/v1/sources/uploads"


@pytest.fixture
async def client(
    migrated_database_url: str, redis_url: str, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, Path]]:
    uploads = tmp_path / "uploads"
    settings = Settings(
        database_url=migrated_database_url,
        redis_url=redis_url,
        readiness_timeout_seconds=5.0,
        uploads_dir=str(uploads),
    )
    async for http in app_client(create_app(settings)):
        yield http, uploads


def _stored_files(uploads_root: Path) -> list[Path]:
    return sorted(p for p in uploads_root.rglob("*") if p.is_file())


async def test_dropped_files_are_stored_and_queued(
    client: tuple[httpx.AsyncClient, Path],
) -> None:
    http, uploads = client

    response = await http.post(
        UPLOAD_URL,
        files=[
            ("files", ("meridian.md", b"# Meridian\n\n30 days notice.\n", "text/markdown")),
            ("files", ("plain.txt", b"A plain note.\n", "text/plain")),
        ],
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"]["name"] == UPLOADS_SOURCE_NAME
    assert sorted(body["stored"]) == ["meridian.md", "plain.txt"]
    assert body["rejected"] == []
    assert body["enqueued"] == 2

    on_disk = _stored_files(uploads)
    assert [p.name for p in on_disk] == ["meridian.md", "plain.txt"]
    assert on_disk[0].read_bytes().startswith(b"# Meridian")
    # Every byte stays under the workspace's own subdirectory.
    assert all(p.is_relative_to(uploads) for p in on_disk)

    jobs = (await http.get(f"/api/v1/jobs?trace_id={body['batch_id']}")).json()
    assert len(jobs) == 2
    assert all(job["state"] == "pending" for job in jobs)


async def test_second_drop_reuses_one_managed_source(
    client: tuple[httpx.AsyncClient, Path],
) -> None:
    http, _ = client

    first = await http.post(UPLOAD_URL, files=[("files", ("a.md", b"# A\n", "text/markdown"))])
    second = await http.post(UPLOAD_URL, files=[("files", ("b.md", b"# B\n", "text/markdown"))])

    assert first.json()["source"]["id"] == second.json()["source"]["id"]
    sources = (await http.get("/api/v1/sources")).json()
    assert [s["name"] for s in sources] == [UPLOADS_SOURCE_NAME]


async def test_traversal_filename_cannot_escape_the_managed_root(
    client: tuple[httpx.AsyncClient, Path],
) -> None:
    http, uploads = client

    response = await http.post(
        UPLOAD_URL,
        files=[("files", ("../../../escaped.md", b"# Escaped\n", "text/markdown"))],
    )

    assert response.status_code == 201
    assert response.json()["stored"] == ["escaped.md"]
    written = _stored_files(uploads)
    assert [p.name for p in written] == ["escaped.md"]
    assert not (uploads.parent.parent / "escaped.md").exists()


async def test_unsupported_type_is_reported_not_silently_dropped(
    client: tuple[httpx.AsyncClient, Path],
) -> None:
    http, uploads = client

    response = await http.post(
        UPLOAD_URL,
        files=[("files", ("archive.zip", b"PK\x03\x04", "application/zip"))],
    )

    body = response.json()
    assert body["stored"] == []
    assert body["rejected"] == [{"filename": "archive.zip", "reason": "unsupported_type"}]
    assert _stored_files(uploads) == []
