# Retrieval golden dataset v1

`golden_v1.jsonl` — 53 queries (35 paraphrase, 18 keyword) over the committed
fixture corpus in [`evals/fixtures/corpus/`](../fixtures/corpus/): 24 authored
documents styled as a fictional company's handbook, engineering notes, product
specs, meeting minutes, research notes, and guides. Every query labels its
relevant document by path plus a `must_contain` span (whitespace-normalized
substring match at chunk level), so labels survive re-chunking.

**Why paraphrase-heavy:** the M06 risk note — queries authored by the same
person who wrote the chunker bias toward its vocabulary. Paraphrase variants
avoid the corpus's exact words ("will the company pay me back" → the
expenses policy); keyword variants stress FTS (`maintenance_work_mem`,
`Zephyr window`). The set grows from real usage feedback after M09.

## Running

```bash
cd apps/api
uv run python scripts/retrieval_eval.py --provider ollama   # real embeddings
uv run python scripts/retrieval_eval.py                     # fake (mechanics only)
```

The harness ingests the corpus through the real pipeline, runs every query
through `HybridSearch` (spine §10: 24+24 → RRF k=60 → top 8), and reports
Recall@8 and MRR. Numbers are only meaningful with `--provider ollama`.

## Pinned baseline (M11 regression gate reads this)

| Date | Provider | Recall@8 | MRR | Source |
|---|---|---|---|---|
| pending first nightly run | nomic-embed-text (768d) | target ≥ 0.75 | target ≥ 0.60 | `retrieval-eval` CI artifact |

Baseline moves only through a reviewed pull request (eng-testing standard).
