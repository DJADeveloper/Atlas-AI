# Atlas — Scaling Strategy

> Deliverable 24. Conforms to [00-architecture-decisions.md](00-architecture-decisions.md).

---

## 1. How Atlas thinks about scale

Atlas scales along two axes that are usually conflated: **corpus size**
(documents → chunks → index) and **user count** (one laptop owner → a team →
tenants). The stages below climb both, and the recurring theme is that the
expensive decisions were already paid for at design time: `workspace_id`
columns from M03, queue names as scaling seams, ports around every vendor,
Temporal at M19. At each stage the honest question is "what *configuration*
changes?" — and only rarely "what *code* changes?"

```mermaid
graph LR
    S1[Stage 1<br/>single user · 10k docs] --> S2[Stage 2<br/>power user · 1M chunks]
    S2 --> S3[Stage 3<br/>team server · 5 to 50 users]
    S3 --> S4[Stage 4<br/>cloud SaaS · thousands]
```

| Stage | Corpus | Users | Hardware | What changes |
|---|---|---|---|---|
| 1 | ≤10k docs, ~150k chunks | 1 | laptop | nothing — defaults hold |
| 2 | 100k–1M chunks | 1 | laptop / workstation, maybe GPU | index tuning, embed throughput, re-index discipline |
| 3 | ~1–5M chunks total | 5–50 | home/office server | auth on, pgbouncer, worker scale-out — configuration, not migration |
| 4 | many tenants × Stage-2 corpora | thousands | cloud | managed services, RLS tenancy, service extraction begins |

## 2. Stage 1 — single user, ~10k documents

Roughly 10k documents × ~15–20 chunks each ≈ **150–200k chunks**. This is the
design center, and the defaults are chosen so that *nothing needs attention*:

- **Index:** HNSW `m=16`, `ef_construction=64`, `ef_search=40`
  ([41-infrastructure-architecture.md](41-infrastructure-architecture.md) §6).
  200k × 768d float32 vectors ≈ 0.6 GB raw; index comfortably page-cached
  inside the desktop Postgres budget. Vector query latency: single-digit ms;
  hybrid retrieval end-to-end (vector ∥ FTS → RRF → optional rerank) p95
  well under 150 ms, dominated by the reranker when enabled.
- **Ingestion:** initial index of 10k docs is an evening on CPU (§3.2 math),
  incremental after that — the watcher plus content-hash skip means a normal
  day's edits are seconds of work.
- **Operations:** none. Autovacuum defaults plus the per-table `chunks`
  overrides are already in place; backups run nightly; no tuning session
  required. If a Stage-1 user ever has to read this document, we shipped a bug.

The discipline at this stage is *restraint*: no partitioning, no pgbouncer, no
replica — machinery without load is pure liability (more failure modes, no
benefit).

## 3. Stage 2 — power user, 100k–1M chunks

The corpus of a researcher with two decades of PDFs, or a codebase-heavy
setup with several large repos. Postgres is nowhere near its limits, but
defaults start leaving recall and hours on the table.

### 3.1 Index tuning that actually moves numbers

- **`ef_search`: the first knob, not the last.** Raise 40 → 80–120 when the
  eval suite's recall gate slips. Cost is roughly linear in query latency
  (sub-ms → a few ms at 1M rows) — cheap insurance, applied per query via
  `SET LOCAL`, no rebuild.
- **`m` and `ef_construction`: rebuild-time knobs.** At ~1M chunks,
  `m=16, ef_construction=64` typically holds recall@24 ≥ 0.95 against exact
  search on text-embedding workloads; if evals disagree, rebuild at
  `ef_construction=128` first (better graph, same memory), and only then
  consider `m=24` (more memory, better high-recall behavior). Every rebuild
  is measured: build time with `maintenance_work_mem=1GB`, then recall and
  p95 from the eval suite — never tune on vibes (spine §2.6).
- **Partial indexes per workspace.** Retrieval always filters by
  `workspace_id`. HNSW scans degrade when a filter discards most candidates
  (the graph walk returns neighbors that then fail the predicate). With a
  handful of workspaces, partial HNSW indexes
  (`WHERE workspace_id = …`) on the dominant workspaces restore
  per-workspace recall and speed at the cost of index storage per workspace —
  worth it from a few hundred thousand chunks per workspace. (Postgres also
  gains from pgvector's iterative filtered scan; partial indexes remain the
  predictable option.)
