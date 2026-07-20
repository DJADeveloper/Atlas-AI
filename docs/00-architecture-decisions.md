# Atlas — Canonical Architecture Decisions (The Spine)

> **Purpose.** This document is the single source of truth for every cross-cutting
> decision in Atlas. Every other document in `docs/` conforms to it. When a
> document and this spine disagree, the spine wins and the document has a bug.
> Substantive changes to this file require an ADR (see `docs/adr/`).

---

## 1. Product identity

- **Name:** Atlas
- **One-liner:** A local-first AI Operating System — a secure, observable,
  permission-aware layer between a person and their digital world.
- **Not:** a chatbot, a RAG demo, or an API wrapper.
- **Autonomy stages:** S1 Read-only knowledge → S2 Project understanding →
  S3 Safe computer actions → S4 Voice → S5 Connected services → S6 Autonomous
  workflows. Stages are cumulative; a stage never weakens the guarantees of the
  stages before it.

## 2. Non-negotiable principles

1. **Local-first, privacy-first.** User content is indexed and stored locally by
   default. Cloud model calls are opt-in and explicit.
2. **The model never touches the computer.** LLMs produce *intent* (structured
   tool calls). A deterministic policy engine decides *permission*. Executors
   perform *action*. These are three separate components.
3. **Read-only by default.** Every write/execute capability is opt-in, risk-tiered,
   audited, and reversible where physically possible.
4. **Grounded or silent.** Answers about the user's data cite sources or abstain.
5. **Everything observable.** Every request carries a trace; every LLM call logs
   prompt version, model, tokens, cost, and latency.
6. **Evaluation-driven.** Retrieval, grounding, and tool selection have golden
   datasets and CI regression gates before features ship.
7. **No magic.** No hidden automation; every action explainable from the audit log.

## 3. Canonical technology decisions

| Concern | Decision | ADR |
|---|---|---|
| Repository shape | Single monorepo (`apps/`, `packages/`, `infra/`, `docs/`) | ADR-0001 |
| Backend architecture | Modular monolith, Clean/Hexagonal architecture | ADR-0002 |
| Backend language | Python 3.12+, strict typing (mypy), Ruff | — |
| API framework | FastAPI + Pydantic v2 | — |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic | — |
| Primary datastore | PostgreSQL 16 + `pgvector` (single datastore for relational, vector, FTS) | ADR-0003 |
| Cache / queues | Redis 7 | — |
| Background jobs | Celery 5 + Redis now; Temporal adopted at M19 for durable agent workflows | ADR-0004 |
| LLM/embedding access | Thin in-house provider abstraction; no LangChain/framework lock-in | ADR-0005 |
| Desktop | Tauri 2 shell + local FastAPI sidecar; UI is Next.js | ADR-0006 |
| Frontend | Next.js (App Router) + TypeScript strict + Tailwind + shadcn/ui | — |
| Permissions | Deterministic capability grants + risk tiers, enforced outside the model | ADR-0007 |
| Chat streaming | SSE for token streams; WebSocket reserved for voice | ADR-0008 |
| Primary keys | UUIDv7, generated application-side | ADR-0009 |
| Observability | OpenTelemetry (traces/metrics/logs), GenAI semantic conventions; structlog JSON; Prometheus + Grafana; LangSmith as optional exporter | ADR-0010 |
| Python tooling | `uv` for env/deps, `pytest`, `ruff`, `mypy --strict` | — |
| JS tooling | `pnpm` workspaces, Vitest, Playwright | — |
| Containers | Docker + Docker Compose profiles (`core`, `observability`, `full`) | — |
| CI/CD | GitHub Actions | — |

## 4. Canonical repository layout

```
Atlas-AI/
├── apps/
│   ├── api/                  # Python backend (FastAPI + workers)
│   │   ├── src/atlas/        # the atlas package (see §5)
│   │   ├── tests/            # unit / integration / e2e split
│   │   ├── alembic/          # migrations
│   │   └── pyproject.toml
│   ├── web/                  # Next.js UI (used by browser and Tauri shell)
│   └── desktop/              # Tauri 2 shell, bundles api as sidecar
├── packages/
│   ├── api-client/           # generated TS client from OpenAPI
│   └── ui/                   # shared UI primitives (shadcn-based)
├── evals/                    # golden datasets (JSONL) + eval configs
├── infra/
│   ├── docker/               # Dockerfiles
│   └── compose/              # docker-compose.yml + profiles
├── docs/                     # this documentation set + adr/
└── .github/workflows/        # CI pipelines
```

## 5. Canonical backend package layout (Clean Architecture)

