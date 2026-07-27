"use client";

/**
 * Names the one cause of a job that never leaves `pending`: nothing is
 * consuming its queue. The banner carries the command that fixes it,
 * because "pending forever" is otherwise indistinguishable from "busy"
 * and sends people digging through logs.
 */

import { useEffect, useState } from "react";

import { fetchWorkerHealth } from "@/lib/api";
import type { WorkerHealth } from "@/lib/api";

// The answer is cached server-side and changes only when someone starts
// or stops a worker; polling harder buys nothing and costs a request on
// every screen.
const POLL_MS = 30_000;

const WORKER_COMMAND =
  "uv run celery -A atlas.infrastructure.jobs.worker worker --loglevel INFO " +
  "-Q ingest.parse,ingest.embed,chat.background";

export function WorkerBanner() {
  const [health, setHealth] = useState<WorkerHealth | null>(null);

  useEffect(() => {
    let active = true;
    let inFlight = false;

    async function poll() {
      // A background tab has nobody to tell, and overlapping requests
      // would queue behind each other on a slow answer.
      if (inFlight || document.visibilityState === "hidden") {
        return;
      }
      inFlight = true;
      try {
        const next = await fetchWorkerHealth();
        if (active) {
          setHealth(next);
        }
      } finally {
        inFlight = false;
      }
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  // Unknown (still loading, or the API is unreachable) stays silent:
  // a false alarm is worse than a slightly late one.
  if (health === null || health.unconsumedQueues.length === 0) {
    return null;
  }

  const partial = health.online;
  return (
    <div
      data-testid="worker-banner"
      role="status"
      className="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
    >
      <p className="font-semibold">
        {partial
          ? "A worker is running, but not on every queue"
          : "No worker is running — uploads will sit at “pending”"}
      </p>
      <p className="mt-1">
        {partial ? (
          <>
            Nothing is consuming <Queues names={health.unconsumedQueues} />, so work that reaches{" "}
            {health.unconsumedQueues.length > 1 ? "those stages" : "that stage"} waits forever.
            Restart the worker with every queue:
          </>
        ) : (
          <>
            Files upload and queue correctly, but nothing picks them up. Start the worker from{" "}
            <code className="rounded bg-amber-100 px-1 dark:bg-amber-900">apps/api</code>:
          </>
        )}
      </p>
      <pre className="mt-2 overflow-x-auto rounded bg-amber-100 p-2 text-xs dark:bg-amber-900">
        <code>{WORKER_COMMAND}</code>
      </pre>
      {!health.reachable && (
        <p className="mt-2 text-xs">
          The message broker itself did not answer — check that Redis is running
          (<code>make up</code>).
        </p>
      )}
    </div>
  );
}

function Queues({ names }: { names: string[] }) {
  return (
    <>
      {names.map((name, index) => (
        <span key={name}>
          {index > 0 && ", "}
          <code className="rounded bg-amber-100 px-1 dark:bg-amber-900">{name}</code>
        </span>
      ))}
    </>
  );
}
