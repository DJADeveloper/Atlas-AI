"""Application-layer ports.

The Unit of Work owns the transaction boundary: one UoW per use-case
invocation, repositories bound to its transaction, explicit commit,
automatic rollback when the block exits on error (ADR-0002; docs/10 §ports).
Implemented by `atlas.infrastructure.persistence.uow`.
"""

from types import TracebackType
from typing import Protocol

from atlas.domain.knowledge.ports import (
    ChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
    SourceRepository,
)


class UnitOfWork(Protocol):
    sources: SourceRepository
    documents: DocumentRepository
    document_versions: DocumentVersionRepository
    chunks: ChunkRepository
    ingestion_jobs: IngestionJobRepository

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
