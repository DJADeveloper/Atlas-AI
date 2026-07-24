"""Search latency harness (M06 acceptance: p95 < 500 ms over ≥ 200
requests against a 10,000-chunk index).

Seeds 10,000 embedded chunks (deterministic vectors — HNSW cost depends
on dimension and count, not semantics), then measures the full
HybridSearch path per request: query embedding, both candidate queries,
fusion. `--provider ollama` includes real query-embedding time (the
nightly configuration); the fake provider isolates database + fusion
cost.

    uv run python scripts/search_latency.py --provider ollama --output latency.json
"""

import argparse
import asyncio
import hashlib
import json
import random
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from atlas.application.ports import EmbeddingProvider
from atlas.application.retrieval import HybridSearch
from atlas.domain.knowledge.entities import Chunk, Document, DocumentVersion, Source
from atlas.domain.knowledge.values import ContentHash
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.migrations import run_migrations_sync
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.providers.ollama import OllamaEmbeddingProvider
from atlas.infrastructure.search import SqlCandidateSearcher

CHUNK_COUNT = 10_000
CHUNKS_PER_DOCUMENT = 20
REQUESTS = 200
DIMENSIONS = 768
TARGET_P95_MS = 500.0  # M06 acceptance ceiling

_WORDS = [
    "ledger",
    "deployment",
    "rollback",
    "incident",
    "retention",
    "budget",
    "vacation",
    "onboarding",
    "postgres",
    "index",
    "embedding",
    "retrieval",
    "semantic",
    "keyword",
    "policy",
    "security",
    "audit",
    "pricing",
    "customer",
    "telemetry",
    "backup",
    "encryption",
    "review",
    "interview",
    "alert",
]


class _HashProvider:
    model = "latency-fake"

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple(digest[i % len(digest)] / 255.0 for i in range(DIMENSIONS))

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)


def _sentence(rng: random.Random) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(rng.randint(8, 14))) + "."


async def _seed(factory: async_sessionmaker[AsyncSession], workspace_id: UUID) -> None:
    rng = random.Random(1106)
    source = Source(workspace_id=workspace_id, kind="folder", name="Latency", uri="/latency")
    async with SqlAlchemyUnitOfWork(factory) as uow:
        await uow.sources.add(source)
        await uow.commit()
    document_count = CHUNK_COUNT // CHUNKS_PER_DOCUMENT
    for doc_index in range(document_count):
        document = Document(
            source_id=source.id, path=f"doc-{doc_index:05d}.md", mime_type="text/markdown"
        )
        version = DocumentVersion(
            document_id=document.id,
            content_hash=ContentHash(f"{doc_index % 16:x}" * 64),
            size_bytes=1,
            parser="markdown",
        )
        chunks = [
            Chunk(
                document_version_id=version.id,
                ordinal=ordinal,
                text=" ".join(_sentence(rng) for _ in range(4)),
                token_count=40,
                content_hash=ContentHash(f"{(doc_index + ordinal) % 15 + 1:x}" * 64),
                embedding=tuple(rng.random() for _ in range(DIMENSIONS)),
                embedding_model="latency-fake",
            )
            for ordinal in range(CHUNKS_PER_DOCUMENT)
        ]
        async with SqlAlchemyUnitOfWork(factory) as uow:
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.chunks.add_all(chunks)
            document.set_current_version(version.id)  # flip after the version exists
            await uow.documents.save(document)
            await uow.commit()


async def measure(database_url: str, provider: EmbeddingProvider) -> dict[str, object]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rng = random.Random(2026)
    try:
        workspace_id = await ensure_default_workspace(factory, name="Latency")
        started_seed = time.perf_counter()
        await _seed(factory, workspace_id)
        seed_seconds = time.perf_counter() - started_seed

        search = HybridSearch(SqlCandidateSearcher(factory), provider)
        queries = [
            " ".join(rng.choice(_WORDS) for _ in range(rng.randint(2, 6))) for _ in range(REQUESTS)
        ]
        # Warm-up: connection pool, planner caches.
        for query in queries[:5]:
            await search.execute(workspace_id, query)

        durations_ms = []
        for query in queries:
            started = time.perf_counter()
            await search.execute(workspace_id, query)
            durations_ms.append((time.perf_counter() - started) * 1000)

        durations_ms.sort()
        return {
            "chunks": CHUNK_COUNT,
            "requests": REQUESTS,
            "seed_seconds": round(seed_seconds, 1),
            "p50_ms": round(statistics.median(durations_ms), 1),
            "p95_ms": round(durations_ms[int(len(durations_ms) * 0.95) - 1], 1),
            "p99_ms": round(durations_ms[int(len(durations_ms) * 0.99) - 1], 1),
            "max_ms": round(durations_ms[-1], 1),
            "target_p95_ms": TARGET_P95_MS,
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    provider: EmbeddingProvider = (
        OllamaEmbeddingProvider(args.ollama_url) if args.provider == "ollama" else _HashProvider()
    )

    def run(url: str) -> dict[str, object]:
        run_migrations_sync(url)
        return asyncio.run(measure(url, provider))

    if args.database_url:
        result = run(args.database_url)
    else:
        with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as container:
            result = run(container.get_connection_url())

    result["provider"] = args.provider
    payload = json.dumps(result, indent=2)
    print(payload)
    if args.output:
        args.output.write_text(payload + "\n")
    p95 = result["p95_ms"]
    return 0 if isinstance(p95, float) and p95 < TARGET_P95_MS else 1


if __name__ == "__main__":
    sys.exit(main())
