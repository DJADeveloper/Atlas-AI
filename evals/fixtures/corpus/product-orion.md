# Product Spec: Orion Search Rework

## Problem

Customers describe search on the dashboard as slow and literal-minded:
synonyms miss, typos fail, and filters reset between sessions. Support tags
roughly 90 tickets a month with search-frustration labels.

## Proposal

Orion replaces the legacy keyword engine with hybrid retrieval: semantic
embeddings fused with keyword ranking, filter persistence per user, and a
100 millisecond p95 latency budget at the API layer.

## Success metrics

Search-to-click rate above 55%, zero-result queries below 8%, and a
measured drop in search-frustration tickets by half within one quarter of
launch.
