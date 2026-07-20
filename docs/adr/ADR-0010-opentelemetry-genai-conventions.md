# ADR-0010: OpenTelemetry with GenAI conventions

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas promises that every answer and action is explainable, every model call
accounted for (tokens, cost, prompt version), and every regression traceable.
The LLM-observability market offers proprietary platforms (LangSmith, Langfuse,
Braintrust, Arize) with polished tracing UIs; the vendor-neutral path is
OpenTelemetry, whose GenAI semantic conventions have matured. A local-first
product adds a constraint most SaaS teams don't have: telemetry must not leave
the machine by default.

## Decision

**OpenTelemetry is the only instrumentation API in the codebase.** Traces,
metrics, and logs flow to a local OTel Collector (compose `observability`
profile) backed by Prometheus + Grafana and a local trace store. LLM spans
follow the OTel **GenAI semantic conventions** plus `atlas.*` attributes
(prompt_version, profile, cost_usd). **LangSmith is an optional exporter**
behind a feature flag, off by default.

## Alternatives considered

- **LangSmith (or Langfuse/Braintrust) as primary.** Superior LLM-native UX
  today (prompt playgrounds, dataset UIs). Rejected as primary because it
  inverts the privacy default (prompts contain user documents; shipping them to
  a third party by default contradicts the product's core promise) and couples
  instrumentation to a vendor API. As an *optional exporter* it keeps the UX
  benefits for development on synthetic corpora.
- **Structured logging only, no tracing.** Cheapest; rejected because the
  interesting questions are tree-shaped (which retrieval fed which prompt fed
  which tool call?) and reconstructing trees from flat logs is the bad version
  of tracing.
- **Self-built trace tables in Postgres.** Tempting since eval runs already
  live there; rejected because OTel gives context propagation across API ↔
  Celery ↔ (later) Temporal for free, and conventions make the data legible to
  any backend. Domain-level records (`tool_invocations`, `agent_steps`) remain
  in Postgres — they are product data, not telemetry; the two are correlated by
  trace_id, not merged.

## Consequences

- Instrument once, choose backends per deployment: local Grafana for users,
  optional cloud exporters for development, anything OTLP-speaking later.
- Telemetry-privacy rule becomes enforceable at one choke point: content-bearing
  attributes are scrubbed/limited at the collector for user deployments
  (`31-observability-architecture.md`).
- We track the still-evolving GenAI semconv; attribute names may need a
  migration pass at 1.0. Accepted — pinning to a vendor schema has the same
  risk without the neutrality.
- Cost accounting lives on spans *and* on domain rows (messages,
  tool_invocations) — spans for operators, rows for product features (user
  cost dashboards); one derivation, two sinks.

## Revisit triggers

GenAI semconv stabilization requiring an attribute migration; an
LLM-observability standard emerging that Grafana-class local tooling adopts
natively.
