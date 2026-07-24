"""Golden-dataset retrieval evaluation (M06: Recall@8 and MRR).

Ingests `evals/fixtures/corpus/` through the real pipeline, runs every
`evals/retrieval/golden_v1.jsonl` query through HybridSearch (spine §10),
and reports Recall@8 / MRR. A result counts as relevant when it comes
from the labeled document AND contains the labeled span
(whitespace-normalized), so labels survive re-chunking.

    uv run python scripts/retrieval_eval.py --provider ollama --output eval.json

Numbers are meaningful only with real embeddings (`--provider ollama`,
the nightly configuration); the fake provider proves mechanics. The M11
regression gate will enforce thresholds; until then this reports.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from atlas.application.ingestion import DetectChanges, EmbedDocument, IngestDocument
from atlas.application.ports import EmbeddingProvider
from atlas.application.retrieval import HybridSearch
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.migrations import run_migrations_sync
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.providers.ollama import OllamaEmbeddingProvider
from atlas.infrastructure.search import SqlCandidateSearcher
from atlas.infrastructure.watcher.filesystem import LocalFileStore
from atlas.rag import FUSED_RESULT_LIMIT

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "evals" / "fixtures" / "corpus"
GOLDEN_PATH = REPO_ROOT / "evals" / "retrieval" / "golden_v1.jsonl"


class _HashProvider:
    """Deterministic vectors; useful only to prove the harness runs."""

    model = "eval-fake"

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple(digest[i % len(digest)] / 255.0 for i in range(768))

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)


@dataclass(frozen=True, slots=True)
class GoldenQuery:
    id: str
    kind: str
    query: str
    relevant: tuple[tuple[str, str], ...]  # (path, must_contain)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def load_golden() -> list[GoldenQuery]:
    queries = []
    for line in GOLDEN_PATH.read_text().splitlines():
        raw = json.loads(line)
        queries.append(
            GoldenQuery(
                id=raw["id"],
                kind=raw["kind"],
                query=raw["query"],
                relevant=tuple((r["path"], r["must_contain"]) for r in raw["relevant"]),
            )
        )
    return queries


class _NullDispatcher:
    """Stages run inline here; queue dispatch is a no-op."""

    def dispatch(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        return None

    def dispatch_embed(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        return None


async def _ingest_corpus(
    factory: async_sessionmaker[AsyncSession], workspace_id: UUID, provider: EmbeddingProvider
) -> dict[UUID, str]:
    """Run the real pipeline over the corpus; returns document_id → path."""
    registry = default_registry()

    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(factory)

    detect = DetectChanges(
        uow_factory=uow,
        file_store=LocalFileStore(),
        dispatcher=_NullDispatcher(),
        supports=registry.supports,
    )
    ingest = IngestDocument(
        uow_factory=uow,
        file_store=LocalFileStore(),
        parser_for=registry.parser_for,
        dispatcher=_NullDispatcher(),
    )
    embed = EmbedDocument(uow_factory=uow, provider=provider)

    source = Source(workspace_id=workspace_id, kind="folder", name="Golden", uri=str(CORPUS_DIR))
    async with uow() as unit:
        await unit.sources.add(source)
        await unit.commit()
    report = await detect.execute(workspace_id, source.id, batch_id="golden-eval")
    for job_id in report.enqueued_job_ids:
        outcome = await ingest.execute(workspace_id, job_id)
        if outcome.result == "chunked":
            outcome = await embed.execute(workspace_id, job_id)
        if outcome.result != "succeeded":
            raise RuntimeError(f"corpus ingestion failed: {outcome}")
    async with uow() as unit:
        documents = await unit.documents.list_for_source(workspace_id, source.id)
    return {d.id: d.path for d in documents}


async def evaluate(database_url: str, provider: EmbeddingProvider) -> dict[str, object]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        workspace_id = await ensure_default_workspace(factory, name="Eval")
        paths = await _ingest_corpus(factory, workspace_id, provider)
        search = HybridSearch(SqlCandidateSearcher(factory), provider)

        queries = load_golden()
        hits = 0
        reciprocal_ranks: list[float] = []
        by_kind: dict[str, list[float]] = {}
        misses: list[str] = []
        for golden in queries:
            results = await search.execute(workspace_id, golden.query)
            first_relevant_rank = 0
            for rank, result in enumerate(results, start=1):
                result_path = paths.get(result.document_id, "")
                matched = any(
                    result_path == path and _normalize(span) in _normalize(result.text)
                    for path, span in golden.relevant
                )
                if matched:
                    first_relevant_rank = rank
                    break
            if first_relevant_rank:
                hits += 1
                reciprocal_ranks.append(1.0 / first_relevant_rank)
            else:
                reciprocal_ranks.append(0.0)
                misses.append(golden.id)
            by_kind.setdefault(golden.kind, []).append(reciprocal_ranks[-1])

        return {
            "queries": len(queries),
            "recall_at_8": round(hits / len(queries), 4),
            "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4),
            "by_kind_mrr": {
                kind: round(sum(values) / len(values), 4) for kind, values in by_kind.items()
            },
            "misses": misses,
            "top_k": FUSED_RESULT_LIMIT,
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
        return asyncio.run(evaluate(url, provider))

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
