# ADR-0011: Identifier-based aggregate references (no ORM relationships)

- **Status:** Accepted
- **Date:** 2026-07-20 (M03; documented after CI run 4 exposed its first consequence)

## Context

M03 introduced the persistence layer: SQLAlchemy Row classes, mappers, and
repositories returning pure domain entities. SQLAlchemy offers two ways to
model cross-table links: `relationship()` object graphs (lazy/eager-loaded
navigation, unit-of-work topological insert ordering) or plain foreign-key
columns with identifier references. The domain model already commits to
id-references (`docs/10-domain-model.md`: entities and events carry ids, not
object pointers), and the M03 scoping contract requires every read to be
workspace-bounded in SQL. CI run 4 made the choice's main cost concrete:
without relationships, SQLAlchemy cannot order inserts across tables, and a
document version plus its chunks added in one Unit of Work reached Postgres
in FK-violating order.

## Decision

Row classes define foreign-key **columns only** — no `relationship()`, no
backrefs, no cascade configuration beyond FK `ON DELETE`. Domain entities
reference other aggregates by `UUID`. Repositories load dependent records
through explicit, workspace-scoped, set-based queries, and every
`add`/`add_all` **flushes immediately** so statement order equals call order.

## Why, and the benefits

1. **Aggregate boundaries stay real.** A relationship graph invites
   `chunk.version.document.source` traversal that bypasses ports, scoping,
   and soft-delete filters. With id references, the only path to another
   aggregate is its repository — where the workspace_id contract lives.
2. **No lazy-loading I/O in domain code.** Entities are frozen/plain
   dataclasses; nothing they expose can trigger a query. Under asyncio this
   also removes an entire failure class: implicit lazy loads raise
   `MissingGreenlet` at runtime — the safe async pattern is "load explicitly,
   never navigate", which this design enforces structurally.
3. **Mechanical, auditable mapping.** `*_to_row` / `apply_*` / `*_from_row`
   copy scalars; there is no identity-map aliasing between an entity graph
   and an ORM graph, and no partially-loaded object states.
4. **Visible query behavior.** Every statement a repository issues is written
   in the repository. Performance is inspected by reading code, not by
   reconstructing loader-strategy interactions.

## Costs (accepted with eyes open)

- **Insert ordering is ours to guarantee.** SQLAlchemy's unit-of-work sorts
  inserts by mapper *relationships*; with none declared, cross-table order is
  undefined. Mitigation below. (Found by CI run 4's FK violations —
  `tests/integration/test_repositories.py` now regression-tests the exact
  scenario.)
- **Joins by hand.** Scoped reads compose 1–3 joins explicitly
  (`_scoped_documents`, `_scoped_versions`, chunk scoping). More SQL text,
  zero magic.
- **One extra round trip per add-batch** (the flush). Writes are batch-shaped
  (`add_all` flushes once per chunk set), so this is noise at Atlas scale.

## How flush ordering is handled

`add`/`add_all` call `session.flush()` immediately: statement order is pinned
to call order, and newly added rows are visible to later statements in the
same transaction. `commit`/`rollback` remain exclusively the Unit of Work's
decision — flush moves data to the transaction, never past it. Documented in
`repositories.py`; proven by
`TestDocumentAggregate::test_chunk_round_trip_with_embedding` and
`TestWorkspaceIsolation::test_cross_workspace_reads_return_nothing`, which
add document + version + chunks in one UoW.

## How N+1 queries are avoided

The classic ORM N+1 — iterating a parent's lazily loaded children — is
structurally impossible: there is nothing lazy to iterate. Repositories
expose only **set-based** dependent loads (`list_for_source`,
`list_for_document`, `list_for_version`, `count_for_version`), each one
statement. The standing rule for use cases: parent-plus-children is two
statements (or one join), never N. Composite read models (M06 retrieval,
M09 UI listings) will be purpose-built single-statement queries behind their
own ports — read models, not entity-graph walks.

## How dependent records are loaded

Explicitly, by the owning aggregate's repository, always workspace-scoped:
a document's versions via `document_versions.list_for_document(workspace_id,
document_id)`; a version's chunks via `chunks.list_for_version(workspace_id,
version_id)`. Cross-aggregate composition happens in the application layer
by id, inside one Unit of Work when it must be transactionally consistent.

## Evidence that would justify revisiting

- Profiling shows use cases repeatedly issuing the same 2–3-statement load
  patterns and that chattiness measurably dominating request latency.
- A future feature genuinely requiring deep graph hydration where
  hand-composed read models become the maintenance burden.
- Field-level change tracking needs that make `apply_*` mappers error-prone.

Even then, the first remedy is a dedicated read-model query, not
`relationship()`. Re-adopting relationships would require `raiseload`
everywhere (to keep async safety), re-validating the scoping guarantees, and
a superseding ADR.
