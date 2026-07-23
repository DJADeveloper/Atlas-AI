"""Ingestion throughput benchmark (M05: the number, not a feeling).

Measures the full parse → chunk → embed → swap pipeline over a
generated MD/TXT corpus and reports **docs/hour**, with the hardware
that produced the number alongside it — a throughput figure is honest
only next to its machine (docs/41 §benchmarks).

Usage (from apps/api):

    uv run python scripts/ingest_throughput.py --docs 200
    uv run python scripts/ingest_throughput.py --provider ollama --output bench.json

Spins a disposable pgvector Postgres via testcontainers unless
ATLAS_DATABASE_URL/--database-url points at one (it must be migrated or
empty; migrations run automatically). ``--provider ollama`` uses the
real local model — the nightly configuration; ``fake`` isolates
parse+chunk+storage cost.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from atlas.application.ingestion import DetectChanges, EmbedDocument, IngestDocument
from atlas.application.ports import EmbeddingProvider, IngestionDispatcher
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.migrations import run_migrations_sync
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.providers.ollama import OllamaEmbeddingProvider
from atlas.infrastructure.watcher.filesystem import LocalFileStore

MARKDOWN_SHARE = 0.7


class NullDispatcher:
    """The benchmark drives stages inline; dispatch is a no-op."""

    def dispatch(self, workspace_id: object, job_id: object, trace_id: object = None) -> None:
        return None

    def dispatch_embed(self, workspace_id: object, job_id: object, trace_id: object = None) -> None:
        return None


class CountingFakeProvider:
    """Deterministic 768d vectors; isolates non-model pipeline cost."""

    model = "bench-fake"

    def __init__(self) -> None:
        self.texts_embedded = 0

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple(digest[i % len(digest)] / 255.0 for i in range(768))

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        self.texts_embedded += len(texts)
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)


def write_corpus(root: Path, count: int) -> None:
    rng = random.Random(20260723)  # deterministic corpus: same bytes every run
    markdown_count = int(count * MARKDOWN_SHARE)
    for index in range(count):
        body = "\n\n".join(
            " ".join(f"w{rng.randint(0, 999)}" for _ in range(rng.randint(60, 140))) + "."
            for _ in range(rng.randint(4, 12))
        )
        if index < markdown_count:
            sections = "\n\n".join(
                f"## Section {index}.{n}\n\n{body}" for n in range(rng.randint(1, 3))
            )
            (root / f"doc-{index:05d}.md").write_text(f"# Benchmark doc {index}\n\n{sections}\n")
        else:
            (root / f"note-{index:05d}.txt").write_text(body + "\n")


def hardware() -> dict[str, object]:
    cpu_model = platform.processor() or ""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    memory_gb: float | None = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal"):
                memory_gb = round(int(line.split()[1]) / 1024 / 1024, 1)
                break
    return {
        "platform": platform.platform(),
        "cpu": cpu_model,
        "cores": os.cpu_count(),
        "memory_gb": memory_gb,
        "python": platform.python_version(),
    }


async def run_benchmark(
    database_url: str,
    corpus_dir: Path,
    docs: int,
    provider: EmbeddingProvider,
    dispatcher: IngestionDispatcher,
) -> dict[str, object]:
    write_corpus(corpus_dir, docs)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        workspace_id = await ensure_default_workspace(factory, name="Bench")
        registry = default_registry()

        def uow() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(factory)

        detect = DetectChanges(
            uow_factory=uow,
            file_store=LocalFileStore(),
            dispatcher=dispatcher,
            supports=registry.supports,
        )
        ingest = IngestDocument(
            uow_factory=uow,
            file_store=LocalFileStore(),
            parser_for=registry.parser_for,
            dispatcher=dispatcher,
        )
        embed = EmbedDocument(uow_factory=uow, provider=provider)

        source = Source(workspace_id=workspace_id, kind="folder", name="Bench", uri=str(corpus_dir))
        async with uow() as unit:
            await unit.sources.add(source)
            await unit.commit()

        started = time.perf_counter()
        report = await detect.execute(workspace_id, source.id, batch_id="bench-1")
        failures = 0
        for job_id in report.enqueued_job_ids:
            outcome = await ingest.execute(workspace_id, job_id)
            if outcome.result == "chunked":
                outcome = await embed.execute(workspace_id, job_id)
            if outcome.result != "succeeded":
                failures += 1
        elapsed = time.perf_counter() - started

        async with uow() as unit:
            chunk_total = 0
            for document in await unit.documents.list_for_source(workspace_id, source.id):
                if document.current_version_id is not None:
                    chunk_total += await unit.chunks.count_embedded_for_version(
                        workspace_id, document.current_version_id
                    )
        return {
            "documents": docs,
            "enqueued": len(report.enqueued_job_ids),
            "failures": failures,
            "chunks_embedded": chunk_total,
            "elapsed_seconds": round(elapsed, 2),
            "docs_per_hour": round(docs / elapsed * 3600) if elapsed > 0 else None,
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=int, default=200)
    parser.add_argument("--provider", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    provider: EmbeddingProvider = (
        OllamaEmbeddingProvider(args.ollama_url)
        if args.provider == "ollama"
        else CountingFakeProvider()
    )

    def execute(database_url: str) -> dict[str, object]:
        run_migrations_sync(database_url)
        with tempfile.TemporaryDirectory(prefix="atlas-bench-") as corpus:
            return asyncio.run(
                run_benchmark(database_url, Path(corpus), args.docs, provider, NullDispatcher())
            )

    if args.database_url:
        result = execute(args.database_url)
    else:
        with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as container:
            result = execute(container.get_connection_url())

    result["provider"] = args.provider
    result["hardware"] = hardware()
    payload = json.dumps(result, indent=2)
    print(payload)
    if args.output:
        args.output.write_text(payload + "\n")
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
