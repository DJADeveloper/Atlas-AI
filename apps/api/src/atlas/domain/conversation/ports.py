"""Repository ports for the conversation context.

Same scoping contract as the knowledge context (M03): every query
method takes ``workspace_id`` first and implementations enforce it in
SQL — swept by the introspection test alongside the knowledge ports."""

from typing import Protocol
from uuid import UUID

from atlas.domain.conversation.entities import Conversation, Message


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
