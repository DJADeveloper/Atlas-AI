"""Memory use cases, v1 (docs/22 sliced for M07).

`RememberFact` covers the explicit path only — "remember that I…" via
API/UI activates immediately (docs/22 §5). The extraction pipeline
that *proposes* memories from conversation arrives at M13; nothing
becomes known merely by having been said.

`RecallMemories` ranking, v1: preferences are standing orders and are
ALWAYS included first, most-recently-updated first (§5 read path);
other kinds rank by lexical word overlap against the current turn with
recency as the tie-break. Postgres FTS ranking replaces the lexical
scorer when memory grows its own retrieval quality bar (M13) — the
use-case seam stays identical.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from atlas.application.ports import UnitOfWork
from atlas.domain.memory.entities import Memory, MemoryKind
from atlas.shared.errors import NotFound

RECALL_LIMIT = 12

_WORDS = re.compile(r"\w+")


def _words(text: str) -> set[str]:
    return {word.lower() for word in _WORDS.findall(text)}


def rank_memories(memories: list[Memory], query: str, *, limit: int = RECALL_LIMIT) -> list[Memory]:
    """Pure ranking, shared by RecallMemories and the chat context
    build: preferences first (standing orders, newest-updated wins the
    budget per docs/22 §5), then lexical overlap with recency."""
    preferences = [m for m in memories if m.kind == "preference"]
    preferences.sort(key=lambda m: m.updated_at, reverse=True)
    query_words = _words(query)
    others = [m for m in memories if m.kind != "preference"]
    others.sort(
        key=lambda m: (len(query_words & _words(m.content)), m.updated_at),
        reverse=True,
    )
    return (preferences + others)[:limit]


@dataclass(frozen=True)
class RememberFact:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(
        self,
        workspace_id: UUID,
        kind: MemoryKind,
        content: str,
        *,
        source_message_id: UUID | None = None,
        confidence: float | None = None,
        expires_at: datetime | None = None,
    ) -> Memory:
        memory = Memory(
            workspace_id=workspace_id,
            kind=kind,
            content=content,
            source_message_id=source_message_id,
            confidence=confidence,
            expires_at=expires_at,
        )
        async with self.uow_factory() as uow:
            await uow.memories.add(memory)
            await uow.commit()
        return memory


@dataclass(frozen=True)
class RecallMemories:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(
        self, workspace_id: UUID, query: str, *, limit: int = RECALL_LIMIT
    ) -> list[Memory]:
        async with self.uow_factory() as uow:
            active = await uow.memories.list_active(workspace_id)
        return rank_memories(active, query, limit=limit)


@dataclass(frozen=True)
class ListMemories:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(self, workspace_id: UUID) -> list[Memory]:
        async with self.uow_factory() as uow:
            return await uow.memories.list_active(workspace_id)


@dataclass(frozen=True)
class ReviseMemory:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(self, workspace_id: UUID, memory_id: UUID, content: str) -> Memory:
        async with self.uow_factory() as uow:
            memory = await uow.memories.get(workspace_id, memory_id)
            if memory is None or memory.is_deleted:
                raise NotFound(f"memory {memory_id} not found")
            memory.revise(content)
            await uow.memories.save(memory)
            await uow.commit()
        return memory


@dataclass(frozen=True)
class ForgetMemory:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(self, workspace_id: UUID, memory_id: UUID) -> None:
        async with self.uow_factory() as uow:
            memory = await uow.memories.get(workspace_id, memory_id)
            if memory is None or memory.is_deleted:
                raise NotFound(f"memory {memory_id} not found")
            memory.soft_delete()
            await uow.memories.save(memory)
            await uow.commit()
