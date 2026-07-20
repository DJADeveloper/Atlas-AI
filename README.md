# Atlas

**A local-first AI Operating System.**

Atlas is a secure, observable, permission-aware layer between a person and
their digital world. You talk to your computer — text now, voice later — and
Atlas finds what you mean in your own files, answers with citations, and (only
with your explicit, revocable permission) acts on your behalf.

> "Find the proposal I wrote for CAIR." · "What changed since last week's
> architecture?" · "Organize my Downloads folder." · "Prepare me for tomorrow."

Atlas is **not** a chatbot, a RAG demo, or an API wrapper. It is a
production-grade system built on six commitments:

1. **Local-first, privacy-first** — your index, memories, and telemetry stay on
   your machine; cloud reasoning is an explicit, visible choice.
2. **The model never touches the computer** — models produce intent; a
   deterministic permission engine decides; audited executors act.
3. **Read-only by default** — every write capability is granted, risk-tiered,
   previewed, approved, undoable, and logged.
4. **Grounded or silent** — answers cite sources or honestly abstain.
5. **Everything observable** — every request is traced; every token and dollar
   accounted for.
6. **Evaluation-driven** — retrieval, grounding, and tool selection ship with
   golden datasets and CI regression gates.

## Status

**Architecture: approved and frozen** — the founding package (28 deliverables
and a 25-milestone plan) lives in [`docs/`](docs/README.md); changes require
an ADR. **Implementation: M03 (Database core & domain skeleton) delivered** — the
reversible Alembic baseline for all ten foundational tables (pgvector
HNSW + FTS included), UUIDv7 identities, the pure-Python knowledge
domain with workspace-scoped repository ports, a SQLAlchemy Unit of
Work, DB-backed feature flags, and an append-only audit table enforced
at the database — on top of M01/M02's scaffolding, probes, config, DI,
logging, and error spine.

## Quickstart

```bash
# toolchain: uv >= 0.8, pnpm >= 10, docker
make install                 # Python 3.12 env (uv) + JS workspace (pnpm)
make up                      # compose core profile: postgres+pgvector, redis, api
curl localhost:8000/health   # {"status":"ok","version":"0.1.0"}
curl localhost:8000/ready    # {"status":"ready","checks":{...}}
make ci-local                # the exact gates CI runs: lint, types, tests, compose
make test-integration        # readiness tests against real containers
```

## Architecture at a glance

- **Backend:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 —
  a modular monolith in Clean/Hexagonal architecture.
- **Data:** PostgreSQL 16 + pgvector as the single datastore (relational +
  vector + full-text hybrid search with RRF) · Redis for cache and queues.
- **AI:** thin in-house provider layer over Anthropic / OpenAI / Ollama;
  local embeddings; versioned prompts; routed models; hard budgets.
- **Desktop:** Tauri 2 shell + Next.js UI around a localhost FastAPI sidecar.
- **Trust:** capability grants + risk tiers + approvals + append-only audit,
  enforced by deterministic code the model cannot influence.
- **Quality:** OpenTelemetry with GenAI conventions · golden-dataset evals
  gating CI · property-tested policy engine.

Start reading at
[`docs/00-architecture-decisions.md`](docs/00-architecture-decisions.md) —
the decision spine — then follow the
[reading order](docs/README.md#reading-order). Decisions with alternatives and
trade-offs are recorded in [`docs/adr/`](docs/adr/README.md).

## Roadmap

Six autonomy stages, delivered across six phases and 25 milestones
([full plan](docs/60-milestones.md)):

| Phase | Milestones | Unlocks |
|---|---|---|
| A — Foundation | M01–M06 | Ingestion, hybrid retrieval, citations |
| B — Assistant MVP | M07–M11 | Grounded streaming chat, observability, eval gates |
| C — Knowledge platform | M12–M16 | All formats, projects, desktop app, permissions, agent runtime |
| D — Computer control | M17–M20 | Safe filesystem actions, durable workflows, knowledge graph |
| E — Interfaces & connectors | M21–M23 | Voice, Git/Drive connectors, daily brief |
| F — Autonomy & hardening | M24–M25 | Autonomous workflows, security hardening, 1.0 |