Dependencies point strictly inward: `presentation → application → domain`;
`infrastructure` implements `domain` ports and is wired by DI at the edge.
`domain` imports nothing from the other layers and no frameworks.

```
apps/api/src/atlas/
├── domain/                # Enterprise core. Pure Python. No SQLAlchemy, no FastAPI.
│   ├── knowledge/         # Source, Document, DocumentVersion, Chunk + ports
│   ├── conversation/      # Conversation, Message, Citation + ports
│   ├── agents/            # AgentRun, AgentStep, Plan + ports
│   ├── tools/             # ToolDefinition, Capability, RiskTier, PermissionGrant, Approval
│   ├── memory/            # Memory (typed), MemoryScope + ports
│   ├── projects/          # Project, ProjectDocument
│   ├── evaluation/        # EvalDataset, EvalExample, EvalRun, EvalResult
│   └── shared/            # value objects, domain errors, domain events, Result type
├── application/           # Use cases (one class per use case), DTOs, unit-of-work port
│   ├── ingestion/         # IngestDocument, ReindexSource, DetectChanges …
│   ├── retrieval/         # HybridSearch, AssembleContext …
│   ├── chat/              # SendMessage, StreamAnswer …
│   ├── agents/            # StartAgentRun, ResumeAgentRun, ApproveStep …
│   ├── tools/             # InvokeTool, GrantCapability, RevokeCapability …
│   ├── memory/            # RememberFact, RecallMemories …
│   └── evaluation/        # RunEvalSuite, CompareEvalRuns …
├── infrastructure/        # Adapters. Implements domain ports.
│   ├── persistence/       # SQLAlchemy models, repositories, UoW, pgvector
│   ├── parsing/           # PyMuPDF, python-docx, python-pptx, pandas adapters
│   ├── providers/         # anthropic/, openai/, ollama/ (LLM + embeddings)
│   ├── watcher/           # filesystem watching (watchfiles)
│   ├── jobs/              # Celery app, task definitions
│   ├── search/            # hybrid search implementation (SQL + RRF + rerank)
│   └── security/          # keychain, secrets detection, encryption adapters
├── ai/                    # Provider-agnostic LLM runtime: routing, fallback,
│   │                      # circuit breakers, token/cost accounting
│   └── prompts/           # Prompt registry: versioned prompt templates
├── rag/                   # Chunking strategies, embedding pipeline, context
│                          # assembly, citation extraction, abstention logic
├── agents/                # Agent runtime: ReAct loop, planner, reflection,
│                          # checkpointing, approval gates
├── tools/                 # Tool registry + built-in tool implementations
│                          # (filesystem, system, git …) with schemas & policies
├── evals/                 # Eval runners, metrics, LLM-judge harness
├── observability/         # OTel setup, structlog config, metrics, cost meter
├── auth/                  # Local token auth now; JWT/OIDC-ready interfaces
├── config/                # Pydantic Settings, profiles, feature flags
├── presentation/          # FastAPI app factory, /api/v1 routers, SSE, WS, deps
└── shared/                # Cross-cutting: ids (uuid7), clock, errors, typing utils
```

Rule of thumb: `ai/`, `rag/`, `agents/`, `tools/`, `evals/` are *vertical
capability modules*; their public interfaces (ports) live in `domain/`, their
service logic in `application/` semantics (orchestrated by use cases), and
anything touching a vendor SDK, disk, or network lives in `infrastructure/`
or the module's own adapter code. Nothing in `domain/` ever imports them.

## 6. Ubiquitous language (canonical vocabulary)

| Term | Meaning |
|---|---|
| **Source** | A registered origin of content: a watched folder, later a connector (Drive, Git, Notion, Slack, Email). Carries permission scope. |
| **Document** | A logical file/item from a Source. Identified by stable source-relative path/external id. |
| **DocumentVersion** | An immutable snapshot of a Document's content, keyed by content hash (SHA-256). |
| **Chunk** | A retrievable span of a DocumentVersion with embedding + FTS vector + metadata. |
| **IngestionJob** | A tracked unit of parse→chunk→embed work with states `pending → running → succeeded | failed | skipped`. |
| **Conversation / Message** | Chat threads. Messages have roles `user | assistant | tool | system`. |
| **Citation** | A link from an assistant Message to Chunks that ground a claim. |
| **Capability** | A named, parameterized permission, e.g. `fs.read`, `fs.move`, `app.open`, `terminal.run`, `git.commit`. |
| **PermissionGrant** | (principal, capability, scope, mode, expiry) — deterministic, user-granted. |
| **RiskTier** | T0 read-only · T1 reversible write · T2 approval required · T3 always confirm with preview. |
| **ToolInvocation** | One validated, policy-checked, audited execution of a tool. |
| **Approval** | A pending human decision gating a ToolInvocation or AgentStep. |
| **AgentRun / AgentStep** | One agent task and its recorded steps (thought/tool/observation), checkpointed for recovery. |
| **Memory** | A typed long-lived fact: kinds `preference | project_fact | decision | entity | episodic`. Distinct from conversation history and from the knowledge index. |
| **Project** | A user-defined grouping of Sources/Documents with its own memory scope. |
| **PromptVersion** | An immutable, named, versioned prompt template; every LLM call records which one it used. |
| **AuditEvent** | Append-only record of anything security-relevant. |

