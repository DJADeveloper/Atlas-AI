# Atlas — API Specification

> Deliverable 20. Conforms to [00-architecture-decisions.md](00-architecture-decisions.md).

---

## 1. API principles

1. **OpenAPI-first.** The FastAPI app (Pydantic v2 models) is the single source
   of the OpenAPI document; `packages/api-client` is **generated** from it and
   is the *only* way the UI talks to the API — no hand-rolled `fetch` calls, so
   contract drift becomes a TypeScript compile error, not a runtime surprise.
2. **Localhost-only.** The API binds to `127.0.0.1`; nothing listens on the
   network in local mode. Auth exists to stop *other local processes*.
3. **Traceable by construction.** Every response — success or failure, JSON or
   SSE — carries `X-Trace-Id`; error bodies repeat it as `trace_id`.
4. **Errors are contracts.** RFC 9457 problem+json with a stable
   machine-readable `code` (§3.5). Clients branch on `code`, never on prose.
5. **Uniform conventions.** Section 3 applies to *every* endpoint; endpoint
   sections document only deviations.
6. **Read-only by default.** The mutating surface is small, idempotency-guarded
   (§3.6), and audited wherever security-relevant.

## 2. Authentication

Local token auth (spine §5, `atlas/auth`), shaped so JWT/OIDC can slot in later
without changing routes. On first run the Tauri shell generates a **device
secret**, stores it in the OS keychain, and registers its hash in `api_keys`
(browser dev mode uses a pairing code printed by `atlas dev`).
`POST /api/v1/auth/token` exchanges the secret for a short-lived opaque
**Bearer token** (24 h, held in memory only); all other endpoints require
`Authorization: Bearer <token>`.

```json
// POST /api/v1/auth/token
{ "device_id": "desktop-mbp-darryl", "device_secret": "ds_9f2c…redacted" }
// 200
{ "access_token": "atk_0k3…", "token_type": "bearer", "expires_in": 86400 }
```

`GET /api/v1/auth/me` returns the authenticated principal:
`{ "user_id": "019f7b00-0d1e-7f2a-8b3c-4d5e6f708192", "device_id": "desktop-mbp-darryl", "profile": "hybrid" }`.

**401 vs 403 — fixed semantics.**

| Status | Meaning | Codes |
|---|---|---|
| `401 Unauthorized` | *Who are you?* Missing/expired/invalid token. Includes `WWW-Authenticate: Bearer`; client re-runs the token exchange. | `authentication_required`, `invalid_token` |
| `403 Forbidden` | *I know who you are; the answer is no.* Policy denial or feature flag off. Re-authenticating will not help. | `permission_denied`, `feature_disabled` |

## 3. Conventions

Stated once here; applied globally; never repeated per endpoint.

### 3.1 JSON casing — `snake_case`

All request and response fields are `snake_case`. The domain is Python/Pydantic
and the database is snake_case (spine §7); keeping **one canonical name per
field** across DB column, domain model, payload, log line, and generated TS
client eliminates a whole class of mapping bugs and makes `grep fused_score`
work across the monorepo. The alternative — camelCase at the edge plus a
translation layer — buys JavaScript aesthetics at the price of two names for
everything and a serializer that can silently drift. The generated client
preserves snake_case: the UI adapts, not the contract.

### 3.2 Timestamps and identifiers

Timestamps are RFC 3339, UTC, `Z` suffix, millisecond precision where
meaningful: `"2026-07-20T14:32:05.114Z"`. No local times, no epoch numbers on
the wire. Identifiers are UUIDv7, generated application-side (ADR-0009),
lowercase hyphenated: `019f8a3c-6b21-7d4e-8a2f-3c9d1e5b7a01` — time-ordered,
so ids sort by creation and make good cursor components.

### 3.3 Pagination — opaque cursor

Every list endpoint accepts `?limit=` (default 20, max 100) and `?cursor=`:

```json
{ "items": [ … ], "next_cursor": "eyJrIjoiMjAyNi0wNy0yMFQ…", "has_more": true }
```

`next_cursor` is `null` on the last page. The cursor is an opaque base64url
token encoding the sort-key values and id of the last row; clients treat it as
a black box. **Why cursor, not offset:** offset pagination is O(n) — Postgres
scans and discards every skipped row — and *unstable*: an insert between page
fetches shifts every later page, producing duplicates or gaps. A keyset cursor
is an index seek (O(log n)) and pins the boundary to a concrete row, so
concurrent writes (a background indexer is *always* writing in Atlas) cannot
corrupt iteration. The trade-off — no "jump to page 7" — is one no Atlas screen
needs; every list is an infinite scroll or a filtered drill-down.

### 3.4 Filtering

