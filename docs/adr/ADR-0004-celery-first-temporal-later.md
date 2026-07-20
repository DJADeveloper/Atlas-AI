# ADR-0004: Celery first; Temporal for durable workflows at M19

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas has two very different background workloads. **Bulk pipeline work**
(parse → chunk → embed) is high-volume, stateless-per-item, idempotent, and
retry-friendly. **Agent/workflow execution** (multi-step runs with human
approval pauses, budgets, and crash recovery) is long-lived, stateful, and
demands durable execution semantics. The master requirement asks us to evaluate
Celery vs Temporal. Temporal's programming model (workflows as durable code,
signals, timers) is the industry's best answer for the second workload — but it
brings its own server, database, and operational weight, heavy for a
local-first desktop footprint on day one.

## Decision

Phase A–C background processing uses **Celery 5 + Redis** for the ingestion
pipeline and maintenance jobs. The agent runtime (M16) gets durability from
**application-level write-ahead checkpointing** in Postgres (`agent_steps`,
`agent_checkpoints`). At **M19**, agent/workflow execution migrates to
**Temporal** (bundled via compose profile); Celery remains for bulk ingestion.

## Alternatives considered

- **Temporal for everything from day one.** Cleanest end-state, one execution
  substrate. Rejected for start because: it front-loads a second server + DB
  into the desktop footprint before any feature needs durable execution
  (Phases A–B are ingestion + chat); and running bulk embed fan-out through
  Temporal activities is paying workflow-history overhead for work that a task
  queue does better.
- **Celery for everything, forever.** Celery cannot express "pause this
  five-step run for a human approval for up to 72 hours, survive a reboot, then
  resume exactly once" without reinventing Temporal badly on top of it. Our
  interim checkpointing is a deliberately *simple* version of that for
  single-step-granularity recovery; stretching it to S6 autonomous workflows
  would be reinventing it *badly*.
- **Postgres-based job runners (Procrastinate, pgqueuer) instead of Celery.**
  Attractive (drops Redis as broker), but Celery's maturity, beat scheduler,
  and routing/priority features win for the pipeline workload; Redis is already
  present for caching regardless.

## Consequences

- Phase A ships with one lightweight broker (Redis) already in the stack.
- The agent runtime is designed *as if* on Temporal (deterministic step
  functions, external effects only in activities/tools, approvals as external
  signals) so the M19 migration changes the substrate, not the model —
  `23-agent-architecture.md` specifies this discipline.
- We accept known Celery+Redis sharp edges (visibility timeout redelivery,
  at-least-once delivery) and neutralize them with idempotent, content-hash-
  keyed tasks (`41-infrastructure-architecture.md`).
- M19 carries an explicit migration milestone with chaos tests (kill worker
  mid-run, resume exactly-once) as acceptance criteria; findings are appended
  to this ADR.

## Revisit triggers

If, before M19, approval-paused runs or scheduled workflows appear earlier than
planned, pull the Temporal adoption forward. If at M19 the desktop footprint of
Temporal proves unacceptable, the fallback is hardening the Postgres
checkpointing layer into a proper durable-execution mini-engine — recorded as a
superseding ADR with eyes open about the cost.
