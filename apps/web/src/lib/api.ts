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

export interface UploadOutcome {
  stored: string[];
  rejected: { filename: string; reason: string }[];
  enqueued: number;
  error: string | null;
}

/**
 * Send dropped files as one multipart request. The generated body type
 * renders binary parts as `string` (an openapi-typescript limitation),
 * so the real payload is the FormData built here and handed to the
 * serializer — the declared body is a shape it never reads.
 */
export async function uploadFiles(files: File[]): Promise<UploadOutcome> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  const { data, error } = await api.POST("/api/v1/sources/uploads", {
    body: { files: [] },
    bodySerializer: () => form,
  });
  if (!data) {
    const problem = error as unknown as { detail?: unknown } | undefined;
    return {
      stored: [],
      rejected: [],
      enqueued: 0,
      error: typeof problem?.detail === "string" ? problem.detail : "upload failed",
    };
  }
  return {
    stored: data.stored,
    rejected: data.rejected,
    enqueued: data.enqueued,
    error: null,
  };
}

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
