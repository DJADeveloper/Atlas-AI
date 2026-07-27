"use client";

/**
 * Drop files straight into Atlas. The browser never exposes a real
 * filesystem path, so the bytes are uploaded into a folder Atlas owns
 * and indexed through the ordinary ingestion pipeline — the typed-path
 * flow below it stays for watching folders already on disk.
 */

import { useCallback, useRef, useState } from "react";

import { uploadFiles } from "@/lib/api";
import type { UploadOutcome } from "@/lib/api";

const ACCEPTED = ".md,.markdown,.txt,.text,.pdf";

const REASON_LABELS: Record<string, string> = {
  unsupported_type: "unsupported file type",
  invalid_name: "unusable filename",
  empty_file: "file is empty",
  too_large: "larger than 50 MB",
};

export function DropZone({ onIndexed }: { onIndexed: () => void }) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<UploadOutcome | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const send = useCallback(
    async (files: File[]) => {
      if (files.length === 0 || busy) {
        return;
      }
      setBusy(true);
      setOutcome(null);
      try {
        setOutcome(await uploadFiles(files));
        onIndexed();
      } finally {
        setBusy(false);
      }
    },
    [busy, onIndexed],
  );

  return (
    <div className="mb-6">
      <div
        data-testid="drop-zone"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void send(Array.from(event.dataTransfer.files));
        }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragging
            ? "border-sky-500 bg-sky-50 dark:bg-sky-950"
            : "border-zinc-300 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-600"
        }`}
      >
        <p className="text-sm font-medium">
          {busy ? "Indexing…" : "Drop files here, or click to choose"}
        </p>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Markdown, text, and PDF — they stay on this machine.
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED}
          data-testid="drop-input"
          className="hidden"
          onChange={(event) => {
            void send(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
      </div>

      {outcome !== null && (
        <div data-testid="upload-result" className="mt-2 space-y-1 text-sm">
          {outcome.error !== null && (
            <p className="text-red-700 dark:text-red-300">{outcome.error}</p>
          )}
          {outcome.stored.length > 0 && (
            <p className="text-emerald-700 dark:text-emerald-300">
              Added {outcome.stored.join(", ")} — {outcome.enqueued} queued for indexing.
            </p>
          )}
          {outcome.rejected.map((item) => (
            <p key={item.filename} className="text-amber-700 dark:text-amber-300">
              Skipped {item.filename} ({REASON_LABELS[item.reason] ?? item.reason}).
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