- **Partitioning `chunks`: considered, deferred.** Declarative partitioning
  by `workspace_id` would localize churn (a re-indexed source only bloats its
  partition) and make workspace deletion `DROP PARTITION`-cheap. But pgvector
  indexes are per-partition, cross-partition queries lose the single-graph
  advantage, and operational complexity rises sharply. Verdict: **not at
  Stage 2**; revisit at Stage 3+ only if churn-driven bloat maintenance
  (concurrent reindex cadence) becomes disruptive. This is written down so
  future-us knows it was a decision, not an oversight.

### 3.2 Embedding throughput math — the real Stage-2 bottleneck

Assume ~512-token chunks, `nomic-embed-text` via Ollama, batches of 32–64:

| Hardware | Throughput | 1M-chunk full re-embed | Sustained docs per hour |
|---|---|---|---|
| 8-core laptop CPU | ~5–15 chunks/s | ~19–55 h | ~900–2,700 (at ~20 chunks/doc) |
| Apple Silicon (M-series GPU) | ~30–80 chunks/s | ~3.5–9 h | ~5,000–14,000 |
| NVIDIA GPU, 8GB+ | ~50–150 chunks/s | ~2–5.5 h | ~9,000–27,000 |

(Illustrative orders of magnitude for planning, not benchmarks; the eval rig
measures real numbers per machine.)

Three consequences:

1. **Incremental-only discipline is non-negotiable.** A full re-embed is a
   *days*-scale event on CPU. The content-hash pipeline already guarantees
   only changed content re-embeds; Stage 2 makes this a hard rule — the only
   sanctioned full re-embeds are an embedding-model change (an explicit,
   scheduled migration per spine §7) or disaster recovery.
2. **Batch sizing matters more than concurrency.** One embed worker with
   batches of 32–64 saturates the accelerator; concurrency 2 just thrashes
   ([41-infrastructure-architecture.md](41-infrastructure-architecture.md) §3.2).
3. **A GPU is the honest advice** for a 1M-chunk user: it turns initial
   indexing from a weekend into a lunch break. Atlas surfaces measured
   embed throughput in the jobs UI so users can see what their hardware does.

### 3.3 Everything else at Stage 2

FTS (GIN) stays fast at 1M rows with no attention. `shared_buffers` rises to
1–2 GB on a workstation (desktop budget in
[41-infrastructure-architecture.md](41-infrastructure-architecture.md) §8 is a
floor, not a law). Retrieval cache hit rates start mattering; the 60s TTL is
already right. Backup dumps grow to a few GB — still nightly-friendly.

## 4. Stage 3 — self-host team server, 5–50 users

The same containers from the self-host target
([40-deployment-architecture.md](40-deployment-architecture.md) §4), now with
concurrency. **The headline: this stage is configuration, not migration** —
`workspace_id` columns exist on every scoped table from M03 *precisely so
that this sentence is true*, and `permission_grants` (principal, capability,
scope — spine §11) already model per-user permissions.

What actually turns on:

- **Multi-user auth activates.** The `auth/` module's token issuance grows
  real user records; interfaces were OIDC/JWT-ready from the start, so
  wiring a team IdP is an adapter, not a redesign. Every request resolves a
  principal; every query is workspace-scoped; the policy engine evaluates
  grants per principal exactly as it did for one.
- **Row scoping is enforcement, not convention.** Repository-level mandatory
  `workspace_id` predicates (already the query pattern) get a second lock:
  Postgres RLS policies can be enabled here as defense-in-depth — the same
  mechanism Stage 4 relies on, rehearsed early where the blast radius is a
  team, not a customer base.
- **Connection pooling via pgbouncer** (transaction mode) in front of
  Postgres. 50 users × api replicas × worker pools would exhaust naive
  connection counts; pgbouncer holds server connections at ~2× cores.
  Transaction pooling forbids session state — which Atlas's repository
  layer already respects, with the one exception of `SET LOCAL
  hnsw.ef_search`, which is transaction-scoped and therefore pgbouncer-safe
  by construction.
- **Workers scale horizontally.** Queue names become worker pools: 2× parse,
  1× embed per GPU, 1× agent, 1× maintenance — Compose `deploy.replicas` or
  plain duplicate services. Beat runs exactly once, as its own container.
- **Sizing point:** a 50-user office on one 8-core/32 GB server with a
  mid-range GPU is comfortable: chat is bursty (§7) so concurrent LLM streams
  rarely exceed single digits, and retrieval at ~1–5M total chunks fits the
  Stage-2 playbook per workspace.

## 5. Stage 4 — cloud SaaS, thousands of users (M25+ territory)

Post-1.0, and the first stage where architecture *changes* rather than
*dials up*.