Simple filters are query params on GET lists (`?state=running`,
`?source_id=…`); time ranges use `_after`/`_before` suffixes with RFC 3339
values; multi-value filters repeat the parameter (`?state=failed&state=skipped`);
structured multi-field filters (search) go in a POST body. Default sort is
newest-first by `id` (UUIDv7 ⇒ creation order); alternatives are a documented
`?sort=` enum.

### 3.5 Error model — RFC 9457 + stable code registry

Content type `application/problem+json`, always this shape:

```json
{ "type": "https://atlas.dev/errors/approval_required", "title": "Approval required",
  "status": 409, "code": "approval_required",
  "detail": "fs.move is risk tier T2 and needs your approval.",
  "trace_id": "7f3a9c1e5b2d4f6a8c0e2a4b6d8f0a1c",
  "errors": [ { "field": "path", "message": "must be absolute" } ],
  "context": { "approval_id": "019f8c55-2e3f-7a4b-8c5d-6e7f8a9b0c1d" } }
```

`errors` appears only on `validation_error`; `context` carries machine-usable
follow-up data (approval id, budget numbers). The registry is **append-only** —
codes are never renamed or reused:

| `code` | HTTP | Meaning |
|---|---|---|
| `validation_error` | 422 | Request failed Pydantic validation; see `errors[]`. |
| `authentication_required` | 401 | No credentials presented. |
| `invalid_token` | 401 | Token expired, revoked, or malformed. |
| `permission_denied` | 403 | Deterministic policy denial — no grant covers the capability/scope. |
| `feature_disabled` | 403 | Feature flag or profile disables this endpoint (e.g. cloud calls in local-only). |
| `not_found` | 404 | Resource id does not exist (or is soft-deleted). |
| `conflict` | 409 | State conflict: duplicate source path, stale `updated_at`, already-decided approval. |
| `idempotency_key_reuse` | 409 | Same `Idempotency-Key` replayed with a *different* payload. |
| `stream_in_progress` | 409 | Conversation already has an active generation; wait or cancel. |
| `index_stale` | 409 | `require_fresh` search while ingestion jobs are pending in scope. |
| `agent_run_terminal` | 409 | Cancel/resume on a run already in a terminal state. |
| `approval_required` | 409 | Synchronous invocation parked pending approval; `context.approval_id`. |
| `approval_expired` | 410 | Decision posted after the approval TTL; invocation auto-canceled. |
| `payload_too_large` | 413 | Body exceeds limit (1 MiB JSON). |
| `unsupported_file_type` | 415 | No parser handles this file type (surfaced on jobs). |
| `budget_exceeded` | 402 | Daily or per-conversation cost cap reached; `context` has caps and spend. |
| `rate_limited` | 429 | Token bucket empty; honor `Retry-After`. |
| `provider_unavailable` | 503 | All providers in the fallback chain down / circuit open. |
| `provider_timeout` | 504 | Provider exceeded its deadline; request abandoned. |
| `internal_error` | 500 | Unhandled fault; `trace_id` is the bug-report handle. |

### 3.6 Idempotency keys

Every **mutating POST** accepts `Idempotency-Key: <uuid>` (the generated client
always sends one). The API stores `(route, key) → response` in Redis for 24 h.
Same key + same payload ⇒ the stored response is replayed (same status/body,
plus `Idempotent-Replayed: true`) — a retried "send message" does not create a
second turn; a retried "approve" does not double-execute a move. Same key +
different payload ⇒ `409 idempotency_key_reuse`. This is the standard defense
against "did my POST land before the connection dropped?" — the retry loop
becomes safe by construction, not by luck. PATCH/DELETE are naturally
idempotent and do not use the header.

### 3.7 Common headers

| Header | Direction | Notes |
|---|---|---|
| `Authorization: Bearer …` | request | All endpoints except `/health`, `/ready`, `/auth/token`. |
| `Idempotency-Key` | request | Mutating POSTs (§3.6). |
| `Last-Event-ID` | request | SSE resume (§4.3.5). |
| `X-Trace-Id` | response | Always. W3C trace id, 32 hex chars. |
| `RateLimit-Limit` / `-Remaining` / `-Reset` | response | §6. |
| `Retry-After` | response | On 429/503. |

---

## 4. Endpoint reference

Grouped exactly by spine §8. Paths omit the `/api/v1` prefix.

