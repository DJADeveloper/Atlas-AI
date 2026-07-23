"""Repository ports for the knowledge context.

Contract (M03 acceptance, stricter than the doc-10 sketches and therefore
controlling): every public query method — names starting with get/list/
find/count — takes ``workspace_id`` as its first parameter, and
implementations enforce the scope in SQL. A CI introspection test
(`tests/unit/test_port_scoping.py`) fails the build if a port drifts.

Retrieval methods (`similar`, `keyword`) join `ChunkRepository` at M06.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.values import IngestionState


class SourceRepository(Protocol):
    async def add(self, source: Source) -> None: ...
    async def save(self, source: Source) -> None: ...
    async def get(self, workspace_id: UUID, source_id: UUID) -> Source | None: ...
    async def get_by_uri(self, workspace_id: UUID, uri: str) -> Source | None: ...
    async def list_active(self, workspace_id: UUID) -> list[Source]: ...


class DocumentRepository(Protocol):
    async def add(self, document: Document) -> None: ...
    async def save(self, document: Document) -> None: ...
    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None: ...
    async def get_by_path(
        self, workspace_id: UUID, source_id: UUID, path: str
    ) -> Document | None: ...
    async def list_for_source(self, workspace_id: UUID, source_id: UUID) -> list[Document]: ...


class DocumentVersionRepository(Protocol):
    async def add(self, version: DocumentVersion) -> None: ...
    async def get(self, workspace_id: UUID, version_id: UUID) -> DocumentVersion | None: ...
    async def list_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> list[DocumentVersion]: ...


class ChunkRepository(Protocol):
    async def add_all(self, chunks: Sequence[Chunk]) -> None: ...
    async def list_for_version(self, workspace_id: UUID, version_id: UUID) -> list[Chunk]: ...
    async def count_for_version(self, workspace_id: UUID, version_id: UUID) -> int: ...


class IngestionJobRepository(Protocol):
    async def add(self, job: IngestionJob) -> None: ...
    async def try_add(self, job: IngestionJob) -> bool:
        """Race-safe insert honoring the active-job idempotency key
        (document_id, content_hash): returns False when an equivalent
        non-terminal job already exists, without poisoning the
        transaction (M04 duplicate-work mitigation)."""
        ...

    async def save(self, job: IngestionJob) -> None: ...
    async def get(self, workspace_id: UUID, job_id: UUID) -> IngestionJob | None: ...
    async def list_by_state(
        self, workspace_id: UUID, state: IngestionState, limit: int = 100
    ) -> list[IngestionJob]: ...
