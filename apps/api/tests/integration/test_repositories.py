"""M03: repository round-trips, workspace isolation, UoW semantics.

Every test runs against a freshly migrated database on the shared
pgvector Postgres, through the same UnitOfWork the application uses.
"""

from collections.abc import AsyncIterator, Callable
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.values import ContentHash
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.tables import EMBEDDING_DIM
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

HASH = ContentHash("b" * 64)


class Harness:
    """Two workspaces on one migrated database, plus a UoW factory."""

    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        workspace_a: UUID,
        workspace_b: UUID,
    ) -> None:
        self.uow = uow_factory
        self.workspace_a = workspace_a
        self.workspace_b = workspace_b

    async def committed_source(self, workspace_id: UUID, uri: str = "/home/u/docs") -> Source:
        source = Source(workspace_id=workspace_id, kind="folder", name="Docs", uri=uri)
        async with self.uow() as uow:
            await uow.sources.add(source)
            await uow.commit()
        return source


@pytest.fixture
async def harness(migrated_database_url: str) -> AsyncIterator[Harness]:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    workspace_a = await ensure_default_workspace(factory, name="A")
    workspace_b = await ensure_default_workspace(factory, name="B")
    yield Harness(lambda: SqlAlchemyUnitOfWork(factory), workspace_a, workspace_b)
    await engine.dispose()


class TestSourceRepository:
    async def test_round_trip_preserves_entity(self, harness: Harness) -> None:
        source = await harness.committed_source(harness.workspace_a)
        async with harness.uow() as uow:
            loaded = await uow.sources.get(harness.workspace_a, source.id)
        assert loaded == source

    async def test_get_by_uri_and_list_active(self, harness: Harness) -> None:
        source = await harness.committed_source(harness.workspace_a)
        paused = Source(
            workspace_id=harness.workspace_a,
            kind="folder",
            name="Paused",
            uri="/home/u/paused",
            status="paused",
        )
        async with harness.uow() as uow:
            await uow.sources.add(paused)
            await uow.commit()
        async with harness.uow() as uow:
            by_uri = await uow.sources.get_by_uri(harness.workspace_a, source.uri)
            active = await uow.sources.list_active(harness.workspace_a)
        assert by_uri == source
        assert [s.id for s in active] == [source.id]

    async def test_soft_deleted_sources_are_invisible(self, harness: Harness) -> None:
        source = await harness.committed_source(harness.workspace_a)
        source.soft_delete()
        async with harness.uow() as uow:
            await uow.sources.save(source)
            await uow.commit()
        async with harness.uow() as uow:
            assert await uow.sources.get(harness.workspace_a, source.id) is None
            assert await uow.sources.get_by_uri(harness.workspace_a, source.uri) is None


class TestWorkspaceIsolation:
    async def test_cross_workspace_reads_return_nothing(self, harness: Harness) -> None:
        """M03 acceptance: workspace scoping in SQL, not in hope."""
        source = await harness.committed_source(harness.workspace_a)
        document = Document(source_id=source.id, path="a.md")
        version = DocumentVersion(
            document_id=document.id, content_hash=HASH, size_bytes=3, parser="markdown"
        )
        job = IngestionJob(source_id=source.id, document_id=document.id)
        chunk = Chunk(
            document_version_id=version.id,
            ordinal=0,
            text="alpha",
            token_count=1,
            embedding=tuple([0.5] * EMBEDDING_DIM),
            embedding_model="nomic-embed-text",
        )
        async with harness.uow() as uow:
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.chunks.add_all([chunk])
            await uow.ingestion_jobs.add(job)
            await uow.commit()

        wrong = harness.workspace_b
        async with harness.uow() as uow:
            assert await uow.sources.get(wrong, source.id) is None
            assert await uow.sources.list_active(wrong) == []
            assert await uow.documents.get(wrong, document.id) is None
            assert await uow.documents.get_by_path(wrong, source.id, "a.md") is None
            assert await uow.documents.list_for_source(wrong, source.id) == []
            assert await uow.document_versions.get(wrong, version.id) is None
            assert await uow.document_versions.list_for_document(wrong, document.id) == []
            assert await uow.chunks.list_for_version(wrong, version.id) == []
            assert await uow.chunks.count_for_version(wrong, version.id) == 0
            assert await uow.ingestion_jobs.get(wrong, job.id) is None
            assert await uow.ingestion_jobs.list_by_state(wrong, "pending") == []


