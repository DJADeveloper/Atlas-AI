"""Memory entities, v1 (docs/22 sliced for M07; schema per docs/11 §2.5).

Typed long-lived memories with provenance: a memory can point at the
message it was learned from. `project_fact` stays in the kind vocabulary
but is unusable until projects arrive (M13) — the constraint that a
project fact needs a project cannot be satisfied before then."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from atlas.shared.clock import utc_now
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7

MemoryKind = Literal["preference", "project_fact", "decision", "entity", "episodic"]
MEMORY_KINDS: tuple[MemoryKind, ...] = get_args(MemoryKind)


@dataclass(slots=True, kw_only=True)
class Memory:
    id: UUID = field(default_factory=uuid7)
    workspace_id: UUID
    kind: MemoryKind
    content: str
    source_message_id: UUID | None = None  # provenance
    confidence: float | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValidationFailed("memory content must not be empty")
        if self.kind == "project_fact":
            raise ValidationFailed("project_fact memories require projects (M13)")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValidationFailed("confidence must be within [0, 1]")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else utc_now()) >= self.expires_at

    def revise(self, content: str, *, now: datetime | None = None) -> None:
        if not content.strip():
            raise ValidationFailed("memory content must not be empty")
        self.content = content
        self.updated_at = now if now is not None else utc_now()

    def soft_delete(self, *, now: datetime | None = None) -> None:
        if self.is_deleted:
            raise Conflict("memory is already deleted")
        self.deleted_at = now if now is not None else utc_now()
