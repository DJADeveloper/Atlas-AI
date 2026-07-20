# ADR-0002: Modular monolith with Clean/Hexagonal architecture

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas's backend must run comfortably on a laptop today, yet the roadmap ends at
a multi-user, cloud-capable system (spine §13, M25). We must pick the process
architecture (monolith vs services) and the internal code architecture
(layering and dependency rules) — and the two choices interact: bad internal
boundaries make later extraction impossible; premature services make local-first
miserable.

## Decision

A single deployable backend (API + workers sharing one codebase) structured as
a modular monolith following Clean/Hexagonal architecture: `domain` (pure,
framework-free entities, value objects, ports) ← `application` (use cases,
unit-of-work) ← `presentation` (FastAPI), with `infrastructure` implementing
domain ports and wired by dependency injection at the composition root.
Dependency direction is enforced mechanically with import-linter in CI.

## Alternatives considered

- **Microservices from day one.** Buys independent scaling and deployment —
  needs Atlas does not have for years — at the price of network hops inside a
  latency-sensitive local product, distributed debugging, and an operational
  burden absurd for a desktop sidecar. Rejected on "monolith first" grounds:
  services should be extracted along proven seams, not guessed ones.
- **Simple layered monolith without ports (framework-centric, "fat FastAPI").**
  Fastest to write; typical for demos. Rejected because Atlas's core promises
  live in code that must be trivially unit-testable (policy engine, agent loop,
  retrieval logic) and vendor-swappable (providers, parsers). Coupling business
  logic to SQLAlchemy/FastAPI makes both properties accidental rather than
  structural.
- **Full DDD with event sourcing / CQRS.** Event sourcing gives perfect audit
  history but at severe complexity cost; Atlas needs an append-only audit *log*
  (a table), not event-sourced state reconstruction. We borrow DDD's language
  (aggregates, bounded contexts, domain events) without the heaviest machinery.

## Consequences

- Business rules test in milliseconds with fakes; the permission engine can be
  property-tested exhaustively (`52-testing-strategy.md`).
- Ceremony cost: repositories, DTO mapping, and use-case classes for flows that
  a CRUD framework would give for free. Accepted; trivial CRUD (settings) may
  keep use cases thin, but never violates dependency direction.
- Service extraction later (ingestion workers, agent executors) becomes moving
  an adapter behind an existing port to a new process — the seams in spine §5
  are the extraction map (`42-scaling-strategy.md`).
- The composition root (app factory) is the single place where wiring lives;
  DI is constructor-injection via FastAPI dependencies and plain constructors,
  not a magic container.

## Revisit triggers

Ingestion throughput or agent concurrency demonstrably constrained by sharing a
process with the interactive API on target hardware; a cloud deployment with
per-tenant isolation requirements that process-level separation would simplify.
