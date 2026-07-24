"""M06 API surface over real Postgres: POST /search and the document
read endpoints the source viewer (M09) and citations (M08) build on.

The app's Ollama provider is swapped for the counting fake after
startup — Container is a frozen dataclass, so the swap is one
dataclasses.replace, and the SQL searcher underneath stays real.
"""

from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config.settings import Settings
from atlas.domain.knowledge.entities import Chunk, Document, DocumentVersion, Source
from atlas.domain.knowledge.values import ContentHash
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.tables import EMBEDDING_DIM
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.presentation.app import create_app
from tests.conftest import app_client
from tests.fakes import FakeEmbeddingProvider

pytestmark = pytest.mark.integration

TARGET_TEXT = "The reimbursement policy covers travel and equipment purchases."
OTHER_TEXT = "Meeting cadence is weekly on Tuesdays for the platform team."


class Api:
    def __init__(self, http: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]) -> None:
        self.http = http
        self.factory = factory
        self.provider = FakeEmbeddingProvider(dimensions=EMBEDDING_DIM)
        self.document_id: str = ""
        self.chunk_ids: list[str] = []


@pytest.fixture
async def api(migrated_database_url: str, redis_url: str) -> AsyncIterator[Api]:
    settings = Settings(
        database_url=migrated_database_url, redis_url=redis_url, readiness_timeout_seconds=5.0
    )
    app = create_app(settings)
    async for http in app_client(app):
        container = app.state.container
        rig = Api(http, container.session_factory)
        app.state.container = replace(container, embedding_provider=rig.provider)
        await _seed(rig)
        yield rig


async def _seed(rig: Api) -> None:
    """One source, one document, two embedded chunks — vectors from the
    same fake provider the app now uses for query embedding. Seeds the
    DEFAULT workspace ("Local") — the same one get_workspace_id resolves
    for every request; a differently named workspace would be invisible
    to the API (which is itself the scoping working as designed)."""
    workspace_id = await ensure_default_workspace(rig.factory)
    source = Source(workspace_id=workspace_id, kind="folder", name="Docs", uri="/seeded")
    document = Document(source_id=source.id, path="policy.md", mime_type="text/markdown")
    document.title = "Policies"
    version = DocumentVersion(
        document_id=document.id, content_hash=ContentHash("a" * 64), size_bytes=1, parser="md"
    )
    vectors = await rig.provider.embed_documents([TARGET_TEXT, OTHER_TEXT])
    rig.provider.calls.clear()  # seeding is not part of the assertions
    chunks = [
        Chunk(
            document_version_id=version.id,
            ordinal=index,
            text=text_value,
            token_count=len(text_value.split()),
            content_hash=ContentHash(f"{index + 1:x}" * 64),
            heading_path=("Handbook",),
            meta={"breadcrumb": "Policies > Handbook", "page_start": 1, "page_end": 1},
            embedding=vectors[index],
            embedding_model=rig.provider.model,
        )
        for index, text_value in enumerate([TARGET_TEXT, OTHER_TEXT])
    ]
    async with SqlAlchemyUnitOfWork(rig.factory) as uow:
        await uow.sources.add(source)
        await uow.documents.add(document)
        await uow.document_versions.add(version)
        await uow.chunks.add_all(chunks)
        document.set_current_version(version.id)  # flip after the version exists
        await uow.documents.save(document)
        await uow.commit()
    rig.document_id = str(document.id)
    rig.chunk_ids = [str(chunk.id) for chunk in chunks]


async def test_search_returns_fused_results_with_ranks_and_highlights(api: Api) -> None:
    response = await api.http.post("/api/v1/search", json={"query": TARGET_TEXT})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == TARGET_TEXT
    assert body["results"]
    top = body["results"][0]
    assert top["chunk_id"] == api.chunk_ids[0]
    assert top["document_id"] == api.document_id
    assert (top["vector_rank"], top["keyword_rank"]) == (1, 1)
    assert top["highlight"] and "<mark>" in top["highlight"]
    assert top["heading_path"] == ["Handbook"]
    assert top["score"] > 0


async def test_search_filters_scope_results(api: Api) -> None:
    scoped = await api.http.post(
        "/api/v1/search",
        json={"query": "policy", "filters": {"mime_types": ["application/pdf"]}},
    )
    assert scoped.status_code == 200
    assert scoped.json()["results"] == []


async def test_search_validation_shapes(api: Api) -> None:
    empty = await api.http.post("/api/v1/search", json={"query": ""})
    assert empty.status_code == 422  # pydantic min_length
    too_many = await api.http.post("/api/v1/search", json={"query": "q", "limit": 50})
    assert too_many.status_code == 422


async def test_document_and_chunk_read_endpoints(api: Api) -> None:
    document = await api.http.get(f"/api/v1/documents/{api.document_id}")
    assert document.status_code == 200
    body = document.json()
    assert body["path"] == "policy.md"
    assert body["title"] == "Policies"
    assert body["current_version_id"] is not None

    chunks = await api.http.get(f"/api/v1/documents/{api.document_id}/chunks")
    assert chunks.status_code == 200
    listed = chunks.json()
    assert [c["id"] for c in listed] == api.chunk_ids
    assert listed[0]["text"] == TARGET_TEXT
    assert listed[0]["heading_path"] == ["Handbook"]
    assert (listed[0]["page_start"], listed[0]["page_end"]) == (1, 1)

    missing = await api.http.get("/api/v1/documents/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    problem = missing.json()
    assert problem["code"] == "not_found"
    assert problem["trace_id"]
