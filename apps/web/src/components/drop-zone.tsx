"use client";

/**
 * Drop files straight into Atlas. The browser never exposes a real
 * filesystem path, so the bytes are uploaded into a folder Atlas owns
 * and indexed through the ordinary ingestion pipeline — the typed-path
 * flow below it stays for watching folders already on disk.
 *
 * Uploading is only the first second of the work; parsing and embedding
 * follow asynchronously. The batch is therefore tracked to completion
 * here rather than dumping the user on the jobs page to guess.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ProgressBar } from "@atlas/ui";

import { api, uploadFiles } from "@/lib/api";
import type { UploadOutcome } from "@/lib/api";

const ACCEPTED = ".md,.markdown,.txt,.text,.pdf";
const POLL_MS = 1_000;
/** After this long with no progress, stop implying something is coming. */
const STALL_AFTER_MS = 20_000;

const REASON_LABELS: Record<string, string> = {
  unsupported_type: "unsupported file type",
  invalid_name: "unusable filename",
  empty_file: "file is empty",
  too_large: "larger than 50 MB",
};

const STAGE_LABELS: Record<string, string> = {
  parse: "reading",
  embed: "embedding",
};

interface BatchProgress {
  total: number;
  done: number;
  failed: number;
  stage: string | null;
  moving: boolean;
}

export function DropZone({ onIndexed }: { onIndexed: () => void }) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [outcome, setOutcome] = useState<UploadOutcome | null>(null);
  const [progress, setProgress] = useState<BatchProgress | null>(null);
  const [stalled, setStalled] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const batchId = outcome?.batchId ?? null;
  const expected = outcome?.enqueued ?? 0;
  const settled = progress !== null && progress.done + progress.failed >= progress.total;

  // Follow the batch until every job reaches a terminal state.
  useEffect(() => {
    if (batchId === null || expected === 0) {
      return;
    }
    const trace = batchId;
    let active = true;
    let lastChange = Date.now();
    let previous = "";

    async function poll() {
      const { data } = await api.GET("/api/v1/jobs", {
        params: { query: { trace_id: trace, limit: 500 } },
      });
      if (!active || !data) {
        return;
      }
      const done = data.filter((job) => job.state === "succeeded").length;
      const failed = data.filter((job) => job.state === "failed" || job.dead_letter).length;
      const running = data.find((job) => job.state === "running");
      const next: BatchProgress = {
        total: Math.max(data.length, expected),
        done,
        failed,
        stage: running?.stage ?? null,
        moving: running !== undefined,
      };
      const fingerprint = `${next.done}/${next.failed}/${next.stage ?? ""}`;
      if (fingerprint !== previous) {
        previous = fingerprint;
        lastChange = Date.now();
        setStalled(false);
      } else if (Date.now() - lastChange > STALL_AFTER_MS) {
        setStalled(true);
      }
      setProgress(next);
      if (next.done + next.failed >= next.total) {
        onIndexed();
      }
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [batchId, expected, onIndexed]);

  const send = useCallback(
    async (files: File[]) => {
      if (files.length === 0 || uploading) {
        return;
      }
      setUploading(true);
      setOutcome(null);
      setProgress(null);
      setStalled(false);
      try {
        setOutcome(await uploadFiles(files));
        onIndexed();
      } finally {
        setUploading(false);
      }
    },
    [uploading, onIndexed],
  );

  const busy = uploading || (progress !== null && !settled);

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
        aria-busy={busy}
        className={`cursor-pointer rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragging
            ? "border-sky-500 bg-sky-50 dark:bg-sky-950"
            : "border-zinc-300 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-600"
        }`}
      >
        <p className="text-sm font-medium">
          {uploading ? "Uploading…" : "Drop files here, or click to choose"}
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
        <div data-testid="upload-result" className="mt-3 space-y-2 text-sm">
          {outcome.error !== null && (
            <p className="text-red-700 dark:text-red-300">{outcome.error}</p>
          )}

          {outcome.stored.length > 0 && (
            <p className="text-zinc-700 dark:text-zinc-300">
              Added {outcome.stored.join(", ")}.
            </p>
          )}

          {progress !== null && (
            <div className="space-y-1">
              <ProgressBar done={progress.done + progress.failed} total={progress.total} />
              <p
                data-testid="upload-progress"
                className={
                  settled
                    ? "text-emerald-700 dark:text-emerald-300"
                    : "text-zinc-600 dark:text-zinc-400"
                }
              >
                {settled
                  ? indexedSummary(progress)
                  : `Indexing ${progress.done + progress.failed} of ${progress.total}${
                      progress.stage !== null
                        ? ` — ${STAGE_LABELS[progress.stage] ?? progress.stage}`
                        : ""
                    }…`}
              </p>
              {stalled && !settled && (
                <p data-testid="upload-stalled" className="text-amber-700 dark:text-amber-300">
                  Still waiting. Nothing has picked this up — check that the worker is
                  running (the banner above says how).
                </p>
              )}
            </div>
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

function indexedSummary(progress: BatchProgress): string {
  if (progress.failed === 0) {
    return `Indexed ${progress.done} ${progress.done === 1 ? "file" : "files"} — ready to ask about.`;
  }
  return `Indexed ${progress.done}, ${progress.failed} failed — see the jobs page for the error.`;
}
