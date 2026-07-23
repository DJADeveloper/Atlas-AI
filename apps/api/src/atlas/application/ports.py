"""Application-layer ports.

The Unit of Work owns the transaction boundary: one UoW per use-case
invocation, repositories bound to its transaction, explicit commit,
automatic rollback when the block exits on error (ADR-0002; docs/10 §ports).
Implemented by `atlas.infrastructure.persistence.uow`.

`IngestionDispatcher` is the seam to the task queue: use cases decide
WHAT to run, the adapter (Celery at M04, per ADR-0004) decides WHERE.
Dispatch always happens after commit — a worker must never receive a
job id whose row is still uncommitted.

`EmbeddingProvider` (ADR-0005) hides the embedding model behind a port:
the adapter owns task prefixes, batching, and transport; callers hand it
bare texts. Embeddings are local in every profile (docs/21 §6) — the
embed stage sees every byte of every indexed document.
"""

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol
from uuid import UUID

from atlas.domain.knowledge.ports import (
    ChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
    SourceRepository,
)


class IngestionDispatcher(Protocol):
    def dispatch(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        """Enqueue one ingestion attempt; trace_id rides along so worker
        logs correlate with the API request that caused the work."""
        ...

    def dispatch_embed(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        """Enqueue the embed stage for a job whose chunks are staged —
        its own queue, so an Ollama outage never re-parses a PDF."""
        ...


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str:
        """Stable model identifier recorded on chunks.embedding_model."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Embed chunk texts (document task prefix applied by the
        adapter); returns one vector per input, in order."""
        ...

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed a search query (query task prefix applied by the
        adapter). Used from M06's retrieval path."""
        ...


class UnitOfWork(Protocol):
    # Read-only properties, deliberately: consumers never assign
    # repositories, and covariance lets implementations expose their
    # concrete repository types (SQL and in-memory alike).
    @property
    def sources(self) -> SourceRepository: ...
    @property
    def documents(self) -> DocumentRepository: ...
    @property
    def document_versions(self) -> DocumentVersionRepository: ...
    @property
    def chunks(self) -> ChunkRepository: ...
    @property
    def ingestion_jobs(self) -> IngestionJobRepository: ...

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
