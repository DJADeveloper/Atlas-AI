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
  batchId: string | null;
  stored: string[];
  rejected: { filename: string; reason: string }[];
  enqueued: number;
  error: string | null;
}

export interface WorkerHealth {
  online: boolean;
  reachable: boolean;
  unconsumedQueues: string[];
}

/**
 * Whether anything is consuming the job queues. A job stuck in
 * `pending` means its queue has no consumer, which is invisible from
 * the job row alone — this is what turns that into a stated cause.
 */
export async function fetchWorkerHealth(): Promise<WorkerHealth | null> {
  const { data } = await api.GET("/api/v1/system/workers");
  if (!data) {
    return null;
  }
  return {
    online: data.online,
    reachable: data.reachable,
    unconsumedQueues: data.unconsumed_queues,
  };
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
      batchId: null,
      stored: [],
      rejected: [],
      enqueued: 0,
      error: typeof problem?.detail === "string" ? problem.detail : "upload failed",
    };
  }
  return {
    batchId: data.batch_id,
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
