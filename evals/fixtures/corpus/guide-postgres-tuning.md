# Guide: PostgreSQL Tuning for Vector Workloads

## Memory

Set maintenance_work_mem to at least 512MB before building HNSW indexes;
index build time drops dramatically. Keep shared_buffers at a quarter of
system memory as a starting point and measure before touching it further.

## Autovacuum

High-churn tables holding embeddings need aggressive autovacuum: scale
factors around 0.02 for vacuum and 0.01 for analyze. Dead tuples bloat
HNSW pages, and vacuum never shrinks the index — plan periodic reindexing.

## Query-time knobs

hnsw.ef_search trades recall for latency per query; raise it for offline
evaluation jobs and keep it modest on interactive paths.