## 7. Canonical database tables

(Fully specified in `docs/11-database-schema.md`; names are fixed here.)

`users`, `workspaces`, `sources`, `documents`, `document_versions`, `chunks`,
`ingestion_jobs`, `projects`, `project_documents`, `conversations`, `messages`,
`citations`, `memories`, `permission_grants`, `approvals`, `tool_invocations`,
`agent_runs`, `agent_steps`, `agent_checkpoints`, `prompts`, `prompt_versions`,
`eval_datasets`, `eval_examples`, `eval_runs`, `eval_results`, `feedback`,
`audit_events`, `settings`, `feature_flags`, `api_keys`.

Conventions: snake_case, plural table names; UUIDv7 `id` PK; `created_at` /
`updated_at` `timestamptz` (UTC); soft deletes only where the domain needs them
(`deleted_at`), never for audit tables; `audit_events` is append-only.
Embedding column: `vector(768)` in the default local profile
(`nomic-embed-text` via Ollama); dimension is a deployment-profile constant —
switching embedding models is an explicit re-embed migration, never a mixed
index.

## 8. Canonical API surface

Base path `/api/v1`, OpenAPI-first, generated TS client in `packages/api-client`.

| Area | Endpoints (representative) |
|---|---|
| Health | `GET /health`, `GET /ready` |
| Auth | `POST /auth/token`, `GET /auth/me` |
| Conversations | `POST /conversations`, `GET /conversations`, `GET /conversations/{id}`, `POST /conversations/{id}/messages` (SSE stream response), `POST /messages/{id}/feedback` |
| Search | `POST /search` (hybrid, filtered), `GET /documents/{id}`, `GET /documents/{id}/chunks` |
| Sources | `POST /sources`, `GET /sources`, `PATCH /sources/{id}`, `DELETE /sources/{id}`, `POST /sources/{id}/reindex` |
| Jobs | `GET /jobs`, `GET /jobs/{id}` |
| Projects | CRUD + `POST /projects/{id}/documents` |
| Memories | `GET/POST/PATCH/DELETE /memories` |
| Tools & permissions | `GET /tools`, `GET/POST/DELETE /permissions`, `GET /approvals`, `POST /approvals/{id}/decision` |
| Agents | `POST /agent-runs`, `GET /agent-runs/{id}`, `GET /agent-runs/{id}/steps`, `POST /agent-runs/{id}/cancel` |
| Evals | `POST /eval-runs`, `GET /eval-runs`, `GET /eval-runs/{id}` |
| Settings | `GET/PATCH /settings`, `GET /audit-events` |
| Voice (S4) | `WS /ws/voice` |

Errors: RFC 9457 problem+json with stable machine-readable `code`, plus
`trace_id` in every response (header `X-Trace-Id`).

## 9. Model & provider defaults

| Role | Default (hybrid profile) | Local-only profile |
|---|---|---|
| Chat / agent reasoning | Anthropic `claude-sonnet-5` | Ollama `llama3.1:8b` |
| Complex planning / synthesis (escalation) | Anthropic `claude-opus-4-8` | — |
| Cheap classification / routing / titles | Anthropic `claude-haiku-4-5-20251001` | Ollama `llama3.1:8b` |
| Embeddings | Ollama `nomic-embed-text` (768d) — embeddings stay local even in hybrid | same |
| Reranking (optional) | Local cross-encoder (`bge-reranker-base`) behind `Reranker` port | same |

Two runtime profiles: **hybrid** (local data + indexing, cloud reasoning,
default) and **local-only** (no bytes leave the machine — a privacy mode, with
documented quality trade-offs). Profile is a top-level setting, visible in UI.
Routing, fallback chains, and circuit breakers live in `atlas/ai`; providers are
interchangeable behind `LLMProvider` / `EmbeddingProvider` ports.

## 10. Retrieval pipeline defaults

- **Chunking:** structure-aware per format (headings for MD/DOCX, slides for
  PPTX, sheet regions for XLSX, functions/classes for code via tree-sitter);
  target 512 tokens, 15% overlap, hard max 1024.
