"use client";

/** Jobs progress (M09): 2s polling — state changes visible well
 * inside the 5s acceptance window. A job that never leaves `pending`
 * is the one failure the table cannot explain on its own, so its age
 * is shown and the worker banner names the cause. */

import { useEffect, useState } from "react";

import { Skeleton, StatusBadge, toneForState } from "@atlas/ui";

import { WorkerBanner } from "@/components/worker-banner";
import { api } from "@/lib/api";

interface JobView {
  id: string;
  state: string;
  stage: string;
  attempts: number;
  error: string | null;
  createdAt: string;
}

const POLL_MS = 2_000;
/** A pending job older than this is waiting on a consumer, not on work. */
const STUCK_AFTER_MS = 15_000;

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobView[] | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let active = true;
    async function poll() {
      const { data } = await api.GET("/api/v1/jobs");
      if (active && data) {
        setJobs(
          data.map((item) => ({
            id: item.id,
            state: item.state,
            stage: item.stage,
            attempts: item.attempts,
            error: item.error ?? null,
            createdAt: item.created_at,
          })),
        );
        setNow(Date.now());
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <section data-testid="jobs-screen">
      <h1 className="mb-4 text-xl font-semibold">Ingestion jobs</h1>

      <WorkerBanner />

      {jobs === null ? (
        <Skeleton rows={4} testId="jobs-skeleton" />
      ) : jobs.length === 0 ? (
        <p data-testid="jobs-empty" className="text-sm text-zinc-500 dark:text-zinc-400">
          No ingestion jobs yet. Add a source or drop a file to start one.
        </p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-200 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800">
              <th className="py-2">Job</th>
              <th>State</th>
              <th>Stage</th>
              <th>Waiting</th>
              <th>Attempts</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody data-testid="jobs-list">
            {jobs.map((job) => {
              const ageMs = now - Date.parse(job.createdAt);
              const stuck = job.state === "pending" && ageMs > STUCK_AFTER_MS;
              const terminal = job.state === "succeeded" || job.state === "failed";
              return (
                <tr key={job.id} className="border-b border-zinc-100 dark:border-zinc-900">
                  <td className="py-2 font-mono text-xs">{job.id.slice(0, 8)}</td>
                  <td>
                    <StatusBadge label={job.state} tone={toneForState(job.state)} />
                  </td>
                  <td>{job.stage}</td>
                  <td
                    className={stuck ? "text-amber-700 dark:text-amber-300" : "text-zinc-500"}
                    title={stuck ? "No worker has claimed this job" : undefined}
                  >
                    {terminal ? "—" : formatAge(ageMs)}
                  </td>
                  <td>{job.attempts}</td>
                  <td className="max-w-64 truncate text-xs text-zinc-500" title={job.error ?? ""}>
                    {job.error ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

function formatAge(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
