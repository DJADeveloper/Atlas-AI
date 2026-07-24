/**
 * @atlas/api-client — the generated Atlas API client (ADR-0001) plus
 * the one SSE consumption module every app shares (docs/12 §4.3).
 */

export { createAtlasClient } from "./client";
export type { AtlasClient, AtlasClientOptions, paths } from "./client";
export { AtlasApiError, SseParser, streamChatMessage } from "./sse";
export type {
  ChatSseEvent,
  ChatSseEventName,
  ProblemDetails,
  StreamOptions,
  StreamOutcome,
} from "./sse";