- **Candidate generation:** top 24 by pgvector cosine (HNSW) + top 24 by
  Postgres FTS (`websearch_to_tsquery`, `ts_rank_cd`).
- **Fusion:** Reciprocal Rank Fusion, k=60.
- **Rerank:** optional cross-encoder over fused top 24 → final top 8.
- **Context assembly:** deduplicate by document, pack to model budget, inline
  citation markers `[n]` mapped to `citations` rows.
- **Abstention:** if fused relevance falls below threshold, say so — never
  free-associate over the user's data.

## 11. Permission model & risk tiers (fixed)

- Capabilities are namespaced verbs (`fs.read`, `fs.write.move`, `app.open`,
  `terminal.run`, `clipboard.read`, `git.read`, `email.read` …).
- A **PermissionGrant** scopes a capability (e.g. `fs.read` on `~/Projects/**`),
  a mode, and optional expiry. Grants are created only by explicit user action.
- **Risk tiers:** T0 auto-allow (read-only); T1 auto-allow + visible notification
  (reversible writes, e.g. create folder); T2 approval required (move/rename
  batches, sending anything); T3 approval with rendered preview + typed
  confirmation (deletes, terminal commands, anything irreversible).
- The policy engine is deterministic code with unit tests. Model output can
  *request*; it can never *grant*, *escalate*, or *bypass*.
- Every allow/deny/approval decision writes an `audit_events` row.

## 12. Observability conventions (fixed)

- One OTel trace per user request; `trace_id` returned to clients.
- Span names: `ingest.parse`, `ingest.chunk`, `ingest.embed`, `rag.retrieve`,
  `rag.rerank`, `llm.call`, `tool.invoke`, `agent.step`, `policy.check`.
- LLM spans follow OTel GenAI semantic conventions and always record:
  provider, model, `prompt_version`, input/output tokens, cost USD, latency,
  stop reason.
- Logs: structlog JSON, correlated by `trace_id`. Metrics: Prometheus
  (latency histograms, token counters, cost counters, queue depth, index lag).
- Cost is a first-class metric aggregated per request, per conversation, per day.

## 13. Canonical milestone list

~25 milestones in 6 phases (full details in `docs/60-milestones.md`; titles fixed here):

- **Phase A — Foundation:** M01 Scaffolding & CI · M02 Config, DI & logging ·
  M03 Database core & domain skeleton · M04 Ingestion v1 (MD/TXT/PDF) ·
  M05 Chunking & embeddings · M06 Hybrid retrieval & citations
- **Phase B — Assistant MVP:** M07 Chat API & conversation memory ·
  M08 Grounded RAG answers & abstention · M09 Web UI chat + citations ·
  M10 Observability foundation · M11 Eval harness & CI regression gate
- **Phase C — Knowledge platform:** M12 Full format coverage & incremental
  re-index · M13 Projects & scoped search · M14 Desktop app (Tauri) ·
  M15 Tool registry & permission engine · M16 Agent runtime v1
- **Phase D — Computer control:** M17 Safe filesystem actions ·
  M18 App control & system tools · M19 Durable workflows (Temporal) ·
  M20 Knowledge graph v1
- **Phase E — Interfaces & connectors:** M21 Voice v1 · M22 Connector
  framework (Git, Drive) · M23 Email & calendar, daily brief
- **Phase F — Autonomy & hardening:** M24 Autonomous workflows & scheduling ·
  M25 Security hardening & 1.0

## 14. Documentation conventions

- Docs live in `docs/`, numbered by area: `0x` foundations, `1x` domain/data/API,
  `2x` AI subsystems, `3x` trust (security/observability/evals), `4x`
  infrastructure, `5x` strategy, `60` milestones.
- Diagrams are Mermaid (render on GitHub). ADRs use the template in `docs/adr/`.
- Every significant decision states alternatives and trade-offs — the docs are
  a teaching artifact as much as a specification.

## 15. ADR index

| ADR | Title |
|---|---|
| ADR-0001 | Monorepo |
| ADR-0002 | Modular monolith with Clean/Hexagonal architecture |
| ADR-0003 | PostgreSQL + pgvector as the single datastore |
| ADR-0004 | Celery first; Temporal for durable workflows at M19 |
| ADR-0005 | Thin in-house provider abstraction; no framework lock-in |
| ADR-0006 | Tauri + local FastAPI sidecar for desktop |
| ADR-0007 | Deterministic permission engine outside the model |
| ADR-0008 | SSE for chat streaming; WebSocket reserved for voice |
| ADR-0009 | UUIDv7 primary keys |
| ADR-0010 | OpenTelemetry with GenAI conventions |
