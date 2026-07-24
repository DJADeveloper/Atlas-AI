"use client";

/**
 * Source viewer (M09): the document's chunks in order, with the cited
 * chunk visually highlighted — clicking a citation chip lands here.
 */

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

interface ChunkView {
  id: string;
  ordinal: number;
  text: string;
  heading_path: string[];
}

export function SourceViewer({
  documentId,
  highlightChunkId,
}: {
  documentId: string;
  highlightChunkId: string | null;
}) {
  const [title, setTitle] = useState<string | null>(null);
  const [path, setPath] = useState<string>("");
  const [chunks, setChunks] = useState<ChunkView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const highlightRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    async function load() {
      const document = await api.GET("/api/v1/documents/{document_id}", {
        params: { path: { document_id: documentId } },
      });
      if (document.data) {
        setTitle(document.data.title ?? null);
        setPath(document.data.path);
      } else {
        setError("document not found");
        return;
      }
      const listed = await api.GET("/api/v1/documents/{document_id}/chunks", {
        params: { path: { document_id: documentId } },
      });
      if (listed.data) {
        setChunks(
          listed.data.map((chunk) => ({
            id: chunk.id,
            ordinal: chunk.ordinal,
            text: chunk.text,
            heading_path: chunk.heading_path,
          })),
        );
      }
    }
    void load();
  }, [documentId]);

  useEffect(() => {
    highlightRef.current?.scrollIntoView({ block: "center" });
  }, [chunks]);

  if (error !== null) {
    return <p className="text-sm text-red-700 dark:text-red-300">{error}</p>;
  }
  return (
    <article data-testid="source-viewer">
      <h1 className="mb-1 text-xl font-semibold">{title ?? path}</h1>
      <p className="mb-6 text-xs text-zinc-500 dark:text-zinc-400">{path}</p>
      <div className="space-y-3">
        {chunks.map((chunk) => {
          const highlighted = chunk.id === highlightChunkId;
          return (
            <div
              key={chunk.id}
              ref={highlighted ? highlightRef : null}
              data-testid={highlighted ? "highlighted-chunk" : `chunk-${chunk.ordinal}`}
              className={
                highlighted
                  ? "rounded border-l-4 border-sky-500 bg-sky-50 p-3 text-sm dark:bg-sky-950"
                  : "rounded border-l-4 border-transparent p-3 text-sm"
              }
            >
              {chunk.heading_path.length > 0 && (
                <p className="mb-1 text-xs font-medium text-zinc-500 dark:text-zinc-400">
                  {chunk.heading_path.join(" › ")}
                </p>
              )}
              <p className="whitespace-pre-wrap">{chunk.text}</p>
            </div>
          );
        })}
      </div>
    </article>
  );
}