### 4.1 Health

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /health` | Liveness — process is up. | 200 `{ "status": "ok", "version": "0.14.2" }`. No auth. |
| `GET /ready` | Readiness — Postgres + migrations + Redis (+ Ollama, optional). | 200 `{ "status": "ready", "checks": { "postgres": "ok", "migrations": "ok", "redis": "ok", "ollama": "ok" } }`, or 503 with the failing check. No auth. |

### 4.2 Auth

Covered in §2. Errors: `invalid_token` 401 on a bad secret; `rate_limited` 429
on brute-force pacing (5 attempts/min).

### 4.3 Conversations & chat

#### `POST /conversations` — create a conversation

```json
{ "title": null, "project_id": "019f7c22-1d3e-7f4a-9b5c-6d7e8f9a0b1c" }
// 201
{ "id": "019f8a3c-6b21-7d4e-8a2f-3c9d1e5b7a01", "title": null,
  "project_id": "019f7c22-1d3e-7f4a-9b5c-6d7e8f9a0b1c", "message_count": 0,
  "created_at": "2026-07-20T14:30:11.021Z", "updated_at": "2026-07-20T14:30:11.021Z" }
```

`title` is auto-generated after the first turn by `claude-haiku-4-5-20251001`
(local profile: `llama3.1:8b`). 201; errors: `not_found` (project),
`validation_error`.

#### Read endpoints

| Endpoint | Purpose |
|---|---|
| `GET /conversations` | Cursor-paginated summaries (id, title, project_id, message_count, last_message_at); filters `?project_id=`, `?updated_after=`. |
| `GET /conversations/{id}` | Metadata for one conversation; 404 `not_found`. |
| `GET /conversations/{id}/messages` | Persisted messages, cursor-paginated, oldest-first within the window. |

Message item shape:

```json
{ "id": "019f8a3c-72b8-7f60-8c4b-5e1f3a7d9c23", "role": "assistant",
  "content": "You anchored Pro at $12/mo [1] …", "abstained": false,
  "citations": [ { "marker": 1, "chunk_id": "019f89d4-4e91-7b2c-9f3a-0b1c2d3e4f5a",
      "document_id": "019f89d4-33c5-7a1b-8d2e-6f7a8b9c0d1e",
      "document_title": "pricing-notes.md", "snippet": "…anchor Pro at $12/mo…" } ],
  "usage": { "model": "claude-sonnet-5", "input_tokens": 3812, "output_tokens": 402,
             "cost_usd": 0.0174 },
  "created_at": "2026-07-20T14:32:09.871Z" }
```

#### `POST /conversations/{id}/messages` — send a message (SSE response)

The flagship endpoint. Request:

```json
{ "content": "What did we decide about Pro pricing, and who signed off?" }
```

Response: `200` with `Content-Type: text/event-stream` (protocol below).
Pre-stream failures are plain problem+json: `budget_exceeded` 402 (caps checked
*before* the LLM call), `stream_in_progress` 409, `not_found` 404,
`validation_error` 422. `Idempotency-Key` required — a replayed key re-attaches
to the existing stream instead of generating a second answer.

##### 4.3.1 Why SSE and not WebSocket (ADR-0008)

Token streaming is strictly server→client. SSE rides plain HTTP: the same
Bearer header, the same `X-Trace-Id`, HTTP/2 multiplexing, curl-debuggable, and
a *built-in* resume protocol (`Last-Event-ID`) that WebSocket makes you invent.
WebSocket buys bidirectionality chat doesn't need and costs a second
auth/keepalive/backpressure story. Voice (S4) genuinely is bidirectional
low-latency audio — that is why `WS /ws/voice` exists and why WebSocket is
*reserved* for it. The generated client streams via `fetch` + a reader, not
`EventSource` (which cannot set the `Authorization` header).

##### 4.3.2 Stream framing

The server opens with `retry: 3000`, sends a `: ping` comment every 15 s to
defeat idle proxy timeouts, and gives every event a monotonically increasing
integer `id` (the resume cursor). Every `data:` line is one JSON object.

##### 4.3.3 Event types

| Event | Payload schema | Emitted |
|---|---|---|
| `message_start` | `{ "message_id", "conversation_id", "model", "prompt_version", "trace_id", "created_at" }` | Once, first. |
| `content_delta` | `{ "index": int, "delta": string }` | Many; `index` orders fragments. |
| `citation` | `{ "marker": int, "chunk_id", "document_id", "document_title", "source_id", "snippet", "fused_score": float }` | As `[n]` markers resolve; matches a persisted `citations` row. |
| `tool_request` | `{ "invocation_id", "tool", "capability", "risk_tier", "arguments": object }` | When the model emits a validated tool intent. |
| `approval_required` | `{ "approval_id", "invocation_id", "capability", "risk_tier", "summary", "preview": object\|null, "expires_at" }` | When policy parks a T2/T3 invocation for a human. |
| `usage` | `{ "model", "provider", "prompt_version", "input_tokens", "output_tokens", "cost_usd", "latency_ms", "stop_reason" }` | Once, before `message_end`. |
| `message_end` | `{ "message_id", "stop_reason", "citation_count", "abstained": bool }` | Once, last on success. |
| `error` | RFC 9457 body (§3.5) as the data payload | Terminal on mid-stream failure. |

Clients MUST ignore unknown event types — that is how the protocol stays
additive (§5).

##### 4.3.4 Example transcript

```text
retry: 3000