### 5.1 Tenant isolation — the decision that shapes everything

| Model | Isolation | Cost per tenant | Migration burden | Noisy neighbor | Verdict |
|---|---|---|---|---|---|
| Shared schema + RLS on `workspace_id` | logical, DB-enforced | ~zero | one schema, one Alembic history | shared buffers/IO — mitigated by limits | **start here** |
| Schema-per-tenant | stronger logical | low | N× migrations, N× pgvector indexes; tooling burden grows with tenant count | shared instance still | niche middle: rarely worth it |
| DB-per-tenant | physical | highest | fleet orchestration | none | reserved for enterprise/regulated tier |

**Recommendation: shared schema + RLS initially.** Rationale: every table has
carried `workspace_id` since M03, so RLS policies
(`USING workspace_id = current_setting('atlas.workspace_id')::uuid`) drop onto
the existing shape; one Alembic history stays one; per-tenant marginal cost
stays near zero, which §6 of [43-cost-strategy.md](43-cost-strategy.md)
depends on. RLS becomes the *second* enforcement layer under the repository
predicates — belt and braces, and the belt was already on. The escape hatch
is graduated, not theoretical: an enterprise tier can be carved out to
DB-per-tenant later because nothing in the code assumes co-tenancy — it
assumes `workspace_id`.

### 5.2 The rest of the Stage-4 delta

- **Managed Postgres with read replicas.** Writes (ingestion, chat persistence)
  to primary; read replicas absorb search and dashboards. pgvector queries are
  reads — replicas scale exactly the hot path. Replica lag is acceptable for
  search (seconds-stale index ≈ the retrieval cache's existing contract) and
  unacceptable for read-your-writes paths (conversation history), which pin
  to primary.
- **Ingestion workers extract first.** The autoscaled worker pool keys on
  queue depth (KEDA on Redis list length, or ECS step scaling). Ingestion is
  the natural first extraction from the modular monolith: it already
  communicates only via queues and the DB, has no interactive latency
  contract, and spikes hardest (tenant onboarding = millions of chunks).
  **Agent execution extracts second** — long-lived, resource-heavy, already
  running on Temporal (M19), so moving its workers is a deployment change;
  Temporal's history keeps runs durable across pod churn. The chat/retrieval
  path extracts *last*, if ever — it is latency-sensitive and benefits most
  from staying fused. This ordering *is* the service-extraction map, and the
  module seams from ADR-0002 are its edges.
- **Object storage** (S3-compatible) behind the existing blob port for
  original documents; local disk was an adapter all along.
- **Per-tenant rate limiting and quotas** at the gateway plus token budgets in
  `atlas/ai` — cost isolation is tenant isolation
  ([43-cost-strategy.md](43-cost-strategy.md) §6).
- **What still does not change:** the domain, the use cases, the OpenAPI
  contract, the eval gates.

## 6. Load characteristics — why the queues were shaped this way

| Workload | Shape | Latency contract | Placement |
|---|---|---|---|
| Chat / retrieval | bursty-interactive: seconds of intense activity, minutes of silence | p95 first-token < 2s | api process, never queued |
| Ingestion | batch-background: huge, spiky, deadline-free | throughput, not latency | `ingest.*` queues, niceness-throttled |
| Agent background runs | long-lived, checkpointed | progress visibility, not speed | `agent.background`, Temporal from M19 |
| Evals / maintenance | scheduled, off-peak | none | `maintenance`, beat-driven |

These profiles never share a pool, so they never share a failure mode: a
tenant dumping 100 GB of PDFs cannot add a millisecond to another tenant's
chat, because the work enters a different queue consumed by different
processes with a different scaling policy. The queue topology in
[41-infrastructure-architecture.md](41-infrastructure-architecture.md) §3 is
this table, implemented.

## 7. Limits of the chosen tech — honest triggers for revisiting

| Choice | Comfortable until | Trigger to revisit | Escape hatch |
|---|---|---|---|
| pgvector HNSW | ~5–10M chunks per tenant | recall/latency SLOs fail after `ef_search`/rebuild tuning, or index rebuild windows become operationally hostile | dedicated vector DB behind the `Retriever` port — a new adapter plus a backfill, not a rewrite; embeddings re-exportable from Postgres |
| Redis broker + Celery | Stage 3 comfortably | delivery-guarantee incidents in practice, or multi-region queues | interactive-adjacent flows move to Temporal (already in-stack from M19); Celery remains for fire-and-forget |
| Single Postgres writer | Stage 4 with replicas | sustained write saturation from ingestion at fleet scale | ingestion writes batch/COPY first; then tenant sharding by `workspace_id` — the column doubles as the shard key |
| Postgres FTS | ~10M+ docs per tenant | relevance ceiling or language-analysis needs | search behind the same retrieval port; hybrid fusion already treats FTS as one replaceable candidate stream |
| Modular monolith | Stage 4 partial extraction | team scale (many squads shipping independently), not machine scale | extraction map in §5.2 |

The pgvector line deserves emphasis because it is the fashionable one to get
wrong in both directions. The trigger is **>10M chunks per tenant or SLO
failure that tuning cannot fix** — not a benchmark blog post. Below that,
pgvector's operational story (one database, one backup, transactional
consistency between chunks and vectors) beats a second stateful system's
recall margin. Above it, the `Retriever` port (spine ADR-0005 philosophy:
thin abstractions, swappable vendors) makes the swap a bounded project.

