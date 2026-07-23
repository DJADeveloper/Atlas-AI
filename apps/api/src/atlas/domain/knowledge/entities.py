"""Knowledge-context entities.

Aggregate boundaries per `docs/10-domain-model.md`: `Source` and `Document`
are aggregate roots; `DocumentVersion` and `Chunk` are immutable and owned
by their document/version; `IngestionJob` is a small state-machine aggregate
of its own. Entities raise domain errors (`atlas.shared.errors`) on invariant
violations — never framework exceptions.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from atlas.domain.knowledge.values import (
    ContentHash,
    IngestionStage,
    IngestionState,
    SourceKind,
    SourceStatus,
)
from atlas.shared.clock import utc_now
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7

_JOB_TRANSITIONS: dict[IngestionState, tuple[IngestionState, ...]] = {
    "pending": ("running", "skipped"),
    "running": ("succeeded", "failed", "skipped"),
    "failed": ("pending",),  # retry re-queues
    "succeeded": (),
    "skipped": (),
}


@dataclass(slots=True, kw_only=True)
class Source:
    """A registered origin of content: a watched folder now, connectors at M22."""

    id: UUID = field(default_factory=uuid7)
    workspace_id: UUID
    kind: SourceKind
    name: str
    uri: str
    config: dict[str, object] = field(default_factory=dict)
    status: SourceStatus = "active"
    last_indexed_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationFailed("source name must not be empty")
        if not self.uri.strip():
            raise ValidationFailed("source uri must not be empty")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, *, now: datetime | None = None) -> None:
        if self.is_deleted:
            raise Conflict("source is already deleted")
        self.deleted_at = now if now is not None else utc_now()

    def mark_indexed(self, *, now: datetime | None = None) -> None:
        self.last_indexed_at = now if now is not None else utc_now()


@dataclass(slots=True, kw_only=True)
class Document:
    """A logical item within a Source, identified by a stable relative path."""

    id: UUID = field(default_factory=uuid7)
    source_id: UUID
    path: str
    title: str | None = None
    mime_type: str | None = None
    current_version_id: UUID | None = None
    deleted_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValidationFailed("document path must not be empty")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def set_current_version(self, version_id: UUID) -> None:
        """Atomic current-pointer flip; the old version's rows stay for citations."""
        self.current_version_id = version_id

    def soft_delete(self, *, now: datetime | None = None) -> None:
        if self.is_deleted:
            raise Conflict("document is already deleted")
        self.deleted_at = now if now is not None else utc_now()


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentVersion:
    """Immutable snapshot of a document's content, keyed by content hash."""

    id: UUID = field(default_factory=uuid7)
    document_id: UUID
    content_hash: ContentHash
    size_bytes: int
    parser: str
    meta: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValidationFailed("size_bytes must be >= 0")
        if not self.parser.strip():
            raise ValidationFailed("parser must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class Chunk:
    """A retrievable span of a DocumentVersion. Immutable; re-chunking a
    document produces a new version with new chunk rows.

    ``content_hash`` digests the exact *embedded* text (breadcrumb
    prefix + display text) and keys the embedding cache with
    ``embedding_model`` (M05). A chunk is staged unembedded by the chunk
    stage and completed by :meth:`with_embedding` at the embed stage —
    only fully embedded chunk sets ever become a document's current
    version (the atomic swap, docs/21 §3).
    """

    id: UUID = field(default_factory=uuid7)
    document_version_id: UUID
    ordinal: int
    text: str
    token_count: int
    content_hash: ContentHash
    heading_path: tuple[str, ...] = ()
    meta: dict[str, object] = field(default_factory=dict)
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValidationFailed("chunk ordinal must be >= 0")
        if not self.text:
            raise ValidationFailed("chunk text must not be empty")
        if self.token_count <= 0:
            raise ValidationFailed("token_count must be positive")
        if (self.embedding is None) != (self.embedding_model is None):
            raise ValidationFailed("embedding and embedding_model are set together")
        if self.embedding is not None and not self.embedding:
            raise ValidationFailed("embedding must not be empty")
        if self.embedding_model is not None and not self.embedding_model.strip():
            raise ValidationFailed("embedding_model must not be empty")

    @property
    def is_embedded(self) -> bool:
        return self.embedding is not None

    def with_embedding(self, vector: tuple[float, ...], model: str) -> "Chunk":
        """Complete a staged chunk; immutability makes this a new value."""
        return replace(self, embedding=vector, embedding_model=model)


MAX_INGESTION_ATTEMPTS = 3


@dataclass(slots=True, kw_only=True)
class IngestionJob:
    """One tracked unit of parse→chunk→embed work (states per spine §6).

    ``content_hash`` is the idempotency key with ``document_id`` (M04):
    at most one non-terminal job may exist per (document, content) pair —
    enforced by a partial unique index, so watcher/reindex races collapse
    into a single job instead of duplicate work.
    """

    id: UUID = field(default_factory=uuid7)
    source_id: UUID
    document_id: UUID | None = None
    content_hash: str | None = None
    state: IngestionState = "pending"
    stage: IngestionStage = "parse"
    attempts: int = 0
    error: str | None = None
    trace_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)

    @property
    def is_dead_lettered(self) -> bool:
        """Failed with retries exhausted: terminal, surfaced by the API,
        replayable only by explicit reindex (M04 dead-letter detail)."""
        return self.state == "failed" and self.attempts >= MAX_INGESTION_ATTEMPTS

    @property
    def can_retry(self) -> bool:
        return self.state == "failed" and self.attempts < MAX_INGESTION_ATTEMPTS

    def _transition(self, target: IngestionState) -> None:
        if target not in _JOB_TRANSITIONS[self.state]:
            raise Conflict(f"illegal ingestion transition {self.state} -> {target}")
        self.state = target

    def advance_stage(self, target: IngestionStage) -> None:
        """Move the job forward through the pipeline (docs/21 §3).

        Forward-only; re-entering the current stage is legal because
        Celery delivery is at-least-once and stages are idempotent.
        """
        stages: tuple[IngestionStage, ...] = ("parse", "chunk", "embed", "index")
        if stages.index(target) < stages.index(self.stage):
            raise Conflict(f"ingestion stage cannot move back: {self.stage} -> {target}")
        self.stage = target

    def start(self, *, trace_id: str | None = None, now: datetime | None = None) -> None:
        self._transition("running")
        self.attempts += 1
        self.trace_id = trace_id if trace_id is not None else self.trace_id
        self.started_at = now if now is not None else utc_now()

    def succeed(self, *, now: datetime | None = None) -> None:
        self._transition("succeeded")
        self.finished_at = now if now is not None else utc_now()

    def fail(self, error: str, *, now: datetime | None = None) -> None:
        self._transition("failed")
        self.error = error
        self.finished_at = now if now is not None else utc_now()

    def skip(self, reason: str, *, now: datetime | None = None) -> None:
        self._transition("skipped")
        self.error = reason
        self.finished_at = now if now is not None else utc_now()

    def retry(self) -> None:
        """Re-queue a failed job; attempts are preserved for backoff policy."""
        self._transition("pending")
        self.started_at = None
        self.finished_at = None
