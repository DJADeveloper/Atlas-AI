# Architecture Decision Records

ADRs record decisions that bind Atlas's future: what we chose, what we
rejected, why, and what would make us revisit. They are immutable once
accepted; reversing a decision requires a new ADR that supersedes the old one.

## Template

```markdown
# ADR-NNNN: Title

- **Status:** Proposed | Accepted | Superseded by ADR-XXXX
- **Date:** YYYY-MM-DD

## Context
What forces are at play; what problem must be decided.

## Decision
The decision, stated in one or two sentences, active voice.

## Alternatives considered
Each alternative steelmanned honestly, with why it lost.

## Consequences
What becomes easier, what becomes harder, what we now must do.

## Revisit triggers
Concrete observable conditions under which this ADR should be re-opened.
```

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-0001](ADR-0001-monorepo.md) | Monorepo | Accepted |
| [ADR-0002](ADR-0002-modular-monolith-clean-architecture.md) | Modular monolith with Clean/Hexagonal architecture | Accepted |
| [ADR-0003](ADR-0003-postgres-pgvector-single-datastore.md) | PostgreSQL + pgvector as the single datastore | Accepted |
| [ADR-0004](ADR-0004-celery-first-temporal-later.md) | Celery first; Temporal for durable workflows at M19 | Accepted |
| [ADR-0005](ADR-0005-thin-provider-abstraction.md) | Thin in-house provider abstraction; no framework lock-in | Accepted |
| [ADR-0006](ADR-0006-tauri-fastapi-sidecar.md) | Tauri + local FastAPI sidecar for desktop | Accepted |
| [ADR-0007](ADR-0007-deterministic-permission-engine.md) | Deterministic permission engine outside the model | Accepted |
| [ADR-0008](ADR-0008-sse-for-chat-streaming.md) | SSE for chat streaming; WebSocket reserved for voice | Accepted |
| [ADR-0009](ADR-0009-uuidv7-primary-keys.md) | UUIDv7 primary keys | Accepted |
| [ADR-0010](ADR-0010-opentelemetry-genai-conventions.md) | OpenTelemetry with GenAI conventions | Accepted |
| [ADR-0011](ADR-0011-id-based-aggregate-references.md) | Identifier-based aggregate references (no ORM relationships) | Accepted |