id: 0
event: message_start
data: {"message_id":"019f8a3c-72b8-7f60-8c4b-5e1f3a7d9c23","conversation_id":"019f8a3c-6b21-7d4e-8a2f-3c9d1e5b7a01","model":"claude-sonnet-5","prompt_version":"grounded_chat.v7","trace_id":"7f3a9c1e5b2d4f6a8c0e2a4b6d8f0a1c","created_at":"2026-07-20T14:32:06.402Z"}

id: 1
event: content_delta
data: {"index":0,"delta":"You anchored Pro at $12/mo on 2026-07-14 [1]"}

id: 2
event: citation
data: {"marker":1,"chunk_id":"019f89d4-4e91-7b2c-9f3a-0b1c2d3e4f5a","document_id":"019f89d4-33c5-7a1b-8d2e-6f7a8b9c0d1e","document_title":"pricing-notes.md","source_id":"019f89d1-2a4b-7c8d-9e0f-1a2b3c4d5e6f","snippet":"…decided to anchor Pro at $12/mo…","fused_score":0.0325}

id: 3
event: content_delta
data: {"index":1,"delta":", signed off by Maya in the same doc [1]."}

id: 4
event: usage
data: {"model":"claude-sonnet-5","provider":"anthropic","prompt_version":"grounded_chat.v7","input_tokens":3812,"output_tokens":402,"cost_usd":0.0174,"latency_ms":2916,"stop_reason":"end_turn"}

id: 5
event: message_end
data: {"message_id":"019f8a3c-72b8-7f60-8c4b-5e1f3a7d9c23","stop_reason":"end_turn","citation_count":1,"abstained":false}
```

##### 4.3.5 Reconnection & `Last-Event-ID`

Generation is **detached from the HTTP connection**: the use case appends every
event to a Redis Stream keyed by message id, and the handler tails it. If the
connection drops, the model keeps generating. The client resumes with
`GET /messages/{message_id}/stream` sending `Last-Event-ID: 3`; the server
replays buffered events with id > 3, then continues live. The buffer is
retained 15 minutes after `message_end`; after that the endpoint returns 404
`not_found` and the client falls back to `GET /conversations/{id}/messages`,
where the finished message is already persisted. Nothing is lost — the buffer
is a latency optimization over the database, not the source of truth.

#### `POST /messages/{id}/feedback` — rate an answer

```json
{ "rating": "down", "categories": ["wrong_citation"], "comment": "That quote is from the 2025 doc." }
```

201; writes a `feedback` row (feeds the eval datasets). Errors: `not_found`,
`validation_error`.

### 4.4 Search & documents

#### `POST /search` — hybrid search

POST because the filter object outgrows a query string. Full request:

```json
{ "query": "pro pricing decision",
  "filters": { "source_ids": ["019f89d1-2a4b-7c8d-9e0f-1a2b3c4d5e6f"],
    "project_id": "019f7c22-1d3e-7f4a-9b5c-6d7e8f9a0b1c", "file_types": ["md", "pdf"],
    "modified_after": "2026-01-01T00:00:00Z", "modified_before": "2026-07-20T00:00:00Z" },
  "top_k": 8, "rerank": true, "require_fresh": false }
// 200
{ "results": [
    { "chunk_id": "019f89d4-4e91-7b2c-9f3a-0b1c2d3e4f5a",
      "content": "We decided to anchor Pro at $12/mo, Team at $29/seat…",
      "highlight": "decided to anchor <em>Pro</em> at <em>$12/mo</em>",
      "scores": { "vector_score": 0.83, "fts_rank": 0.61, "fused_score": 0.0325,
                  "rerank_score": 0.94 },
      "document": { "id": "019f89d4-33c5-7a1b-8d2e-6f7a8b9c0d1e",
        "title": "pricing-notes.md", "path": "notes/pricing-notes.md",
        "source_id": "019f89d1-2a4b-7c8d-9e0f-1a2b3c4d5e6f", "file_type": "md",
        "modified_at": "2026-07-14T09:12:44Z" } } ],
  "retrieval": { "vector_candidates": 24, "fts_candidates": 24, "fused": 31,
    "reranked": true, "abstain_recommended": false, "latency_ms": 142,
    "index_freshness": { "pending_jobs": 0, "as_of": "2026-07-20T14:31:58.030Z" } } }
