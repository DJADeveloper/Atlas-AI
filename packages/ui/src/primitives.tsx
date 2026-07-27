/**
 * Shared UI primitives (M09): chip, stream cursor, status badge,
 * skeleton, progress bar.
 *
 * Tailwind utility classes over design tokens; dark mode via the
 * `dark:` variant (class strategy, toggled on <html>). Deliberately
 * tiny — polish beyond the smoke bar is M14/M25 scope.
 */

import type { ReactNode } from "react";

export interface ChipProps {
  children: ReactNode;
  onClick?: () => void;
  tone?: "citation" | "neutral";
  testId?: string;
}

/** Small interactive chip — citations in chat, filters elsewhere. */
export function Chip({ children, onClick, tone = "neutral", testId }: ChipProps) {
  const toneClasses =
    tone === "citation"
      ? "bg-sky-100 text-sky-900 hover:bg-sky-200 dark:bg-sky-900 dark:text-sky-100 dark:hover:bg-sky-800"
      : "bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200";
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium transition-colors ${toneClasses}`}
    >
      {children}
    </button>
  );
}

/** Blinking block cursor shown while an answer is streaming. */
export function StreamCursor({ testId = "stream-cursor" }: { testId?: string }) {
  return (
    <span
      data-testid={testId}
      className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-zinc-500 align-text-bottom dark:bg-zinc-300"
      aria-hidden="true"
    />
  );
}

/**
 * Placeholder for content that is still loading. An empty table and a
 * loading table look identical otherwise, which reads as "broken" —
 * every list renders this until its first response lands.
 */
export function Skeleton({
  rows = 3,
  testId = "skeleton",
}: {
  rows?: number;
  testId?: string;
}) {
  return (
    <div data-testid={testId} aria-busy="true" aria-live="polite" className="space-y-2">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="h-8 animate-pulse rounded bg-zinc-100 dark:bg-zinc-800"
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

/** Determinate progress for work with a known denominator. */
export function ProgressBar({
  done,
  total,
  testId = "progress-bar",
}: {
  done: number;
  total: number;
  testId?: string;
}) {
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div
      data-testid={testId}
      role="progressbar"
      aria-valuenow={done}
      aria-valuemin={0}
      aria-valuemax={total}
      className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
    >
      <div
        className="h-full rounded-full bg-sky-600 transition-all duration-500 dark:bg-sky-500"
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

export type StatusToneName = "ok" | "busy" | "error" | "muted";

const STATUS_TONES: Record<StatusToneName, string> = {
  ok: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900 dark:text-emerald-100",
  busy: "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100",
  error: "bg-red-100 text-red-900 dark:bg-red-900 dark:text-red-100",
  muted: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

export interface StatusBadgeProps {
  label: string;
  tone: StatusToneName;
  testId?: string;
}

/** Job / source state badge with a consistent tone vocabulary. */
export function StatusBadge({ label, tone, testId }: StatusBadgeProps) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${STATUS_TONES[tone]}`}
    >
      {label}
    </span>
  );
}

/** Maps backend job/source states onto badge tones — one vocabulary. */
export function toneForState(state: string): StatusToneName {
  if (state === "succeeded" || state === "active" || state === "ok") {
    return "ok";
  }
  if (state === "failed" || state === "error") {
    return "error";
  }
  if (state === "pending" || state === "running" || state === "retry_scheduled") {
    return "busy";
  }
  return "muted";
}
