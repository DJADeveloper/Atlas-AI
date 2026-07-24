"""Repository ports for the conversation context.

Same scoping contract as the knowledge context (M03): every query
method takes ``workspace_id`` first and implementations enforce it in
SQL — swept by the introspection test alongside the knowledge ports."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from atlas.domain.conversation.entities import Citation, Conversation, Message


class ConversationRepository(Protocol):
    async def add(self, conversation: Conversation) -> None: ...
    async def save(self, conversation: Conversation) -> None: ...
    async def get(self, workspace_id: UUID, conversation_id: UUID) -> Conversation | None: ...
    async def list_recent(self, workspace_id: UUID, *, limit: int = 50) -> list[Conversation]: ...


class MessageRepository(Protocol):
    async def add(self, message: Message) -> None: ...
    async def get(self, workspace_id: UUID, message_id: UUID) -> Message | None: ...
    async def list_for_conversation(
        self, workspace_id: UUID, conversation_id: UUID, *, limit: int = 500
    ) -> list[Message]:
        """Chronological (UUIDv7 PK order), oldest first."""
        ...

    async def count_for_conversation(self, workspace_id: UUID, conversation_id: UUID) -> int: ...

    async def latest_for_conversation(
        self, workspace_id: UUID, conversation_id: UUID
    ) -> Message | None:
        """Newest message (UUIDv7 max), for last_message_at summaries."""
        ...


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    """Read model for the API message shape (docs/12 §4.3): a citation
    joined to its chunk and document so the UI can render provenance
    without extra round trips."""

    marker: int
    chunk_id: UUID
    document_id: UUID
    document_title: str | None
    snippet: str
    score: float | None


class CitationRepository(Protocol):
    async def add_all(self, citations: list[Citation]) -> None: ...

    async def list_for_message(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        """Marker order (the [n] sequence as emitted)."""
        ...

    async def list_resolved_for_message(
        self, workspace_id: UUID, message_id: UUID
    ) -> list[ResolvedCitation]:
        """Marker order, joined to chunk text and document title."""
        ...
