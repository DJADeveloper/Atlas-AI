# ADR-0009: UUIDv7 primary keys

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Every table needs a primary-key strategy. Requirements: generatable
application-side (offline/local-first, no DB round-trip, usable in logs and
traces before commit), non-guessable enough for URLs, stable across
export/import and future sync, and index-friendly under write load.

## Decision

All primary keys are **UUIDv7** (RFC 9562), generated application-side in
`atlas/shared/ids.py`, stored as native Postgres `uuid`.

## Alternatives considered

- **BIGSERIAL/identity integers.** Smallest, fastest joins. Rejected:
  DB-round-trip generation couples domain code to the database, ids leak
  cardinality, and any future sync/multi-device story (very plausible for
  local-first) becomes a coordination problem.
- **UUIDv4.** The default choice, fully random. Rejected in favor of v7 for one
  concrete reason: random keys shatter B-tree locality, and Atlas's hottest
  tables (`chunks`, `messages`, `agent_steps`, `audit_events`) are
  insert-heavy; v7's time-ordered prefix keeps inserts appending to the same
  index pages and makes id order roughly chronological — pleasant for
  debugging and pagination fallbacks too.
- **ULID / KSUID / Snowflake.** Same time-ordered idea, non-standard encodings
  or extra infrastructure. UUIDv7 gets the property inside the standard `uuid`
  type with ecosystem support everywhere.

## Consequences

- One `new_id()` helper; domain objects are fully constructible (and unit
  tests fully deterministic — the helper is a `Clock`/`IdGenerator` port) with
  no database present.
- v7 ids embed creation time at millisecond precision — a minor metadata leak
  (acceptable: rows carry `created_at` anyway; ids are not secrets and
  authorization never depends on unguessability).
- Cursor pagination uses explicit `(created_at, id)` cursors rather than
  relying on id ordering — v7 ordering is a bonus, not a contract.
- 16-byte keys everywhere: measurable index-size cost vs int8, accepted for
  the generation and sync properties.

## Revisit triggers

None foreseeable; the decision is cheap to live with and would be expensive to
churn.
