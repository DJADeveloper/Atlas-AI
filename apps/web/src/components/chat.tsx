"use client";

/**
 * The chat screen (M09): conversation list, live token streaming over
 * the shared SSE module, citation chips as markers resolve, a
 * visually distinct abstention state, and thumbs feedback per answer.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Chip, StreamCursor } from "@atlas/ui";

import { api, streamMessage } from "@/lib/api";
import type { ChatSseEvent } from "@/lib/api";

interface CitationInfo {
  marker: number;
  chunk_id: string;
  document_id: string;
  document_title: string | null;
  snippet: string;
}

interface MessageItem {
  id: string;
  role: string;
  content: string;
  abstained: boolean;
  citations: CitationInfo[];
  streaming?: boolean;
  feedback?: "up" | "down";
}

interface ConversationSummary {
  id: string;
  title: string | null;
}

function newId(): string {
  return crypto.randomUUID();
}

export function ChatScreen() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const refreshConversations = useCallback(async () => {
    const { data } = await api.GET("/api/v1/conversations");
    if (data) {
      setConversations(data.items.map((item) => ({ id: item.id, title: item.title ?? null })));
    }
  }, []);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  const openConversation = useCallback(async (id: string) => {
    setConversationId(id);
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/messages", {
      params: { path: { conversation_id: id } },
    });
    if (data) {
      setMessages(
        data.items.map((item) => ({
          id: item.id,
          role: item.role,
          content: item.content,
          abstained: item.abstained,
          citations: (item.citations ?? []).map((citation) => ({
            marker: citation.marker,
            chunk_id: citation.chunk_id,
            document_id: citation.document_id,
            document_title: citation.document_title ?? null,
            snippet: citation.snippet,
          })),
        })),
      );
    }
  }, []);

  const send = useCallback(async () => {
    const content = draft.trim();
    if (content === "" || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    setDraft("");
    try {
      let targetId = conversationId;
      if (targetId === null) {
        const created = await api.POST("/api/v1/conversations", { body: { title: null } });
        if (!created.data) {
          throw new Error("could not create a conversation");
        }
        targetId = created.data.id;
        setConversationId(targetId);
      }
      const userMessage: MessageItem = {
        id: newId(),
        role: "user",
        content,
        abstained: false,
        citations: [],
      };
      const pendingId = newId();
      setMessages((current) => [
        ...current,
        userMessage,
        { id: pendingId, role: "assistant", content: "", abstained: false, citations: [], streaming: true },
      ]);

      const applyEvent = (event: ChatSseEvent) => {
        setMessages((current) =>
          current.map((message) => {
            if (message.id !== pendingId) {
              return message;
            }
            if (event.event === "message_start") {
              return { ...message, id: String(event.data["message_id"]), streaming: true };
            }
            return message;
          }),
        );
        setMessages((current) =>
          current.map((message) => {
            if (!message.streaming) {
              return message;
            }
            switch (event.event) {
              case "content_delta":
                return { ...message, content: message.content + String(event.data["delta"] ?? "") };
              case "citation":
                return {
                  ...message,
                  citations: [
                    ...message.citations,
                    {
                      marker: Number(event.data["marker"]),
                      chunk_id: String(event.data["chunk_id"]),
                      document_id: String(event.data["document_id"]),
                      document_title:
                        event.data["document_title"] === null
                          ? null
                          : String(event.data["document_title"]),
                      snippet: String(event.data["snippet"] ?? ""),
                    },
                  ],
                };
              case "message_end":
                return {
                  ...message,
                  abstained: Boolean(event.data["abstained"]),
                  streaming: false,
                };
              case "error":
                setError(`${String(event.data["code"])}: ${String(event.data["detail"])}`);
                return { ...message, streaming: false };
              default:
                return message;
            }
          }),
        );
      };

      await streamMessage({
        conversationId: targetId,
        content,
        idempotencyKey: newId(),
        onEvent: applyEvent,
      });
      await refreshConversations();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [busy, conversationId, draft, refreshConversations]);

  const rate = useCallback(async (messageId: string, rating: "up" | "down") => {
    const { data } = await api.POST("/api/v1/messages/{message_id}/feedback", {
      params: { path: { message_id: messageId } },
      body: { rating, categories: [], comment: null },
    });
    if (data) {
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId ? { ...message, feedback: rating } : message,
        ),
      );
    }
  }, []);

  return (
    <div className="flex h-full gap-6">
      <aside className="w-56 shrink-0 border-r border-zinc-200 pr-4 dark:border-zinc-800">
        <button
          type="button"
          data-testid="new-conversation"
          onClick={() => {
            setConversationId(null);
            setMessages([]);
          }}
          className="mb-3 w-full rounded bg-zinc-900 px-2 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          New chat
        </button>
        <ul className="space-y-1 text-sm">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                onClick={() => void openConversation(conversation.id)}
                className={`w-full truncate rounded px-2 py-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                  conversation.id === conversationId ? "bg-zinc-100 font-medium dark:bg-zinc-800" : ""
                }`}
              >
                {conversation.title ?? "Untitled"}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col" data-testid="chat-screen">
        <div className="flex-1 space-y-4 overflow-y-auto pb-4">
          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} onRate={rate} />
          ))}
          <div ref={bottomRef} />
        </div>
        {error !== null && (
          <p data-testid="chat-error" className="mb-2 rounded bg-red-50 px-3 py-2 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
            {error}
          </p>
        )}
        <form
          className="flex gap-2 border-t border-zinc-200 pt-3 dark:border-zinc-800"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask about your documents…"
            data-testid="chat-input"
            className="flex-1 rounded border border-zinc-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700"
          />
          <button
            type="submit"
            disabled={busy}
            data-testid="chat-send"
            className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
          >
            Send
          </button>
        </form>
      </section>
    </div>
  );
}

function MessageBubble({
  message,
  onRate,
}: {
  message: MessageItem;
  onRate: (messageId: string, rating: "up" | "down") => Promise<void>;
}) {
  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[80%] rounded-lg bg-sky-600 px-3 py-2 text-sm text-white">
        {message.content}
      </div>
    );
  }
  if (message.abstained) {
    return (
      <div
        data-testid="abstention-message"
        className="max-w-[80%] rounded-lg border border-dashed border-amber-400 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-600 dark:bg-amber-950 dark:text-amber-200"
      >
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
          Not found in your documents
        </p>
        <p>{message.content}</p>
      </div>
    );
  }
  return (
    <div className="max-w-[80%] rounded-lg bg-zinc-100 px-3 py-2 text-sm dark:bg-zinc-800" data-testid="assistant-message">
      <p className="whitespace-pre-wrap">
        {message.content}
        {message.streaming === true && <StreamCursor />}
      </p>
      {message.citations.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1" data-testid="citation-list">
          {message.citations.map((citation) => (
            <Link
              key={citation.marker}
              href={`/documents/${citation.document_id}?chunk=${citation.chunk_id}`}
            >
              <Chip tone="citation" testId={`citation-chip-${citation.marker}`}>
                [{citation.marker}] {citation.document_title ?? "source"}
              </Chip>
            </Link>
          ))}
        </div>
      )}
      {message.streaming !== true && (
        <div className="mt-2 flex gap-1">
          <button
            type="button"
            aria-label="Thumbs up"
            data-testid="feedback-up"
            onClick={() => void onRate(message.id, "up")}
            className={`rounded px-1.5 text-xs ${message.feedback === "up" ? "bg-emerald-200 dark:bg-emerald-800" : "hover:bg-zinc-200 dark:hover:bg-zinc-700"}`}
          >
            👍
          </button>
          <button
            type="button"
            aria-label="Thumbs down"
            data-testid="feedback-down"
            onClick={() => void onRate(message.id, "down")}
            className={`rounded px-1.5 text-xs ${message.feedback === "down" ? "bg-red-200 dark:bg-red-800" : "hover:bg-zinc-200 dark:hover:bg-zinc-700"}`}
          >
            👎
          </button>
        </div>
      )}
    </div>
  );
}
