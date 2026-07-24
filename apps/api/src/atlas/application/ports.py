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
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from atlas.domain.conversation.ports import (
    CitationRepository,
    ConversationRepository,
    MessageRepository,
)
from atlas.domain.knowledge.ports import (
    ChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    IngestionJobRepository,
    SourceRepository,
)
from atlas.domain.memory.ports import MemoryRepository


class IngestionDispatcher(Protocol):
    def dispatch(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        """Enqueue one ingestion attempt; trace_id rides along so worker
        logs correlate with the API request that caused the work."""
        ...

    def dispatch_embed(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        """Enqueue the embed stage for a job whose chunks are staged —
        its own queue, so an Ollama outage never re-parses a PDF."""
        ...


class ChatDispatcher(Protocol):
    """Background chat work (M07): summarization folds old turns into
    the rolling summary; title generation names a fresh conversation
    after its first exchange. Both are fire-and-forget — a lost task
    costs a nicety, never data (the originals stay in `messages`)."""

    def dispatch_summarize(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None: ...

    def dispatch_title(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None: ...


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


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Retrieval filters, applied in SQL so they provably scope (M06).

    Field names follow docs/12 §4.4: ``file_types`` are extensions
    (``md``, ``pdf`` — how users think about files), and the modified
    bounds apply to the *current version's* creation time — "changed
    since last week", not "file first seen". The project filter joins
    at M13 when projects exist.
    """

    source_ids: tuple[UUID, ...] = ()
    file_types: tuple[str, ...] = ()
    modified_after: datetime | None = None
    modified_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate from a single retrieval mode, rank implied by
    position; ``highlight`` is set for FTS matches (ts_headline)."""

    chunk_id: UUID
    document_id: UUID
    text: str
    score: float
    heading_path: tuple[str, ...] = ()
    highlight: str | None = None


class CandidateSearcher(Protocol):
    """Candidate generation over the index (spine §10). Named find_* and
    workspace_id-first to honor the scoping convention by inspection."""

    async def find_vector_candidates(
        self,
        workspace_id: UUID,
        embedding: Sequence[float],
        *,
        limit: int,
        filters: SearchFilters,
    ) -> list[Candidate]: ...

    async def find_keyword_candidates(
        self,
        workspace_id: UUID,
        query: str,
        *,
        limit: int,
        filters: SearchFilters,
    ) -> list[Candidate]: ...


class Reranker(Protocol):
    """Optional cross-encoder over the fused candidates (spine §10,
    off by default; a local bge-reranker-base adapter arrives when the
    quality numbers ask for it)."""

    async def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]: ...


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
    @property
    def conversations(self) -> ConversationRepository: ...
    @property
    def messages(self) -> MessageRepository: ...
    @property
    def citations(self) -> CitationRepository: ...
    @property
    def memories(self) -> MemoryRepository: ...

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
