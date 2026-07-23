"""M05 acceptance: the HNSW index exists and the planner uses it.

Inserts enough embedded chunks that a sequential scan plus sort is
clearly worse than the index path, then asserts EXPLAIN chooses
``chunks_embedding_hnsw`` for the cosine top-k query — the exact query
shape M06 retrieval will issue.
"""

import random

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas.domain.knowledge.entities import Chunk, Document, DocumentVersion, Source
from atlas.domain.knowledge.values import ContentHash
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.tables import EMBEDDING_DIM
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

CHUNK_COUNT = 1200


def _vector(rng: random.Random) -> tuple[float, ...]:
    return tuple(rng.random() for _ in range(EMBEDDING_DIM))


async def test_cosine_top_k_uses_the_hnsw_index(migrated_database_url: str) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rng = random.Random(7)
    try:
        workspace_id = await ensure_default_workspace(factory, name="Hnsw")
        source = Source(workspace_id=workspace_id, kind="folder", name="H", uri="/hnsw")
        document = Document(source_id=source.id, path="h.md")
        version = DocumentVersion(
            document_id=document.id, content_hash=ContentHash("a" * 64), size_bytes=1, parser="md"
        )
        chunks = [
            Chunk(
                document_version_id=version.id,
                ordinal=index,
                text=f"span {index}",
                token_count=2,
                content_hash=ContentHash(f"{index:064x}"),
                embedding=_vector(rng),
                embedding_model="nomic-embed-text",
            )
            for index in range(CHUNK_COUNT)
        ]
        async with SqlAlchemyUnitOfWork(factory) as uow:
            await uow.sources.add(source)
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.chunks.add_all(chunks)
            await uow.commit()

        probe = "[" + ",".join(str(component) for component in _vector(rng)) + "]"
        async with factory() as session:
            await session.execute(text("ANALYZE chunks"))
            plan_rows = await session.execute(
                text(
                    "EXPLAIN SELECT id FROM chunks "
                    "ORDER BY embedding <=> CAST(:probe AS vector) LIMIT 8"
                ),
                {"probe": probe},
            )
            plan = "\n".join(row[0] for row in plan_rows)
        assert "chunks_embedding_hnsw" in plan, f"planner ignored the index:\n{plan}"
        assert "Index Scan" in plan
    finally:
        await engine.dispose()
