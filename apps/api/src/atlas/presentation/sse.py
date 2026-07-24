"""SSE wire protocol (docs/12 §4.3.2-4.3.5).

Three separable pieces, pinned by contract tests:

- `wire_event`: application stream events → protocol (event name,
  JSON payload) pairs, exactly the §4.3.3 table.
- `pump_stream`: drains the use-case generator into the stream buffer.
  This coroutine — not the HTTP handler — owns generation: a dropped
  connection kills only the tail, the pump finishes the answer, the
  message persists, and the buffer holds events for resume (§4.3.5).
- `sse_response`: tails the buffer as a `text/event-stream` response
  with `retry: 3000`, monotonic ids, and `: ping` comments.
"""

import json
from collections.abc import AsyncGenerator, AsyncIterator
from uuid import UUID

import structlog
from fastapi.responses import StreamingResponse

from atlas.application.chat import (
    ChatStreamEvent,
    StreamCitation,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    StreamUsage,
)
from atlas.infrastructure.streams import BufferedEvent, StreamBuffer
from atlas.presentation.errors import PROBLEM_TYPE_BASE
from atlas.shared.clock import utc_now

_logger = structlog.get_logger("atlas.sse")

RETRY_PREAMBLE = b"retry: 3000\n\n"
PING_COMMENT = b": ping\n\n"

SSE_HEADERS = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # never let a proxy buffer token streams
}


def wire_event(event: ChatStreamEvent) -> tuple[str, dict[str, object], bool]:
    """(event name, payload, terminal) per the §4.3.3 table."""
    if isinstance(event, StreamStarted):
        return (
            "message_start",
            {
                "message_id": str(event.message_id),
                "conversation_id": str(event.conversation_id),
                "model": event.model,
                "prompt_version": event.prompt_version,
                "trace_id": event.trace_id,
                "created_at": utc_now().isoformat(),
            },
            False,
        )
    if isinstance(event, StreamDelta):
        return "content_delta", {"index": event.index, "delta": event.text}, False
    if isinstance(event, StreamCitation):
        return (
            "citation",
            {
                "marker": event.marker,
                "chunk_id": str(event.chunk_id),
                "document_id": str(event.document_id),
                "document_title": event.document_title,
                "source_id": str(event.source_id) if event.source_id else None,
                "snippet": event.snippet,
                "fused_score": event.score,
            },
            False,
        )
    if isinstance(event, StreamUsage):
        return (
            "usage",
            {
                "model": event.model,
                "provider": event.provider,
                "prompt_version": event.prompt_version,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cost_usd": event.cost_usd,
                "latency_ms": event.latency_ms,
                "stop_reason": event.stop_reason,
            },
            False,
        )
    if isinstance(event, StreamCompleted):
        return (
            "message_end",
            {
                "message_id": str(event.message.id),
                "stop_reason": "end_turn",
                "citation_count": event.citation_count,
                "abstained": event.message.abstained,
            },
            True,
        )
    if isinstance(event, StreamFailed):
        return (
            "error",
            {
                "type": f"{PROBLEM_TYPE_BASE}{event.code}",
                "title": event.title,
                "status": event.status,
                "code": event.code,
                "detail": event.detail,
            },
            True,
        )
    raise TypeError(f"unmapped stream event {type(event).__name__}")  # unreachable by design


async def pump_stream(
    events: AsyncIterator[ChatStreamEvent],
    started: StreamStarted,
    buffer: StreamBuffer,
) -> None:
    """Drain the exchange into the buffer, terminal event guaranteed.

    Any unexpected exception becomes an `error` event rather than a
    silently truncated stream — a client must always see a terminal
    event or buffer expiry, never an open-ended wait."""
    message_id = started.message_id
    index = 0

    async def emit(name: str, payload: dict[str, object], *, terminal: bool) -> None:
        nonlocal index
        await buffer.append(
            message_id,
            BufferedEvent(index=index, event=name, data=json.dumps(payload), terminal=terminal),
        )
        index += 1

    try:
        name, payload, terminal = wire_event(started)
        await emit(name, payload, terminal=terminal)
        async for event in events:
            name, payload, terminal = wire_event(event)
            await emit(name, payload, terminal=terminal)
    except Exception:
        _logger.exception("sse.pump_failed", message_id=str(message_id))
        try:
            await emit(
                "error",
                {
                    "type": f"{PROBLEM_TYPE_BASE}internal_error",
                    "title": "Internal error",
                    "status": 500,
                    "code": "internal_error",
                    "detail": "Stream generation failed unexpectedly.",
                },
                terminal=True,
            )
        except Exception:
            _logger.exception("sse.pump_error_event_failed", message_id=str(message_id))
    finally:
        await buffer.finish(message_id)
        await buffer.release_conversation(started.conversation_id)


def encode_sse(event: BufferedEvent) -> bytes:
    return f"id: {event.index}\nevent: {event.event}\ndata: {event.data}\n\n".encode()


async def sse_body(
    buffer: StreamBuffer, message_id: UUID, *, after: int = -1
) -> AsyncGenerator[bytes]:
    yield RETRY_PREAMBLE
    async for item in buffer.read(message_id, after=after):
        if item is None:
            yield PING_COMMENT
            continue
        yield encode_sse(item)
        if item.terminal:
            return


def sse_response(buffer: StreamBuffer, message_id: UUID, *, after: int = -1) -> StreamingResponse:
    return StreamingResponse(
        sse_body(buffer, message_id, after=after),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
