# Atlas — Architecture Documentation

Atlas is a local-first **AI Operating System**: a secure, observable,
permission-aware layer between a person and their digital world. This
directory is the founding architecture package — written before application
code, deliberately, so that every line of code that follows has a boundary,
a rationale, and an acceptance test waiting for it.

**Start here:** [`00-architecture-decisions.md`](00-architecture-decisions.md)
— the canonical decision spine every other document conforms to. When any
document disagrees with the spine, the spine wins.

## Reading order

| # | Document | Covers |
|---|---|---|
| 00 | [Architecture decisions (spine)](00-architecture-decisions.md) | Canonical cross-cutting decisions |
| 01 | [Product vision](01-product-vision.md) | What Atlas is, for whom, and why now |
| 02 | [Engineering vision](02-engineering-vision.md) | Quality attributes, practices, definition of done |
| 03 | [Architecture overview](03-architecture-overview.md) | High-level architecture · Clean Architecture design · folder structure |
| 10 | [Domain model](10-domain-model.md) | Bounded contexts, aggregates, ports, events |
| 11 | [Database schema](11-database-schema.md) | Full schema · ERD · migration strategy |
| 12 | [API specification](12-api-specification.md) | REST surface, SSE protocol, error model |
| 13 | [Sequence flows](13-sequence-flows.md) | End-to-end flows incl. failure paths |
| 20 | [AI architecture](20-ai-architecture.md) | Provider abstraction, routing, prompt registry |
| 21 | [RAG architecture](21-rag-architecture.md) | Ingestion → chunking → hybrid retrieval → citations |
| 22 | [Memory architecture](22-memory-architecture.md) | The seven memory systems and their boundaries |
| 23 | [Agent architecture](23-agent-architecture.md) | Runtime loop, checkpointing, budgets, approvals |
| 24 | [Tool architecture](24-tool-architecture.md) | Registry, schemas, invocation pipeline |
| 25 | [Computer automation](25-computer-automation.md) | Capability catalog, sandboxing, undo |
| 30 | [Security architecture](30-security-architecture.md) | Threat model, injection defense, secrets, audit |
| 31 | [Observability architecture](31-observability-architecture.md) | Traces, metrics, cost metering, dashboards |
| 32 | [Evaluation architecture](32-evaluation-architecture.md) | Golden datasets, metrics, CI gates, judges |
| 40 | [Deployment architecture](40-deployment-architecture.md) | Dev, desktop, self-host, cloud targets |
| 41 | [Infrastructure architecture](41-infrastructure-architecture.md) | Queues, caching, reliability, backups |
| 42 | [Scaling strategy](42-scaling-strategy.md) | Four scaling stages with triggers |
| 43 | [Cost strategy](43-cost-strategy.md) | Cost model, controls, budgets, unit economics |
| 50 | [Feature roadmap](50-feature-roadmap.md) | Stages S1–S6, demo moments, non-goals |
| 51 | [Risk analysis](51-risk-analysis.md) | Risk register + top-five deep dives |
| 52 | [Testing strategy](52-testing-strategy.md) | Test pyramid for a clean-architecture AI system |
| 53 | [CI/CD strategy](53-cicd-strategy.md) | Pipelines, gates, release engineering |
| 60 | [Milestones](60-milestones.md) | The 25-milestone execution plan, M01–M25 |
| — | [ADRs](adr/README.md) | ADR-0001 … ADR-0010 |

## Deliverable map

The original mandate lists 28 deliverables; this maps each to its home.

| Deliverable | Document |
|---|---|
| 1 Product vision | 01 |
| 2 Engineering vision | 02 |
| 3 High-level architecture | 03 |
| 4 Clean Architecture design | 03 |
| 5 Folder structure | 03 (normative tree in 00 §4–5) |
| 6 Database schema | 11 |
| 7 Domain model | 10 |
| 8 AI architecture | 20 |
| 9 RAG architecture | 21 |
| 10 Agent architecture | 23 |
| 11 Tool architecture | 24 |
| 12 Memory architecture | 22 |
| 13 Computer automation architecture | 25 |
| 14 Deployment architecture | 40 |
| 15 Infrastructure architecture | 41 |
| 16 Security architecture | 30 |
| 17 Evaluation architecture | 32 |
| 18 Observability architecture | 31 |
| 19 Sequence flows | 13 |
| 20 API specification | 12 |
| 21 Database ERD | 11 |
| 22 Feature roadmap | 50 |
| 23 Risk analysis | 51 |
| 24 Scaling strategy | 42 |
| 25 Testing strategy | 52 |
| 26 CI/CD strategy | 53 |
| 27 Cost strategy | 43 |
| 28 Milestone roadmap | 60 |

## Conventions

- Diagrams are Mermaid and render directly on GitHub.
- Numbering: `0x` foundations · `1x` domain/data/API · `2x` AI subsystems ·
  `3x` trust (security/observability/evaluation) · `4x` infrastructure ·
  `5x` strategy · `60` milestones.
- Decisions that bind the future live in [`adr/`](adr/README.md); documents
  cite ADRs rather than re-arguing them.
- These documents teach as well as specify: significant choices state their
  alternatives and trade-offs inline.
