# ADR-0005: Thin in-house provider abstraction; no framework lock-in

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas calls LLMs (Anthropic, OpenAI, Ollama) and embedding models, with
routing, fallback, streaming, tool-use, and cost accounting. The ecosystem
offers batteries-included frameworks (LangChain/LangGraph, LlamaIndex) that
bundle providers, RAG plumbing, agent loops, and integrations. We must decide
whether Atlas's AI layer is built on such a framework or on a thin abstraction
we own.

## Decision

Atlas builds a thin, typed, in-house provider layer: `LLMProvider` and
`EmbeddingProvider` ports in the domain, one adapter per vendor SDK in
`infrastructure/providers/`, and routing/fallback/accounting in `atlas/ai`.
RAG, agents, memory, and tools are first-party Atlas modules. No orchestration
framework in the dependency tree.

## Alternatives considered

- **LangChain/LangGraph.** Steelman: enormous integration surface, LangGraph's
  graph runtime overlaps our agent needs, hiring familiarity. Rejected because
  Atlas's differentiators — the permission boundary, checkpointed agent loop,
  citation pipeline, eval integration — are exactly the parts a framework wants
  to own, and bending a framework's abstractions around a security boundary
  produces the worst of both. Framework API churn would also become permanent
  maintenance load bearing none of our value.
- **LlamaIndex.** Best-in-class ingestion/retrieval abstractions; genuinely
  close to our RAG needs. Rejected for the same ownership reason at the
  retrieval layer (structure-aware chunking and hybrid-RRF in our own SQL are
  core competence to demonstrate, not glue to import), though its designs are
  reference reading.
- **Direct SDK calls sprinkled through the codebase (no abstraction).**
  Simplest possible start; rejected because routing, fallback, cost metering,
  and the local-only profile all require a single choke point, and retrofitting
  one under twenty call sites is misery. The abstraction earns its keep on
  literally the first fallback event.

## Consequences

- Full control and legibility of every token that leaves the machine — a
  privacy product requirement, not a preference.
- We implement (and get to demonstrate) streaming, tool-use adaptation, retry
  and circuit-breaker logic ourselves — more code, all of it core competence.
- Vendor SDKs are still used *inside* adapters (anthropic, openai, ollama
  clients) — "thin abstraction" means one seam, not NIH re-implementation of
  HTTP.
- New provider = new adapter + routing entry; nothing else moves.

## Revisit triggers

A framework stabilizing an abstraction we find ourselves re-deriving in detail
(e.g. durable graph execution — though ADR-0004 answers that with Temporal), or
a team scaling moment where framework familiarity outweighs ownership benefits
for non-core paths.
