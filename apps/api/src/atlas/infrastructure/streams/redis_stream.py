"""Redis Streams implementation of the stream buffer (docs/12 §4.3.5).

One Redis Stream per message id. The SSE event index maps onto an
explicit stream entry id ``{index+1}-0`` (Redis ids must start above
0-0), so `Last-Event-ID: n` resumes with a plain XREAD from ``n+1-0``
— replay and live tail are the same code path, and XREAD's BLOCK
timeout doubles as the ping cadence.

Retention: while a stream is live the key carries a one-hour safety
TTL (a crashed pump must not leak keys forever); `finish` cuts it to
the spec's 15 minutes after the terminal event.
"""

from collections.abc import AsyncIterator
from uuid import UUID

from redis.asyncio import Redis

from atlas.infrastructure.streams.base import BufferedEvent

RETENTION_SECONDS = 900  # 15 minutes after message_end (docs/12 §4.3.5)
_LIVE_SAFETY_SECONDS = 3600
_ACTIVE_TTL_SECONDS = 600
_PING_BLOCK_MS = 15_000  # ": ping" cadence (docs/12 §4.3.2)
_READ_BATCH = 64

_STREAM_KEY = "atlas:chat:stream:{message_id}"
_ACTIVE_KEY = "atlas:chat:active:{conversation_id}"
_IDEM_KEY = "atlas:chat:idem:{key}"


class RedisStreamBuffer:
    """`StreamBuffer` over Redis Streams."""

    def __init__(self, redis: Redis, *, ping_block_ms: int = _PING_BLOCK_MS) -> None:
        self._redis = redis
        self._ping_block_ms = ping_block_ms

    async def append(self, message_id: UUID, event: BufferedEvent) -> None:
        key = _STREAM_KEY.format(message_id=message_id)
        await self._redis.xadd(
            key,
            {
                "event": event.event,
                "data": event.data,
                "terminal": "1" if event.terminal else "0",
            },
            id=f"{event.index + 1}-0",
        )
        await self._redis.expire(key, _LIVE_SAFETY_SECONDS)

    async def finish(self, message_id: UUID) -> None:
        await self._redis.expire(_STREAM_KEY.format(message_id=message_id), RETENTION_SECONDS)

    async def exists(self, message_id: UUID) -> bool:
        return bool(await self._redis.exists(_STREAM_KEY.format(message_id=message_id)))

    async def read(
        self, message_id: UUID, *, after: int = -1
    ) -> AsyncIterator[BufferedEvent | None]:
        key = _STREAM_KEY.format(message_id=message_id)
        cursor = f"{after + 1}-0"  # XREAD returns entries strictly greater
        while True:
            batches = await self._redis.xread(
                {key: cursor}, block=self._ping_block_ms, count=_READ_BATCH
            )
            if not batches:
                # Quiet interval. A vanished key means the buffer
                # expired mid-tail — stop instead of pinging forever.
                if not await self.exists(message_id):
                    return
                yield None
                continue
            for _, entries in batches:
                for entry_id, fields in entries:
                    cursor = _as_text(entry_id)
                    event = _as_event(cursor, fields)
                    yield event
                    if event.terminal:
                        return

    async def try_claim_conversation(self, conversation_id: UUID) -> bool:
        claimed = await self._redis.set(
            _ACTIVE_KEY.format(conversation_id=conversation_id),
            "1",
            nx=True,
            ex=_ACTIVE_TTL_SECONDS,
        )
        return bool(claimed)

    async def release_conversation(self, conversation_id: UUID) -> None:
        await self._redis.delete(_ACTIVE_KEY.format(conversation_id=conversation_id))

    async def recall_message_for(self, idempotency_key: str) -> UUID | None:
        value = await self._redis.get(_IDEM_KEY.format(key=idempotency_key))
        if value is None:
            return None
        return UUID(_as_text(value))

    async def remember_message_for(self, idempotency_key: str, message_id: UUID) -> None:
        await self._redis.set(
            _IDEM_KEY.format(key=idempotency_key),
            str(message_id),
            nx=True,
            ex=RETENTION_SECONDS,
        )


def _as_text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _as_event(entry_id: str, fields: dict[bytes | str, bytes | str]) -> BufferedEvent:
    decoded = {_as_text(k): _as_text(v) for k, v in fields.items()}
    return BufferedEvent(
        index=int(entry_id.partition("-")[0]) - 1,
        event=decoded["event"],
        data=decoded["data"],
        terminal=decoded.get("terminal") == "1",
    )
