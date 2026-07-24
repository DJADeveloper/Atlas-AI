/**
 * The one place the web app talks to the backend: the generated
 * client (ADR-0001) plus the shared SSE module. Hand-rolled fetches
 * against /api/v1 are banned outside this package boundary.
 */

import { createAtlasClient, streamChatMessage } from "@atlas/api-client";
import type { ChatSseEvent, StreamOutcome } from "@atlas/api-client";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8000";

export const api = createAtlasClient({ baseUrl: API_BASE_URL });

export function streamMessage(options: {
  conversationId: string;
  content: string;
  idempotencyKey: string;
  onEvent: (event: ChatSseEvent) => void;
  signal?: AbortSignal;
}): Promise<StreamOutcome> {
  return streamChatMessage({ baseUrl: API_BASE_URL, ...options });
}

export type { ChatSseEvent };
