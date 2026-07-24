"""M06 candidate generation against real Postgres: cosine ordering,
FTS ranking with non-empty highlights, current-version-only visibility,
and filter scoping across generated combinations."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from atlas.application.ports import SearchFilters
from atlas.application.retrieval import HybridSearch
from atlas.domain.knowledge.entities import Chunk, Document, DocumentVersion, Source
from atlas.domain.knowledge.values import ContentHash
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.tables import EMBEDDING_DIM
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.search import SqlCandidateSearcher
from tests.fakes import FakeEmbeddingProvider

pytestmark = pytest.mark.integration

NO_FILTERS = SearchFilters()


def _vector(seed: int) -> tuple[float, ...]:
    """Nearly-orthogonal unit-ish vectors: identical seeds are identical,
    different seeds are distant — cosine ranking becomes deterministic."""
    return tuple(1.0 if i == seed % EMBEDDING_DIM else 0.0 for i in range(EMBEDDING_DIM))


class SearchRig:
    def __init__(self, factory: async_sessionmaker[AsyncSession], workspace_id: UUID) -> None:
        self.factory = factory
        self.workspace_id = workspace_id
        self.searcher = SqlCandidateSearcher(factory)

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.factory)

    async def seed_document(
        self,
        source: Source,
        path: str,
        texts: list[str],
        *,
        vector_seeds: list[int] | None = None,
        mime_type: str = "text/markdown",
        version_created_at: datetime | None = None,
        current: bool = True,
    ) -> tuple[Document, DocumentVersion, list[Chunk]]:
        document = Document(source_id=source.id, path=path, mime_type=mime_type)
        version = DocumentVersion(
            document_id=document.id,
            content_hash=ContentHash(f"{abs(hash(path)) % 16:x}" * 64),
            size_bytes=1,
            parser="markdown",
            created_at=(
                version_created_at if version_created_at is not None else datetime.now(UTC)
            ),
        )
        seeds = vector_seeds or list(range(len(texts)))
        chunks = [
            Chunk(
                document_version_id=version.id,
                ordinal=index,
                text=text_value,
                token_count=max(1, len(text_value.split())),
                content_hash=ContentHash(f"{(abs(hash(text_value)) % 15) + 1:x}" * 64),
                embedding=_vector(seed),
                embedding_model="test-model",
            )
            for index, (text_value, seed) in enumerate(zip(texts, seeds, strict=True))
        ]
        async with self.uow() as uow:
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.chunks.add_all(chunks)
            if current:
                # Flip AFTER the version row exists — the same order the
                # real pipeline uses; the FK forbids pointing at an
                # unflushed version (ADR-0011 insert ordering).
                document.set_current_version(version.id)
                await uow.documents.save(document)
            await uow.commit()
        return document, version, chunks


@pytest.fixture
async def rig(migrated_database_url: str) -> AsyncIterator[SearchRig]:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id = await ensure_default_workspace(factory, name="Search")
    yield SearchRig(factory, workspace_id)
    await engine.dispose()


async def _seed_source(rig: SearchRig, name: str = "Docs", uri: str = "/s") -> Source:
    source = Source(workspace_id=rig.workspace_id, kind="folder", name=name, uri=uri)
    async with rig.uow() as uow:
        await uow.sources.add(source)
        await uow.commit()
    return source


class TestVectorCandidates:
    async def test_ranked_by_cosine_distance(self, rig: SearchRig) -> None:
        source = await _seed_source(rig)
        _, _, chunks = await rig.seed_document(
            source, "a.md", ["alpha text", "beta text", "gamma text"], vector_seeds=[1, 2, 3]
        )
        results = await rig.searcher.find_vector_candidates(
            rig.workspace_id, _vector(2), limit=24, filters=NO_FILTERS
        )
        assert results[0].chunk_id == chunks[1].id  # exact vector match wins
        assert results[0].score == pytest.approx(1.0)
        assert len(results) == 3

    async def test_only_current_versions_are_searchable(self, rig: SearchRig) -> None:
        source = await _seed_source(rig)
        await rig.seed_document(
            source, "old.md", ["orphaned generation"], vector_seeds=[5], current=False
        )
        results = await rig.searcher.find_vector_candidates(
            rig.workspace_id, _vector(5), limit=24, filters=NO_FILTERS
        )
        assert results == []  # staged/orphaned chunk sets are invisible

    async def test_workspace_scoping_holds(self, rig: SearchRig) -> None:
        source = await _seed_source(rig)
        await rig.seed_document(source, "mine.md", ["private data"], vector_seeds=[7])
        other_workspace = await ensure_default_workspace(rig.factory, name="Other")
        results = await rig.searcher.find_vector_candidates(
            other_workspace, _vector(7), limit=24, filters=NO_FILTERS
        )
        assert results == []


class TestKeywordCandidates:
    async def test_fts_matches_carry_nonempty_mark_highlights(self, rig: SearchRig) -> None:
        source = await _seed_source(rig)
        await rig.seed_document(
            source,
            "contract.md",
            [
                "The notice period for termination is thirty days.",
                "Budget planning happens quarterly in this organization.",
            ],
        )
        results = await rig.searcher.find_keyword_candidates(
            rig.workspace_id, "notice period termination", limit=24, filters=NO_FILTERS
        )
        assert len(results) == 1
        top = results[0]
        assert top.highlight is not None and "<mark>" in top.highlight
        assert "notice" in top.highlight.lower()
        assert top.score > 0

    async def test_websearch_syntax_never_errors(self, rig: SearchRig) -> None:
        source = await _seed_source(rig)
        await rig.seed_document(source, "a.md", ["plain content"])
        # websearch_to_tsquery is total: quotes, operators, stray syntax
        for query in ('"notice period"', "cats OR dogs", "-negated", "a AND"):
            await rig.searcher.find_keyword_candidates(
                rig.workspace_id, query, limit=24, filters=NO_FILTERS
            )


class TestFilterScoping:
    """M06 acceptance: a filtered search never returns a chunk outside
    the filter — checked over generated filter combinations."""

    async def _seed_matrix(self, rig: SearchRig) -> dict[str, object]:
        source_a = await _seed_source(rig, "A", "/a")
        source_b = await _seed_source(rig, "B", "/b")
        old = datetime(2024, 1, 1, tzinfo=UTC)
        new = datetime.now(UTC) - timedelta(minutes=1)
        await rig.seed_document(
            source_a,
            "a1.md",
            ["shared keyword alpha"],
            mime_type="text/markdown",
            version_created_at=old,
        )
        await rig.seed_document(
            source_a,
            "a2.txt",
            ["shared keyword beta"],
            vector_seeds=[11],
            mime_type="text/plain",
            version_created_at=new,
        )
        await rig.seed_document(
            source_b,
            "b1.md",
            ["shared keyword gamma"],
            vector_seeds=[12],
            mime_type="text/markdown",
            version_created_at=new,
        )
        return {"a": source_a, "b": source_b, "old": old, "new": new}

    async def test_generated_filter_combinations_scope_both_modes(self, rig: SearchRig) -> None:
        seeded = await self._seed_matrix(rig)
        source_a = seeded["a"]
        assert isinstance(source_a, Source)
        cutoff = datetime(2025, 1, 1, tzinfo=UTC)
        combinations = [
            SearchFilters(source_ids=(source_a.id,)),
            SearchFilters(file_types=("md",)),
            SearchFilters(file_types=("txt",)),
            SearchFilters(modified_after=cutoff),
            SearchFilters(modified_before=cutoff),
            SearchFilters(source_ids=(source_a.id,), file_types=("md",)),
            SearchFilters(source_ids=(source_a.id,), modified_after=cutoff),
            SearchFilters(
                source_ids=(source_a.id,),
                file_types=("txt",),
                modified_after=cutoff,
            ),
        ]
        async with rig.uow() as uow:
            documents = {
                d.id: d
                for s in (seeded["a"], seeded["b"])
                if isinstance(s, Source)
                for d in await uow.documents.list_for_source(rig.workspace_id, s.id)
            }
            version_times = {}
            for document in documents.values():
                if document.current_version_id is not None:
                    version = await uow.document_versions.get(
                        rig.workspace_id, document.current_version_id
                    )
                    assert version is not None
                    version_times[document.id] = version.created_at
        for filters in combinations:
            keyword = await rig.searcher.find_keyword_candidates(
                rig.workspace_id, "shared keyword", limit=24, filters=filters
            )
            vector = await rig.searcher.find_vector_candidates(
                rig.workspace_id, _vector(11), limit=24, filters=filters
            )
            for candidate in [*keyword, *vector]:
                document = documents[candidate.document_id]
                if filters.source_ids:
                    assert document.source_id in filters.source_ids, filters
                if filters.file_types:
                    suffix = document.path.rsplit(".", 1)[-1].lower()
                    assert suffix in filters.file_types, filters
                if filters.modified_after is not None:
                    assert version_times[document.id] >= filters.modified_after, filters
                if filters.modified_before is not None:
                    assert version_times[document.id] <= filters.modified_before, filters


class TestHybridEndToEnd:
    async def test_query_matching_a_chunk_wins_both_modes(self, rig: SearchRig) -> None:
        provider = FakeEmbeddingProvider(dimensions=EMBEDDING_DIM)
        source = await _seed_source(rig)
        target_text = "The reimbursement policy covers travel and equipment."
        decoy_text = "Unrelated notes about gardening and compost."
        # Give chunks the provider's own vectors so query==text aligns modes.
        document = Document(source_id=source.id, path="policy.md", mime_type="text/markdown")
        version = DocumentVersion(
            document_id=document.id, content_hash=ContentHash("a" * 64), size_bytes=1, parser="md"
        )
        vectors = await provider.embed_documents([target_text, decoy_text])
        chunks = [
            Chunk(
                document_version_id=version.id,
                ordinal=index,
                text=text_value,
                token_count=len(text_value.split()),
                content_hash=ContentHash(f"{index + 1:x}" * 64),
                embedding=vectors[index],
                embedding_model=provider.model,
            )
            for index, text_value in enumerate([target_text, decoy_text])
        ]
        async with rig.uow() as uow:
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.chunks.add_all(chunks)
            document.set_current_version(version.id)
            await uow.documents.save(document)
            await uow.commit()

        search = HybridSearch(rig.searcher, provider)
        results = await search.execute(rig.workspace_id, target_text)
        assert results
        top = results[0]
        assert top.chunk_id == chunks[0].id
        assert top.vector_rank == 1
        assert top.keyword_rank == 1
        assert top.highlight is not None and "<mark>" in top.highlight
        assert top.document_id == document.id
