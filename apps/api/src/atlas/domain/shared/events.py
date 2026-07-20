"""Domain events: facts that happened, named in past tense (doc 10).

Entities/use cases *collect* events; publishing is an application/
infrastructure concern that arrives with the first consumer (M04's
ingestion pipeline). Events are frozen, carry ids not object references,
and never leak ORM types.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from atlas.shared.clock import utc_now
from atlas.shared.ids import uuid7


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    event_id: UUID = field(default_factory=uuid7)
    occurred_at: datetime = field(default_factory=utc_now)
    workspace_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRegistered(DomainEvent):
    source_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentVersionAdded(DomainEvent):
    document_id: UUID
    version_id: UUID
    content_hash: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentIngested(DomainEvent):
    """Emitted by the M04 pipeline when a version is parsed and persisted."""

    document_id: UUID
    version_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkEmbedded(DomainEvent):
    """Emitted by the M05 pipeline when a version's chunk set is indexed."""

    version_id: UUID
    chunk_count: int
