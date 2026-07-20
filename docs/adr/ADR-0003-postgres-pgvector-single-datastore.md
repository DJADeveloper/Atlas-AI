# ADR-0003: PostgreSQL + pgvector as the single datastore

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas needs relational storage (documents, conversations, grants, audit),
vector similarity search (chunk embeddings), and keyword/full-text search — on
a machine the user owns, with backup/restore a user can understand. The
fashionable default is a separate vector database next to a relational one.

## Decision

PostgreSQL 16 is the only datastore. `pgvector` (HNSW, cosine) serves vector
search; native FTS (`tsvector` + GIN, `ts_rank_cd`) serves keyword search;
hybrid results are fused with RRF in SQL/application code. Redis is cache and
queue broker only — never a system of record.

## Alternatives considered

- **Dedicated vector DB (Qdrant, Weaviate, Chroma, LanceDB).** Better raw ANN
  performance and vector-native APIs at large scale. Rejected for v1 because it
  splits every write into a distributed transaction problem (chunk row + vector
  entry must stay consistent through re-index, rename, delete), doubles the
  backup/restore story, and adds a second stateful service to a desktop
  footprint budget. pgvector's HNSW comfortably serves the 10⁵–10⁶ chunk range
  Atlas targets through Stage 2 with single-digit-millisecond ANN queries.
- **Elasticsearch/OpenSearch for hybrid search.** Best-in-class lexical + good
  ANN, but a JVM heavyweight wildly oversized for a laptop sidecar.
- **SQLite (+ sqlite-vec) for zero-dependency local storage.** Smallest
  footprint and simplest desktop bundling; genuinely attractive. Rejected as
  the primary store because Atlas leans on Postgres features end to end
  (FTS quality, JSONB, partial indexes, row-level security for the multi-user
  future, LISTEN/NOTIFY) and because running the same engine from laptop to
  cloud eliminates an entire class of "works locally" bugs. The desktop bundle
  ships a managed Postgres (`40-deployment-architecture.md`).

## Consequences

- One transaction boundary covers relational + vector + FTS state: re-indexing
  a document is atomic, and consistency bugs between "the row" and "the vector"
  cannot exist.
- One backup (`pg_dump`), one restore path, one operational surface.
- We own hybrid-search quality (RRF, boosts) in SQL we control — good for
  learning and tuning, more code than a vector-DB SDK would be.
- Embedding dimension is fixed per deployment profile (`vector(768)` default);
  changing embedding models is an explicit re-embed migration (spine §7).
- HNSW index build/maintenance parameters become our responsibility at scale
  (`41-infrastructure-architecture.md` documents tuning).

## Revisit triggers

Corpus beyond ~10M chunks per workspace; recall/latency SLOs failing after
HNSW tuning; multi-tenant cloud requiring vector workload isolation. The
`Retriever`/`ChunkRepository` ports make a swap an adapter project, not a
rewrite.