```

All scores are returned so the UI (and evals) can explain *why* a chunk ranked:
`vector_score` is pgvector cosine similarity, `fts_rank` is `ts_rank_cd`,
`fused_score` is RRF with k=60 (hence the ~0.03 magnitude), `rerank_score` is
the cross-encoder (null when `rerank: false`). `abstain_recommended` mirrors
the chat abstention gate. `require_fresh: true` returns 409 `index_stale` when
the filtered scope has pending ingestion jobs. Errors: `validation_error`,
`not_found` (bad filter ids).

#### `GET /documents/{id}` — document + latest version metadata

```json
// 200
{ "id": "019f89d4-33c5-7a1b-8d2e-6f7a8b9c0d1e",
  "source_id": "019f89d1-2a4b-7c8d-9e0f-1a2b3c4d5e6f",
  "path": "notes/pricing-notes.md", "title": "pricing-notes.md", "file_type": "md",
  "latest_version": { "id": "019f89d4-41a2-7c3d-8e4f-5a6b7c8d9e0f",
    "content_hash": "sha256:9c4f…", "chunk_count": 14, "indexed_at": "2026-07-14T09:13:02.511Z" },
  "created_at": "2026-05-02T10:01:33.870Z", "updated_at": "2026-07-14T09:13:02.511Z" }
```

#### `GET /documents/{id}/chunks` — chunks of the latest version

Cursor-paginated; items carry `chunk_index`, `content`, `token_count`,
`heading_path` (e.g. `["Pricing", "Decision"]`). 404 `not_found`.

### 4.5 Sources

#### `POST /sources` — register a watched folder

Requires an `fs.read` grant covering `path` (created beforehand via
`POST /permissions` in the UI wizard — grants come only from explicit user
action, spine §11).

```json
{ "kind": "folder", "name": "Project notes", "path": "/Users/darryl/Projects/atlas-notes",
  "include": ["**/*.md", "**/*.pdf", "**/*.docx"],
  "exclude": ["**/node_modules/**", "**/.git/**"] }
// 201
{ "id": "019f89d1-2a4b-7c8d-9e0f-1a2b3c4d5e6f", "kind": "folder",
  "name": "Project notes", "path": "/Users/darryl/Projects/atlas-notes",
  "state": "indexing", "paused": false, "document_count": 0,
  "initial_scan_job_id": "019f89d1-3b5c-7d6e-8f70-2a3b4c5d6e7f",
  "created_at": "2026-07-20T09:15:20.114Z" }
```

Errors: `permission_denied` 403 (no covering grant), `conflict` 409 (path
already registered), `validation_error` 422 (relative path, missing dir).

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /sources` | List with `state`, `document_count`, `last_indexed_at`. | Filter `?state=`. |
| `GET /sources/{id}` | Fetch one. | 404 `not_found`. |
| `PATCH /sources/{id}` | Update `name`, `include`, `exclude`, `paused`. | Pattern change enqueues a rescan; 200. |
| `DELETE /sources/{id}` | Unregister; async purge of documents/chunks. | 202 with `purge_job_id`. |
| `POST /sources/{id}/reindex` | Body `{ "force": false }`; `force: true` bypasses the hash gate. | 202 with `job_id`; `conflict` if a scan is running. |

### 4.6 Jobs

| Endpoint | Purpose |
|---|---|
| `GET /jobs` | List; filters `?source_id=`, `?state=` (`pending/running/succeeded/failed/skipped`), `?kind=` (`scan/ingest/reindex/purge`). |
| `GET /jobs/{id}` | Fetch one with progress and error detail. |

```json
// GET /jobs/019f89d1-3b5c-7d6e-8f70-2a3b4c5d6e7f → 200
{ "id": "019f89d1-3b5c-7d6e-8f70-2a3b4c5d6e7f", "kind": "scan",
  "source_id": "019f89d1-2a4b-7c8d-9e0f-1a2b3c4d5e6f", "state": "running",
  "progress": { "documents_total": 412, "documents_done": 268, "chunks_written": 3120,
                "skipped_unchanged": 61, "failed": 1 },
  "error": null, "attempt": 1, "max_attempts": 3,
  "created_at": "2026-07-20T09:15:20.290Z", "started_at": "2026-07-20T09:15:21.008Z",
  "finished_at": null }
```

Failed jobs keep their last `error` (e.g. `{ "code": "unsupported_file_type",
"path": "notes/old.hwp" }`) and are retried per policy before parking in
`failed` — never silently dropped.

### 4.7 Projects

CRUD: `POST /projects`, `GET /projects`, `GET /projects/{id}`,
`PATCH /projects/{id}`, `DELETE /projects/{id}` (soft delete), plus membership:

```json
// POST /projects/{id}/documents
{ "document_ids": ["019f89d4-33c5-7a1b-8d2e-6f7a8b9c0d1e"] }
// 200 → { "added": 1, "already_present": 0 }
```

`DELETE /projects/{id}/documents/{document_id}` detaches (204). A project
carries its own memory scope; project-filtered search and chat pass
`project_id`. Errors: `not_found`, `validation_error`, `conflict` (duplicate name).

### 4.8 Memories

