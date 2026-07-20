# ADR-0008: SSE for chat streaming; WebSocket reserved for voice

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Chat answers must stream token-by-token, interleaved with structured events
(citations, tool requests, approval prompts, usage). Voice (S4) will need
low-latency bidirectional audio. The transport choice affects proxy behavior,
reconnection semantics, client complexity, and API ergonomics.

## Decision

Chat streaming uses **Server-Sent Events**: `POST
/conversations/{id}/messages` returns `text/event-stream` carrying typed events
(`message_start`, `content_delta`, `citation`, `tool_request`,
`approval_required`, `usage`, `message_end`, `error`). **WebSocket** is
reserved for the voice session (`/ws/voice`) where true bidirectionality is
intrinsic.

## Alternatives considered

- **WebSocket for everything.** One transport, full duplex. Rejected for chat
  because chat is request→streamed-response, not a dialogue at the transport
  level: WS adds connection lifecycle management, heartbeat/reconnect
  protocol design, and loses plain-HTTP semantics (auth headers, status codes,
  standard middlewares, curl-ability) for no gain. User actions during a stream
  (approve, cancel) are ordinary HTTP POSTs — they don't need the socket.
- **Long-polling / chunked JSON.** Works everywhere but reinvents SSE with
  worse standardization; no `Last-Event-ID` resume convention.
- **gRPC streaming.** Excellent for service-to-service; wrong for a browser/
  Tauri client without a proxy layer we otherwise don't need.

## Consequences

- Client code is `fetch` + an SSE parser; every event is typed in the generated
  client from the OpenAPI-documented event schemas
  (`12-api-specification.md` specifies the full protocol).
- Reconnection: SSE's `Last-Event-ID` gives resumable streams nearly for free;
  events carry monotonic ids per message.
- SSE is one-directional — exactly the shape of the chat flow; anything
  user-initiated mid-stream is a separate HTTP call correlated by run/message
  id, which keeps those actions independently auditable.
- Voice gets a clean-slate WS protocol design in M21 without chat legacy.
- Matches Anthropic's own streaming shape, making the provider adapter's
  translation to our event protocol thin.

## Revisit triggers

A future collaborative/multi-device sync feature needing server-push outside a
request lifecycle (would add WS or SSE-subscription channels, not replace chat
SSE).
