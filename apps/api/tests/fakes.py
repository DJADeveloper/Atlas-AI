"""In-memory implementations of the persistence and file ports.

Used by application-layer unit tests (L1 of the pyramid): use cases run
against these with zero I/O. They honor the same contracts the SQL
adapters prove in integration tests — workspace scoping included — so a
use case green here and against Postgres is green for the same reasons.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from types import TracebackType
from uuid import UUID

from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.files import FileStat
from atlas.domain.knowledge.values import IngestionState
from atlas.shared.errors import NotFound


@dataclass
class FakeState:
    """Shared backing store, surviving across UoW instances like a DB."""

    workspaces: set[UUID] = field(default_factory=set)
    sources: dict[UUID, Source] = field(default_factory=dict)
    documents: dict[UUID, Document] = field(default_factory=dict)
    versions: dict[UUID, DocumentVersion] = field(default_factory=dict)
    chunks: dict[UUID, Chunk] = field(default_factory=dict)
    jobs: dict[UUID, IngestionJob] = field(default_factory=dict)

    def workspace_of_source(self, source_id: UUID) -> UUID | None:
        source = self.sources.get(source_id)
        return source.workspace_id if source else None


@dataclass
class FakeSourceRepository:
    state: FakeState
    pending: dict[UUID, Source] = field(default_factory=dict)

    async def add(self, source: Source) -> None:
        self.pending[source.id] = replace(source)

    async def save(self, source: Source) -> None:
        stored = self.state.sources.get(source.id) or self.pending.get(source.id)
        if stored is None or stored.workspace_id != source.workspace_id:
            raise NotFound(f"source {source.id} not found in its workspace")
        self.pending[source.id] = replace(source)

    def _visible(self, workspace_id: UUID) -> list[Source]:
        merged = {**self.state.sources, **self.pending}
        return [s for s in merged.values() if s.workspace_id == workspace_id and not s.is_deleted]

    async def get(self, workspace_id: UUID, source_id: UUID) -> Source | None:
        return next((replace(s) for s in self._visible(workspace_id) if s.id == source_id), None)

    async def get_by_uri(self, workspace_id: UUID, uri: str) -> Source | None:
        return next((replace(s) for s in self._visible(workspace_id) if s.uri == uri), None)

    async def list_active(self, workspace_id: UUID) -> list[Source]:
        return [replace(s) for s in self._visible(workspace_id) if s.status == "active"]


@dataclass
class FakeDocumentRepository:
    state: FakeState
    pending: dict[UUID, Document] = field(default_factory=dict)

    async def add(self, document: Document) -> None:
        self.pending[document.id] = replace(document)

    async def save(self, document: Document) -> None:
        if document.id not in self.state.documents and document.id not in self.pending:
            raise NotFound(f"document {document.id} not found")
        self.pending[document.id] = replace(document)

    def _visible(self, workspace_id: UUID) -> list[Document]:
        merged = {**self.state.documents, **self.pending}
        return [
            d
            for d in merged.values()
            if self.state.workspace_of_source(d.source_id) == workspace_id and not d.is_deleted
        ]

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        return next((replace(d) for d in self._visible(workspace_id) if d.id == document_id), None)

    async def get_by_path(self, workspace_id: UUID, source_id: UUID, path: str) -> Document | None:
        return next(
            (
                replace(d)
                for d in self._visible(workspace_id)
                if d.source_id == source_id and d.path == path
            ),
            None,
        )

    async def list_for_source(self, workspace_id: UUID, source_id: UUID) -> list[Document]:
        return sorted(
            (replace(d) for d in self._visible(workspace_id) if d.source_id == source_id),
            key=lambda d: d.path,
        )


@dataclass
class FakeDocumentVersionRepository:
    state: FakeState
    documents: FakeDocumentRepository
    pending: dict[UUID, DocumentVersion] = field(default_factory=dict)

    async def add(self, version: DocumentVersion) -> None:
        self.pending[version.id] = version  # frozen dataclass: safe to share

    def _visible(self, workspace_id: UUID) -> list[DocumentVersion]:
        merged = {**self.state.versions, **self.pending}
        docs = {
            d.id
            for d in {**self.state.documents, **self.documents.pending}.values()
            if self.state.workspace_of_source(d.source_id) == workspace_id
        }
        return [v for v in merged.values() if v.document_id in docs]

    async def get(self, workspace_id: UUID, version_id: UUID) -> DocumentVersion | None:
        return next((v for v in self._visible(workspace_id) if v.id == version_id), None)

    async def list_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> list[DocumentVersion]:
        return sorted(
            (v for v in self._visible(workspace_id) if v.document_id == document_id),
            key=lambda v: v.created_at,
        )


@dataclass
class FakeChunkRepository:
    state: FakeState
    pending: dict[UUID, Chunk] = field(default_factory=dict)

    async def add_all(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            self.pending[chunk.id] = chunk

    async def list_for_version(self, workspace_id: UUID, version_id: UUID) -> list[Chunk]:
        merged = {**self.state.chunks, **self.pending}
        return sorted(
            (c for c in merged.values() if c.document_version_id == version_id),
            key=lambda c: c.ordinal,
        )

    async def count_for_version(self, workspace_id: UUID, version_id: UUID) -> int:
        return len(await self.list_for_version(workspace_id, version_id))


@dataclass
class FakeIngestionJobRepository:
    state: FakeState
    pending: dict[UUID, IngestionJob] = field(default_factory=dict)

    def _merged(self) -> dict[UUID, IngestionJob]:
        return {**self.state.jobs, **self.pending}

    async def add(self, job: IngestionJob) -> None:
        self.pending[job.id] = replace(job)

    async def try_add(self, job: IngestionJob) -> bool:
        for existing in self._merged().values():
            if (
                existing.document_id == job.document_id
                and existing.content_hash == job.content_hash
                and existing.state in ("pending", "running")
            ):
                return False
        self.pending[job.id] = replace(job)
        return True

    async def save(self, job: IngestionJob) -> None:
        if job.id not in self._merged():
            raise NotFound(f"ingestion job {job.id} not found")
        self.pending[job.id] = replace(job)

    def _visible(self, workspace_id: UUID) -> list[IngestionJob]:
        return [
            j
            for j in self._merged().values()
            if self.state.workspace_of_source(j.source_id) == workspace_id
        ]

    async def get(self, workspace_id: UUID, job_id: UUID) -> IngestionJob | None:
        return next((replace(j) for j in self._visible(workspace_id) if j.id == job_id), None)

    async def list_by_state(
        self, workspace_id: UUID, state: IngestionState, limit: int = 100
    ) -> list[IngestionJob]:
        matching = [replace(j) for j in self._visible(workspace_id) if j.state == state]
        return sorted(matching, key=lambda j: j.created_at)[:limit]


class FakeUnitOfWork:
    """Commit merges pending into shared state; exit without commit discards."""

    sources: FakeSourceRepository
    documents: FakeDocumentRepository
    document_versions: FakeDocumentVersionRepository
    chunks: FakeChunkRepository
    ingestion_jobs: FakeIngestionJobRepository

    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.sources = FakeSourceRepository(self._state)
        self.documents = FakeDocumentRepository(self._state)
        self.document_versions = FakeDocumentVersionRepository(self._state, self.documents)
        self.chunks = FakeChunkRepository(self._state)
        self.ingestion_jobs = FakeIngestionJobRepository(self._state)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self._state.sources.update(self.sources.pending)
        self._state.documents.update(self.documents.pending)
        self._state.versions.update(self.document_versions.pending)
        self._state.chunks.update(self.chunks.pending)
        self._state.jobs.update(self.ingestion_jobs.pending)

    async def rollback(self) -> None:
        self.sources.pending.clear()
        self.documents.pending.clear()
        self.document_versions.pending.clear()
        self.chunks.pending.clear()
        self.ingestion_jobs.pending.clear()


class FakeFileStore:
    """Dict-backed SourceFileStore: {source_uri: {relative_path: bytes}}."""

    def __init__(self, trees: dict[str, dict[str, bytes]] | None = None) -> None:
        self.trees: dict[str, dict[str, bytes]] = trees or {}

    def exists(self, source_uri: str) -> bool:
        return source_uri in self.trees

    def scan(self, source_uri: str, ignore_globs: tuple[str, ...]) -> list[str]:
        return sorted(self.trees.get(source_uri, {}))

    def stat(self, source_uri: str, relative_path: str) -> FileStat | None:
        raw = self.trees.get(source_uri, {}).get(relative_path)
        if raw is None:
            return None
        return FileStat(hashlib.sha256(raw).hexdigest(), len(raw))

    def read(self, source_uri: str, relative_path: str) -> bytes | None:
        return self.trees.get(source_uri, {}).get(relative_path)


class FakeDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[tuple[UUID, UUID, str | None]] = []

    def dispatch(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        self.dispatched.append((workspace_id, job_id, trace_id))