| Endpoint | Purpose |
|---|---|
| `GET /memories` | List; filters `?kind=`, `?project_id=`, `?pinned=true`. |
| `POST /memories` | Create (direct user action, or UI confirm of an in-chat proposal). |
| `PATCH /memories/{id}` | Edit `content`, `pinned`, `kind`. |
| `DELETE /memories/{id}` | Forget. 204; audited. |

```json
// POST /memories
{ "kind": "preference",
  "content": "Prefers concise answers with bullet points over long prose",
  "project_id": null,
  "provenance": { "conversation_id": "019f8a3c-6b21-7d4e-8a2f-3c9d1e5b7a01",
                   "message_id": "019f8a3c-72b8-7f60-8c4b-5e1f3a7d9c23" } }
// 201 — echoes the fields above plus:
{ "id": "019f8b10-5c6d-7e8f-9a0b-1c2d3e4f5a6b", "pinned": false, "confidence": 0.9,
  "created_at": "2026-07-20T14:35:40.226Z", "…": "…" }
```

Kinds are the spine's closed set: `preference | project_fact | decision |
entity | episodic`. In-chat capture flows through the `memory.remember` tool
with user confirmation ([13-sequence-flows.md](13-sequence-flows.md), flow 8).

### 4.9 Tools, permissions, approvals

#### `GET /tools` — the tool registry

```json
{ "items": [
    { "name": "fs.move", "capability": "fs.write.move", "risk_tier": "T2",
      "description": "Move or rename a file within granted scopes", "reversible": true,
      "parameters_schema": { "type": "object", "required": ["src", "dst"],
        "properties": { "src": { "type": "string" }, "dst": { "type": "string" } } } } ],
  "next_cursor": null }
```

#### Permissions

| Endpoint | Purpose |
|---|---|
| `GET /permissions` | List active grants; filter `?capability=`. |
| `POST /permissions` | Create a grant — always an explicit user action from the UI. |
| `DELETE /permissions/{id}` | Revoke (immediate; running invocations re-checked). 204; audited. |

```json
// POST /permissions
{ "capability": "fs.read", "scope": { "paths": ["/Users/darryl/Projects/atlas-notes/**"] },
  "mode": "allow", "expires_at": null }
// 201 — echoes the grant plus:
{ "id": "019f89c0-1122-7334-8556-778899aabbcc",
  "created_at": "2026-07-20T09:14:58.402Z", "…": "…" }
```

#### Approvals

`GET /approvals?status=pending` lists the human work queue:

```json
{ "items": [
    { "id": "019f8c55-2e3f-7a4b-8c5d-6e7f8a9b0c1d",
      "tool_invocation_id": "019f8c55-1a2b-7c3d-9e4f-5a6b7c8d9e0f",
      "capability": "fs.write.move", "risk_tier": "T2",
      "summary": "Move 3 screenshots from Downloads to Projects/atlas-notes/img",
      "preview": { "moves": [ { "src": "/Users/darryl/Downloads/shot1.png",
                                 "dst": "/Users/darryl/Projects/atlas-notes/img/shot1.png" } ] },
      "status": "pending", "requested_by": "conversation:019f8a3c-6b21-7d4e-8a2f-3c9d1e5b7a01",
      "expires_at": "2026-07-20T14:51:00.000Z", "created_at": "2026-07-20T14:36:00.113Z" } ],
  "next_cursor": null }
```

`POST /approvals/{id}/decision`:

```json
{ "decision": "approve", "reason": "These are all screenshots, correct target" }
// or
{ "decision": "deny", "reason": "Wrong target folder" }
```

200 returns the approval with `status: approved|denied`, `decided_at`, and the
resumed/canceled invocation id. **Expiry behavior:** pending approvals expire
after `settings.approvals.pending_ttl_minutes` (default 15); expiry
auto-cancels the invocation, writes an `audit_events` row, and a late decision
returns 410 `approval_expired`. T3 approvals additionally require
`"confirmation_text"` matching the rendered preview's typed challenge. Errors:
`conflict` 409 (already decided), `not_found`.

### 4.10 Agent runs

#### `POST /agent-runs` — start a run

```json
{ "goal": "Organize my Downloads folder by file type",
  "project_id": null, "max_steps": 20, "budget_usd": 0.50 }
// 201
{ "id": "019f8d77-0a1b-7c2d-8e3f-4a5b6c7d8e9f", "status": "queued",
  "goal": "Organize my Downloads folder by file type",
  "created_at": "2026-07-20T15:02:10.550Z" }
