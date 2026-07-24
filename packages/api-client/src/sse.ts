/**
 * SSE consumption for the chat stream (docs/12 §4.3.2-4.3.5).
 *
 * One tested module, not per-component parsing (M09 risk note). The
 * parser understands exactly the server's framing: monotonic integer
 * `id`, an `event` name, one JSON `data` line per frame, `: ping`
 * comments, and a `retry:` preamble. Reconnection uses the protocol's
 * own resume design: `GET /messages/{id}/stream` + `Last-Event-ID`.
 */

export type ChatSseEventName =
  | "message_start"
  | "content_delta"
  | "citation"
  | "usage"
  | "message_end"
  | "error";

export interface ChatSseEvent {
  id: number | null;
  event: string;
  data: Record<string, unknown>;
}

const TERMINAL_EVENTS: ReadonlySet<string> = new Set(["message_end", "error"]);

/** Incremental SSE frame parser; safe under arbitrary chunk splits. */
export class SseParser {
  private buffer = "";

  feed(chunk: string): ChatSseEvent[] {
    this.buffer += chunk;
    const events: ChatSseEvent[] = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const parsed = parseFrame(frame);
      if (parsed !== null) {
        events.push(parsed);
      }
      boundary = this.buffer.indexOf("\n\n");
    }
    return events;
  }
}

function parseFrame(frame: string): ChatSseEvent | null {
  let id: number | null = null;
  let event = "";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":") || line.length === 0) {
      continue; // comment (": ping") or blank
    }
    const separator = line.indexOf(": ");
    if (separator === -1) {
      continue;
    }
    const field = line.slice(0, separator);
    const value = line.slice(separator + 2);
    if (field === "id") {
      id = Number.parseInt(value, 10);
    } else if (field === "event") {
      event = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
    // "retry" is the browser-reconnect hint; the fetch reader ignores it.
  }
  if (event === "" || dataLines.length === 0) {
    return null;
  }
  return { id, event, data: JSON.parse(dataLines.join("\n")) as Record<string, unknown> };
}

export interface StreamOptions {
  baseUrl: string;
  onEvent: (event: ChatSseEvent) => void;
  fetch?: typeof globalThis.fetch;
  signal?: AbortSignal;
  /** Reconnect attempts after a mid-stream network drop. */
  maxReconnects?: number;
}

export interface StreamOutcome {
  lastEventId: number | null;
  messageId: string | null;
  terminal: boolean;
}

async function readStream(
  response: Response,
  onEvent: (event: ChatSseEvent) => void,
  state: { lastEventId: number | null; messageId: string | null },
): Promise<boolean> {
  if (response.body === null) {
    throw new Error("SSE response has no body");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      return false; // stream closed without a terminal event
    }
    for (const event of parser.feed(decoder.decode(value, { stream: true }))) {
      if (event.id !== null) {
        state.lastEventId = event.id;
      }
      if (event.event === "message_start" && typeof event.data["message_id"] === "string") {
        state.messageId = event.data["message_id"];
      }
      onEvent(event);
      if (TERMINAL_EVENTS.has(event.event)) {
        return true;
      }
    }
  }
}

/**
 * POST a message and consume its stream; on a mid-stream drop, resume
 * from the buffer via Last-Event-ID until the terminal event arrives
 * (docs/12 §4.3.5 — generation is detached, nothing is lost).
 */
export async function streamChatMessage(
  options: StreamOptions & {
    conversationId: string;
    content: string;
    idempotencyKey: string;
  },
): Promise<StreamOutcome> {
  const doFetch = options.fetch ?? globalThis.fetch;
  const state: { lastEventId: number | null; messageId: string | null } = {
    lastEventId: null,
    messageId: null,
  };
  const maxReconnects = options.maxReconnects ?? 3;

  const response = await doFetch(
    `${options.baseUrl}/api/v1/conversations/${options.conversationId}/messages`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": options.idempotencyKey,
      },
      body: JSON.stringify({ content: options.content }),
      ...(options.signal ? { signal: options.signal } : {}),
    },
  );
  if (!response.ok) {
    throw await problemError(response);
  }

  let terminal = false;
  try {
    terminal = await readStream(response, options.onEvent, state);
  } catch (error) {
    if (options.signal?.aborted) {
      throw error;
    }
    // fall through to resume
  }
  let attempts = 0;
  while (!terminal && state.messageId !== null && attempts < maxReconnects) {
    attempts += 1;
    try {
      terminal = await resumeStream({ ...options, messageId: state.messageId, state });
    } catch (error) {
      if (options.signal?.aborted) {
        throw error;
      }
    }
  }
  return { lastEventId: state.lastEventId, messageId: state.messageId, terminal };
}

async function resumeStream(
  options: StreamOptions & {
    messageId: string;
    state: { lastEventId: number | null; messageId: string | null };
  },
): Promise<boolean> {
  const doFetch = options.fetch ?? globalThis.fetch;
  const headers: Record<string, string> = {};
  if (options.state.lastEventId !== null) {
    headers["Last-Event-ID"] = String(options.state.lastEventId);
  }
  const response = await doFetch(`${options.baseUrl}/api/v1/messages/${options.messageId}/stream`, {
    method: "GET",
    headers,
    ...(options.signal ? { signal: options.signal } : {}),
  });
  if (!response.ok) {
    throw await problemError(response);
  }
  return readStream(response, options.onEvent, options.state);
}

export interface ProblemDetails {
  status: number;
  code: string;
  detail: string;
  trace_id?: string;
}

export class AtlasApiError extends Error {
  constructor(public readonly problem: ProblemDetails) {
    super(`${problem.code}: ${problem.detail}`);
    this.name = "AtlasApiError";
  }
}

async function problemError(response: Response): Promise<AtlasApiError> {
  let problem: ProblemDetails = {
    status: response.status,
    code: "internal_error",
    detail: response.statusText,
  };
  try {
    problem = (await response.json()) as ProblemDetails;
  } catch {
    // non-JSON error body: keep the fallback shape
  }
  return new AtlasApiError(problem);
}