class TestDocumentAggregate:
    async def test_version_and_current_pointer_flip(self, harness: Harness) -> None:
        source = await harness.committed_source(harness.workspace_a)
        document = Document(source_id=source.id, path="notes.md", mime_type="text/markdown")
        version = DocumentVersion(
            document_id=document.id, content_hash=HASH, size_bytes=42, parser="markdown"
        )
        async with harness.uow() as uow:
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.commit()

        document.set_current_version(version.id)
        async with harness.uow() as uow:
            await uow.documents.save(document)
            await uow.commit()

        async with harness.uow() as uow:
            loaded = await uow.documents.get(harness.workspace_a, document.id)
            versions = await uow.document_versions.list_for_document(
                harness.workspace_a, document.id
            )
        assert loaded is not None
        assert loaded.current_version_id == version.id
        assert versions == [version]

    async def test_chunk_round_trip_with_embedding(self, harness: Harness) -> None:
        source = await harness.committed_source(harness.workspace_a)
        document = Document(source_id=source.id, path="c.md")
        version = DocumentVersion(
            document_id=document.id, content_hash=HASH, size_bytes=9, parser="markdown"
        )
        chunks = [
            Chunk(
                document_version_id=version.id,
                ordinal=index,
                text=f"chunk {index}",
                token_count=2,
                heading_path=("Title", f"Section {index}"),
                embedding=tuple(float(index) / 10 for _ in range(EMBEDDING_DIM)),
                embedding_model="nomic-embed-text",
            )
            for index in range(3)
        ]
        async with harness.uow() as uow:
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.chunks.add_all(chunks)
            await uow.commit()

        async with harness.uow() as uow:
            loaded = await uow.chunks.list_for_version(harness.workspace_a, version.id)
            count = await uow.chunks.count_for_version(harness.workspace_a, version.id)
        assert count == 3
        assert loaded == chunks  # ordinal order, embeddings intact


class TestIngestionJobPersistence:
    async def test_state_machine_round_trip(self, harness: Harness) -> None:
        source = await harness.committed_source(harness.workspace_a)
        job = IngestionJob(source_id=source.id, trace_id="trace-m03")
        async with harness.uow() as uow:
            await uow.ingestion_jobs.add(job)
            await uow.commit()

        job.start()
        job.fail("boom")
        async with harness.uow() as uow:
            await uow.ingestion_jobs.save(job)
            await uow.commit()

        async with harness.uow() as uow:
            failed = await uow.ingestion_jobs.list_by_state(harness.workspace_a, "failed")
        assert [j.id for j in failed] == [job.id]
        assert failed[0].attempts == 1
        assert failed[0].error == "boom"


class TestUnitOfWork:
    async def test_uncommitted_work_is_discarded(self, harness: Harness) -> None:
        source = Source(workspace_id=harness.workspace_a, kind="folder", name="Ghost", uri="/ghost")
        async with harness.uow() as uow:
            await uow.sources.add(source)
            # no commit
        async with harness.uow() as uow:
            assert await uow.sources.get_by_uri(harness.workspace_a, "/ghost") is None

    async def test_exception_rolls_back_atomically(self, harness: Harness) -> None:
        source = Source(workspace_id=harness.workspace_a, kind="folder", name="Half", uri="/half")
        with pytest.raises(RuntimeError, match="midway"):
            async with harness.uow() as uow:
                await uow.sources.add(source)
                raise RuntimeError("midway")
        async with harness.uow() as uow:
            assert await uow.sources.get_by_uri(harness.workspace_a, "/half") is None

    async def test_commit_makes_work_visible_to_new_uow(self, harness: Harness) -> None:
        source = await harness.committed_source(harness.workspace_a, uri="/visible")
        async with harness.uow() as uow:
            assert await uow.sources.get(harness.workspace_a, source.id) == source