```

Errors: `budget_exceeded` 402, `validation_error`. `GET /agent-runs` lists
(filter `?status=`); `GET /agent-runs/{id}` returns detail: `status`
(`queued|running|awaiting_approval|succeeded|failed|canceled`),
`steps_completed`, `cost_usd`, `last_checkpoint_at`, `error`.

#### `GET /agent-runs/{id}/steps` — the steps timeline

Cursor-paginated, ascending `step_no`. The canonical timeline shape:

```json
{ "items": [
    { "id": "019f8d77-1b2c-7d3e-8f4a-5b6c7d8e9f0a", "step_no": 1, "kind": "thought",
      "status": "succeeded",
      "content": { "text": "Downloads has 214 files; group by extension family." },
      "started_at": "2026-07-20T15:02:12.001Z", "ended_at": "2026-07-20T15:02:14.310Z",
      "span_id": "a1b2c3d4e5f60718" },
    { "id": "019f8d77-2c3d-7e4f-9a5b-6c7d8e9f0a1b", "step_no": 2, "kind": "tool",
      "status": "succeeded",
      "tool_invocation": { "id": "019f8c55-1a2b-7c3d-9e4f-5a6b7c8d9e0f",
        "tool": "fs.list", "capability": "fs.read", "risk_tier": "T0",
        "arguments": { "path": "/Users/darryl/Downloads" }, "approval_id": null,
        "undo_journal_id": null },
      "checkpoint_id": "019f8d77-2d4e-7f50-8a6b-7c8d9e0f1a2b",
      "started_at": "2026-07-20T15:02:14.402Z", "ended_at": "2026-07-20T15:02:14.980Z",
      "span_id": "b2c3d4e5f6071829" },
    { "id": "019f8d77-3e4f-7a5b-8c6d-7e8f9a0b1c2d", "step_no": 3, "kind": "observation",
      "status": "succeeded", "content": { "summary": "214 entries returned", "truncated": true },
      "started_at": "2026-07-20T15:02:14.985Z", "ended_at": "2026-07-20T15:02:15.020Z",
      "span_id": "c3d4e5f607182930" } ],
  "next_cursor": null }
```

Every `tool` step carries its checkpoint id — the write-ahead record that makes
crash recovery possible (flow 7). `POST /agent-runs/{id}/cancel` → 202 (best
effort; the current step finishes or rolls back); 409 `agent_run_terminal` if
already finished.

### 4.11 Evals

`POST /eval-runs` starts a run (202); `GET /eval-runs` lists (filter
`?suite=`, `?status=`); `GET /eval-runs/{id}` returns the scorecard.

```json
// POST /eval-runs
{ "suite": "retrieval", "dataset_id": "019f6e00-4a5b-7c6d-8e7f-9a0b1c2d3e4f",
  "baseline_run_id": "019f8000-5b6c-7d7e-8f80-0a1b2c3d4e5f",
  "notes": "PR 412 — chunking overlap change" }
// 202 → { "id": "019f8e88-1b2c-7d3e-9f4a-5b6c7d8e9f0a", "status": "running" }

// GET /eval-runs/019f8e88-1b2c-7d3e-9f4a-5b6c7d8e9f0a → 200
{ "id": "019f8e88-1b2c-7d3e-9f4a-5b6c7d8e9f0a", "suite": "retrieval",
  "status": "succeeded", "dataset_id": "019f6e00-4a5b-7c6d-8e7f-9a0b1c2d3e4f",
  "example_count": 180,
  "metrics": { "recall_at_8": 0.91, "mrr_at_8": 0.74, "ndcg_at_8": 0.79,
               "abstention_accuracy": 0.88, "groundedness": 0.93 },
  "baseline_run_id": "019f8000-5b6c-7d7e-8f80-0a1b2c3d4e5f",
  "baseline_delta": { "recall_at_8": -0.006, "mrr_at_8": 0.011, "ndcg_at_8": 0.004,
                      "abstention_accuracy": 0.0, "groundedness": -0.002 },
  "thresholds_passed": true, "cost_usd": 0.42, "git_sha": "b7e412c",
  "created_at": "2026-07-20T16:00:03.114Z", "finished_at": "2026-07-20T16:07:41.660Z" }
```

Per-example rows (`eval_results`) are queryable via
`GET /eval-runs/{id}?include=failures`, which appends failing examples with
expected/actual for the PR diff report (flow 9).

### 4.12 Settings & audit

#### `GET /settings` / `PATCH /settings`

```json
// GET /settings → 200
{ "profile": "hybrid",
  "models": { "chat": "claude-sonnet-5", "escalation": "claude-opus-4-8",
              "routing": "claude-haiku-4-5-20251001", "embedding": "nomic-embed-text" },
  "budgets": { "daily_cost_cap_usd": 5.0, "per_conversation_cap_usd": 1.0 },
  "retrieval": { "top_k": 8, "rerank": true, "abstention_threshold": 0.018 },
  "approvals": { "pending_ttl_minutes": 15 },
  "telemetry": { "langsmith_export": false } }