## 8. Capacity planning — worked example at 1M chunks

Assumptions: 768d float32 embeddings, HNSW `m=16`, avg 1.1 KB chunk text,
single tenant (Stage 2/3 shape).

| Component | Estimate | Working |
|---|---|---|
| Raw vectors | ~3.1 GB | 1M × 768 × 4 B ≈ 3.07 GB |
| HNSW graph overhead | ~0.7–1.0 GB | per-node links ≈ m × 2 × 8 B at layer 0 plus upper layers and page overhead ≈ 250–350 B/node |
| Vector index total | **~3.8–4.1 GB** | vectors are stored in the index pages |
| Chunk heap + TOAST | ~1.6 GB | text, metadata, tsvector |
| FTS GIN index | ~0.5 GB | corpus-dependent |
| Everything else | <0.5 GB | conversations, jobs, audit — rounding error |
| **Database total** | **~6.5–7 GB** | |

- **RAM:** target the HNSW index resident: `shared_buffers` 4–6 GB, machine
  RAM 16 GB comfortable. An index that spills to disk turns sub-10ms scans
  into IO-bound 100ms+ scans — RAM residency *is* the latency SLO.
- **Query p95 expectations (index resident):** vector top-24 at
  `ef_search=40`: 5–15 ms. Hybrid (vector ∥ FTS + RRF): 20–50 ms. With
  cross-encoder rerank of 24: +80–200 ms CPU (the dominant term — which is
  why rerank is optional and post-fusion, spine §10). Grounded chat
  first-token stays LLM-dominated, exactly as it should be.
- **Build time:** initial HNSW build at `maintenance_work_mem=1GB`,
  parallel workers 2: tens of minutes. Plan re-index maintenance windows
  accordingly at this size.
- **Backup:** ~7 GB dump, compressed less by `-Fc` on vector bytes
  (high-entropy); nightly remains fine on any modern disk; the
  embedding-exclusion option drops it to ~1.5 GB if the user opts in.

Rule of thumb worth memorizing: **~4 GB of index per million 768d chunks —
budget RAM to hold it, and p95 takes care of itself.**

---

## 9. Decisions made in this document

1. **Stage boundaries** as tabled in §1 (150–200k chunks as the Stage-1
   design center; 1M chunks as the Stage-2 planning point).
2. **Stage-2 tuning ladder:** `ef_search` first (40→80–120), rebuild at
   `ef_construction=128` second, `m=24` last; partial HNSW indexes per
   dominant workspace; `chunks` partitioning explicitly deferred with
   revisit criteria.
3. **Embed throughput planning numbers** (~5–15 chunks/s CPU, ~50–150 GPU)
   and the incremental-only re-embed rule with its two sanctioned exceptions.
4. **Stage-3 stack:** pgbouncer in transaction mode (safe with
   transaction-scoped `SET LOCAL`), one beat container, RLS rehearsed as
   defense-in-depth ahead of Stage 4.
5. **Stage-4 tenancy:** shared schema + RLS on `workspace_id` initially;
   DB-per-tenant reserved for a future enterprise tier; schema-per-tenant
   rejected as a weak middle.
6. **Service extraction order:** ingestion workers first, agent execution
   (on Temporal) second, chat/retrieval last-if-ever.
7. **Read-replica policy:** search and dashboards on replicas;
   read-your-writes paths pinned to primary.
8. **pgvector escape-hatch trigger:** >10M chunks per tenant or tuning-proof
   SLO failure, swapped behind the `Retriever` port.
9. **Capacity rule of thumb:** ~4 GB index per 1M 768d chunks; RAM residency
   treated as the latency SLO.
