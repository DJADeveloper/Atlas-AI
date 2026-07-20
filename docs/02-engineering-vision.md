# Atlas — Engineering Vision

> Deliverable 2. Conforms to [00-architecture-decisions.md](00-architecture-decisions.md).

---

## 1. The engineering thesis

Atlas is built as if it already serves thousands of users, while running for
one. That is not gold-plating; it is the project's core constraint, because the
product's promises — *never acts without permission, always explains itself,
answers are grounded* — are architectural properties, not features you can bolt
on later. A permission engine retrofitted onto a codebase where model output
reaches the filesystem through ad-hoc glue is a permission engine you cannot
trust. So the engineering vision is: **build the boundaries first, then make
them fast, then make them broad.**

We optimize for engineering excellence over speed, with one discipline that
keeps excellence honest: every milestone ends in a working, demoable slice
(`60-milestones.md`). Architecture that never ships a demo is procrastination
wearing a suit.

## 2. What "production-grade" means here, concretely

| Value | Enforced by |
|---|---|
| **Modular** | Clean Architecture layers with an inward dependency rule (ADR-0002); import-linter contracts in CI so violations fail the build, not the code review. |
| **Testable** | Domain and application layers run against in-memory fakes — no DB, no network; policy engine and chunker carry property-based tests; adapters tested against real Postgres/Redis in testcontainers. Full strategy: `52-testing-strategy.md`. |
| **Observable** | One OTel trace per request, GenAI semantic conventions on every model call, cost as a first-class metric, structlog JSON correlated by trace_id (spine §12). If a behavior can't be reconstructed from telemetry + audit log, it's a bug. |
| **Maintainable** | Strong typing end to end (mypy --strict, TypeScript strict, Pydantic v2 at every boundary); small focused modules; ADRs for every decision with teeth; docs that state trade-offs, not just outcomes. |
| **Secure** | Least privilege by construction: deterministic capability grants, risk tiers, approval gates, append-only audit — all outside the model (ADR-0007). Threat model maintained as a living doc (`30-security-architecture.md`). |
| **Extensible** | Ports and adapters everywhere a vendor or format lives: `LLMProvider`, `EmbeddingProvider`, `Reranker`, `SourceConnector`, parser registry, tool registry. Adding a connector or model must never touch domain code. |
| **Measurable** | Evaluation-driven development: golden datasets, offline eval runs, CI regression gates with numeric thresholds (`32-evaluation-architecture.md`). "It feels better" is not a merge argument. |

## 3. Architectural quality attributes, prioritized

When attributes conflict, this is the precedence order and the reasoning:

1. **Safety & privacy** — a single trust-destroying incident (silent file
   mutation, corpus exfiltration) ends the product. Nothing outranks this.
2. **Explainability** — traces, citations, audit rows. Debuggability and user
   trust are the same mechanism here, which is rare and worth exploiting.
3. **Correctness of retrieval & grounding** — the product is only as good as
   recall@k and citation precision; hence eval gates in CI.
4. **Latency** — interactive paths budget: search p50 < 300 ms, first token
   ~1 s. Background paths trade latency for throughput freely.
5. **Cost** — routed models, context budgets, caching (`43-cost-strategy.md`).
   Cost ranks below latency because a local-first product can always fall back
   to local models; but cost is always *visible*.
6. **Throughput/scale** — a 100k-chunk corpus must be comfortable; beyond that,
   `42-scaling-strategy.md` stages the work. Premature distribution is
   explicitly rejected (modular monolith, ADR-0002).

## 4. Engineering practices

- **Trunk-based development.** Short-lived branches, every merge releasable,
  the pipeline is the gatekeeper (`53-cicd-strategy.md`).
- **ADRs with teeth.** Any decision that binds the future (datastore, framework,
  boundary, protocol) gets an ADR stating alternatives honestly and its
  revisit-trigger. Reversing one requires a superseding ADR, not amnesia.
- **Contract-first API.** OpenAPI is generated from code, diffed in CI for
  breaking changes, and the TS client is regenerated and compile-checked on
  every PR — frontend/backend drift is a build failure.
- **Migrations are additive-first.** Expand → migrate → contract; the desktop
  n-1 compatibility rule (`40-deployment-architecture.md`) depends on it.
- **Determinism where it counts.** Unit and integration tests never call live
  models (recorded fixtures); only eval jobs spend tokens, under budget caps.
- **No placeholder architecture.** A module exists when a milestone needs it.
  The folder tree in spine §5 is the map of *intended* seams, populated
  milestone by milestone — never a forest of empty `__init__.py`.
- **Dependency injection, composition over inheritance, SOLID** — with the
  pragmatic reading: abstractions are introduced at genuine variation points
  (providers, parsers, stores), not speculatively.

## 5. How AI-specific engineering differs — and how Atlas answers it

Classical software fails deterministically; AI systems fail *statistically and
silently*. Retrieval quietly degrades, a prompt edit regresses grounding, a
model upgrade changes tool-selection behavior. The whole trust chain of
classical testing breaks. Atlas's answer is the **evaluation loop as
infrastructure**: every AI behavior (retrieval, grounding, citation, tool
selection, abstention, injection resistance) has a dataset, a metric, a
baseline, and a CI gate; every prompt is a versioned artifact
(`prompt_versions`) stamped onto every trace; every production failure becomes
an eval case via the feedback flywheel. This loop — not any individual model
call — is the system's actual intelligence, and it is the part most worth
demonstrating at Staff level.

Second difference: **the security boundary runs through the middle of the
application.** In classical apps, untrusted input is at the edge. Here, model
output — influenced by every document ingested — is itself untrusted input to
the action layer. Hence the intent/permission/action separation (spine §2.2)
and injection defenses in depth (`30-security-architecture.md`).

## 6. Team-scaling assumptions

Solo-founder today; the practices are chosen to be *cheap at n=1 and load-bearing
at n=10*: ADRs substitute for tribal memory, CI gates substitute for review
capacity, typed contracts substitute for coordination meetings, and CODEOWNERS/
branch protection are configured from day one so nothing cultural must change
when the first collaborator arrives.

## 7. Definition of done, globally

A change is done when: code + tests merged green; types strict; observability
present (new spans/metrics where behavior changed); eval impact assessed (gate
run if AI-touching); docs/ADRs updated; and the change is demoable. A milestone
is done when its acceptance criteria in `60-milestones.md` pass verbatim.
