"use client";

/** Jobs progress (M09): 2s polling — state changes visible well
 * inside the 5s acceptance window. */

import { useEffect, useState } from "react";

import { StatusBadge, toneForState } from "@atlas/ui";

import { api } from "@/lib/api";

interface JobView {
  id: string;
  state: string;
  stage: string;
  attempts: number;
  error: string | null;
}

const POLL_MS = 2_000;

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobView[]>([]);

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
          })),
        );
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
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-200 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800">
            <th className="py-2">Job</th>
            <th>State</th>
            <th>Stage</th>
            <th>Attempts</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody data-testid="jobs-list">
          {jobs.map((job) => (
            <tr key={job.id} className="border-b border-zinc-100 dark:border-zinc-900">
              <td className="py-2 font-mono text-xs">{job.id.slice(0, 8)}</td>
              <td>
                <StatusBadge label={job.state} tone={toneForState(job.state)} />
              </td>
              <td>{job.stage}</td>
              <td>{job.attempts}</td>
              <td className="max-w-64 truncate text-xs text-zinc-500">{job.error ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