```

PATCH is a partial update of the same shape; switching `profile` to
`local_only` is audited and takes effect on the next request (in-flight cloud
calls complete). Errors: `validation_error` (unknown model id, negative caps).

#### `GET /audit-events` — the explainability ledger

Read-only, append-only underneath, cursor-paginated. Filters: `?category=`
(`auth|permission|tool|approval|source|settings|memory`), `?actor=`,
`?occurred_after=`, `?occurred_before=`.

```json
{ "items": [
    { "id": "019f8f99-2c3d-7e4f-8a5b-6c7d8e9f0a1b",
      "occurred_at": "2026-07-20T14:36:00.120Z", "category": "tool",
      "action": "policy.denied", "actor": "agent_run:019f8d77-0a1b-7c2d-8e3f-4a5b6c7d8e9f",
      "subject": { "capability": "terminal.run",
                   "tool_invocation_id": "019f8c55-4b5c-7d6e-8f7a-8b9c0d1e2f3a" },
      "details": { "reason": "no_grant" },
      "trace_id": "8a4b0d2f6c3e5a7b9d1f3a5c7e9b0d2f" } ],
  "next_cursor": "eyJrIjoi…" }
```

### 4.13 Voice (S4)

`WS /ws/voice` — the one WebSocket, reserved for bidirectional audio frames and
barge-in control (ADR-0008). Returns 403 `feature_disabled` until the voice
flag ships at M21; the protocol is specified with that milestone, not here.

---

## 5. Versioning policy

- Base path `/api/v1`. **Once the desktop app ships, `/api/v1` is frozen** in
  the n-1 sense: during auto-update the UI and sidecar may skew by one release,
  so every `/v1` release must serve the previous release's client.
- **Additive changes are allowed** without a version bump: new endpoints, new
  optional request fields, new response fields, new SSE event types, new error
  `code`s, new values on enums documented as open. The client obligations are
  contractual: ignore unknown response fields, unknown SSE events, and unknown
  values on open enums.
- **Breaking changes** — removing/renaming a field, changing a type or meaning,
  tightening validation, closing an enum — require `/api/v2` mounted
  side-by-side; `/v1` continues until every first-party surface has migrated.
- CI runs an OpenAPI diff against the last released spec and fails the PR on
  any non-additive change to `/v1` — the same gate pattern as the evals.

## 6. Rate limiting

Token bucket per bearer token, kept in Redis. On localhost with one user this
mostly guards against runaway scripts and agent loops; the mechanism is the one
a future multi-user deployment needs, so it ships now and the limits simply
tighten later.

| Bucket | Default |
|---|---|
| General requests | 120/min, burst 40 |
| LLM-backed POSTs (`…/messages`, `/agent-runs`, `/eval-runs`) | 20/min, burst 5 |
| Concurrent SSE streams | 4 |
| `POST /auth/token` | 5/min |

Every response carries `RateLimit-Limit`, `RateLimit-Remaining`,
`RateLimit-Reset` (IETF draft headers); exceeding a bucket returns 429
`rate_limited` with `Retry-After`. Note that *cost* budgets (`budget_exceeded`,
402) are a separate mechanism: rate limits protect the process, budgets protect
the wallet.

## 7. Decisions made in this document

Choices not already pinned by the spine (simplest consistent option taken):

1. **Obvious sub-endpoints added:** `GET /conversations/{id}/messages`,
   `GET /messages/{id}/stream` (SSE resume), `GET /sources/{id}`,
   `GET /agent-runs` (list), `GET /projects/{id}`,
   `DELETE /projects/{id}/documents/{document_id}`.
2. **Auth details:** device-secret-in-keychain → 24 h opaque bearer token via
   `POST /auth/token`; hashed secrets in `api_keys`.
3. **Error registry values:** the 20-code table in §3.5 with HTTP mappings
   (`budget_exceeded` → 402, `approval_required` → 409, `approval_expired` → 410,
   `index_stale` → 409).
4. **Idempotency:** `Idempotency-Key` required on LLM-triggering POSTs, accepted
   on all mutating POSTs; Redis-backed replay, 24 h TTL; payload mismatch → 409.
5. **Pagination defaults:** limit 20 default / 100 max; cursor = base64url
   keyset token; `next_cursor: null` terminates.
6. **SSE mechanics:** integer event ids; Redis Stream buffer kept 15 min
   post-completion; `retry: 3000`; 15 s pings; generation detached from the
   connection.
7. **Approval TTL:** default 15 min (`settings.approvals.pending_ttl_minutes`);
   expiry auto-cancels the invocation; T3 adds typed confirmation text.
8. **Rate-limit defaults** as tabulated in §6.
9. **Search extras:** `require_fresh` flag backed by `index_stale`, and an
   `index_freshness` block in every search response.
10. **`DELETE /sources` returns 202** with an async purge job rather than
    blocking on chunk deletion.
