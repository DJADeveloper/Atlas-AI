/**
 * SSE consumption contract (M09): the parser reconstructs the exact
 * event stream under arbitrary chunk splits, ignores pings and the
 * retry preamble, and the streaming reader resumes via Last-Event-ID
 * after a mid-stream drop.
 */

import { expect, test } from "vitest";

import { SseParser, streamChatMessage } from "./sse";

const FRAMES =
  "retry: 3000\n\n" +
  'id: 0\nevent: message_start\ndata: {"message_id":"m-1","model":"claude-sonnet-5"}\n\n' +
  ': ping\n\n' +
  'id: 1\nevent: content_delta\ndata: {"index":0,"delta":"Hel"}\n\n' +
  'id: 2\nevent: content_delta\ndata: {"index":1,"delta":"lo [1]"}\n\n' +
  'id: 3\nevent: citation\ndata: {"marker":1,"chunk_id":"c-1"}\n\n' +
  'id: 4\nevent: usage\ndata: {"input_tokens":10,"output_tokens":5}\n\n' +
  'id: 5\nevent: message_end\ndata: {"message_id":"m-1","citation_count":1,"abstained":false}\n\n';

test("parser reconstructs events regardless of chunk boundaries", () => {
  for (const step of [1, 3, 7, 1000]) {
    const parser = new SseParser();
    const events = [];
    for (let index = 0; index < FRAMES.length; index += step) {
      events.push(...parser.feed(FRAMES.slice(index, index + step)));
    }
    expect(events.map((event) => event.event)).toEqual([
      "message_start",
      "content_delta",
      "content_delta",
      "citation",
      "usage",
      "message_end",
    ]);
    expect(events.map((event) => event.id)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(events[1]?.data).toEqual({ index: 0, delta: "Hel" });
  }
});

test("pings and retry preamble never surface as events", () => {
  const parser = new SseParser();
  expect(parser.feed("retry: 3000\n\n: ping\n\n: ping\n\n")).toEqual([]);
});

function sseResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

test("streamChatMessage resumes with Last-Event-ID after a drop", async () => {
  const calls: Array<{ url: string; lastEventId: string | null }> = [];
  const first = FRAMES.slice(0, FRAMES.indexOf("id: 3")); // drops mid-stream
  const rest =
    'id: 3\nevent: citation\ndata: {"marker":1,"chunk_id":"c-1"}\n\n' +
    'id: 4\nevent: usage\ndata: {"input_tokens":10,"output_tokens":5}\n\n' +
    'id: 5\nevent: message_end\ndata: {"message_id":"m-1","citation_count":1,"abstained":false}\n\n';

  const fakeFetch: typeof fetch = (input, init) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    calls.push({ url, lastEventId: headers.get("Last-Event-ID") });
    return Promise.resolve(sseResponse(calls.length === 1 ? first : rest));
  };

  const seen: string[] = [];
  const outcome = await streamChatMessage({
    baseUrl: "http://api.test",
    conversationId: "conv-1",
    content: "hi",
    idempotencyKey: "k-1",
    fetch: fakeFetch,
    onEvent: (event) => seen.push(event.event),
  });

  expect(outcome.terminal).toBe(true);
  expect(outcome.messageId).toBe("m-1");
  expect(outcome.lastEventId).toBe(5);
  expect(seen).toEqual([
    "message_start",
    "content_delta",
    "content_delta",
    "citation",
    "usage",
    "message_end",
  ]);
  expect(calls[1]?.url).toContain("/api/v1/messages/m-1/stream");
  expect(calls[1]?.lastEventId).toBe("2");
});

test("problem+json failures surface code and trace_id", async () => {
  const fakeFetch: typeof fetch = () =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          status: 409,
          code: "stream_in_progress",
          detail: "busy",
          trace_id: "t-1",
        }),
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      ),
    );
  await expect(
    streamChatMessage({
      baseUrl: "http://api.test",
      conversationId: "conv-1",
      content: "hi",
      idempotencyKey: "k-1",
      fetch: fakeFetch,
      onEvent: () => undefined,
    }),
  ).rejects.toMatchObject({ problem: { code: "stream_in_progress", trace_id: "t-1" } });
});
