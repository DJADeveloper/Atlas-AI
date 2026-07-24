"""The stream-buffer contract behind detached SSE (docs/12 §4.3.5).

Generation is detached from the HTTP connection: the pump appends every
event to a per-message buffer and handlers tail it. A dropped
connection kills only the tail; the model keeps generating, and the
finished answer is already persisted in `messages` — the buffer is a
latency optimization over the database, never the source of truth.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class BufferedEvent:
    """One SSE event: `index` is the monotonic id (the resume cursor),
    `terminal` marks message_end/error — nothing follows it."""

    index: int
    event: str
    data: str
    terminal: bool = False


class StreamBuffer(Protocol):
    async def append(self, message_id: UUID, event: BufferedEvent) -> None: ...

    async def finish(self, message_id: UUID) -> None:
        """Start the 15-minute retention countdown (docs/12 §4.3.5)."""
        ...

    async def exists(self, message_id: UUID) -> bool: ...

    def read(self, message_id: UUID, *, after: int = -1) -> AsyncIterator[BufferedEvent | None]:
        """Replay events with index > ``after``, then tail live until
        the terminal event. Yields ``None`` when a ping interval passes
        with no traffic (the SSE handler emits a `: ping` comment)."""
        ...

    async def try_claim_conversation(self, conversation_id: UUID) -> bool:
        """One live stream per conversation (docs/12 §4.3:
        stream_in_progress). True = claimed; False = already streaming."""
        ...

    async def release_conversation(self, conversation_id: UUID) -> None: ...

    async def recall_message_for(self, idempotency_key: str) -> UUID | None:
        """A replayed Idempotency-Key re-attaches to the existing
        stream instead of generating a second answer."""
        ...

    async def remember_message_for(self, idempotency_key: str, message_id: UUID) -> None: ...
