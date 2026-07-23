"""Application-layer ports.

The Unit of Work owns the transaction boundary: one UoW per use-case
invocation, repositories bound to its transaction, explicit commit,
automatic rollback when the block exits on error (ADR-0002; docs/10 §ports).
Implemented by `atlas.infrastructure.persistence.uow`.

`IngestionDispatcher` is the seam to the task queue: use cases decide
WHAT to run, the adapter (Celery at M04, per ADR-0004) decides WHERE.
Dispatch always happens after commit — a worker must never receive a
job id whose row is still uncommitted.
"""

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
    def dispatch(self, workspace_id: UUID, job_id: UUID) -> None:
        """Enqueue one ingestion attempt for asynchronous execution."""
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
