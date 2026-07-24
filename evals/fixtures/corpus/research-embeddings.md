# Research Note: Embedding Model Comparison

## Setup

We compared three embedding models on our internal retrieval benchmark of
6,000 labeled query-document pairs: a small local model, a mid-size open
model, and a hosted commercial API, measuring recall at ten and latency.

## Results

The mid-size open model recovered 92% of the commercial model's recall at
one eighth of the cost, and local inference kept p95 embedding latency
under 40 milliseconds on a laptop-class GPU. The small model lost badly on
paraphrase queries.

## Recommendation

Default to the mid-size local model; reserve the commercial API for
workspaces that explicitly opt into hosted processing.
