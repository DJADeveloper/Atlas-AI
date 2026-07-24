"""Conversation-context entities (docs/10; schema per docs/11 §2.4).

`Conversation` is a small mutable aggregate root; `Message` rows are
immutable — UUIDv7 PK order IS chronological order, which the history
query exploits. Cost/usage fields live on the message because "what did
this answer cost" is a product question, not telemetry (spine §12)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from atlas.shared.clock import utc_now
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7

MessageRole = Literal["user", "assistant", "tool", "system"]
MESSAGE_ROLES: tuple[MessageRole, ...] = ("user", "assistant", "tool", "system")

MAX_TITLE_LENGTH = 200


@dataclass(slots=True, kw_only=True)
class Conversation:
    id: UUID = field(default_factory=uuid7)
    workspace_id: UUID
    title: str | None = None
    # Rolling summary (docs/22 §2): folding, not forgetting — the full
    # history stays in `messages`. The watermark marks the last message
    # the summary covers; context assembly replays verbatim turns after
    # it. UUIDv7 ordering makes "after" a plain id comparison.
    summary: str | None = None
    summary_through_message_id: UUID | None = None
    deleted_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.title is not None and len(self.title) > MAX_TITLE_LENGTH:
            raise ValidationFailed(f"title exceeds {MAX_TITLE_LENGTH} characters")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def rename(self, title: str, *, now: datetime | None = None) -> None:
        if not title.strip():
            raise ValidationFailed("conversation title must not be empty")
        if len(title) > MAX_TITLE_LENGTH:
            raise ValidationFailed(f"title exceeds {MAX_TITLE_LENGTH} characters")
        self.title = title
        self.touch(now=now)

    def touch(self, *, now: datetime | None = None) -> None:
        """Bumps updated_at — the recency the conversation list sorts by."""
        self.updated_at = now if now is not None else utc_now()

    def fold_summary(self, summary: str, *, through_message_id: UUID) -> None:
        """Advance the rolling summary to a new watermark. Deliberately
        does NOT touch(): folding is background bookkeeping, and bumping
        updated_at would reorder the recency-sorted conversation list."""
        if not summary.strip():
            raise ValidationFailed("summary must not be empty")
        if (
            self.summary_through_message_id is not None
            and through_message_id.int <= self.summary_through_message_id.int
        ):
            raise Conflict("summary watermark must advance")
        self.summary = summary
        self.summary_through_message_id = through_message_id

    def soft_delete(self, *, now: datetime | None = None) -> None:
        if self.is_deleted:
            raise Conflict("conversation is already deleted")
        self.deleted_at = now if now is not None else utc_now()


@dataclass(frozen=True, slots=True, kw_only=True)
class Message:
    """One immutable turn. Assistant messages carry provider/model/usage
    accounting (M07 acceptance: never a null cost on a persisted answer
    — enforced by `record_usage`'s presence, checked in tests)."""

    id: UUID = field(default_factory=uuid7)
    conversation_id: UUID
    role: MessageRole
    content: str
    abstained: bool = False  # grounded-or-silent, made queryable (M08 flips it)
    model: str | None = None
    provider: str | None = None
    prompt_version: str | None = None  # text until the M08 registry adds the FK
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    trace_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.content and self.role != "assistant":
            raise ValidationFailed("message content must not be empty")
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("latency_ms", self.latency_ms),
        ):
            if value is not None and value < 0:
                raise ValidationFailed(f"{name} must be >= 0")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValidationFailed("cost_usd must be >= 0")
