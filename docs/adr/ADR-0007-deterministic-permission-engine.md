# ADR-0007: Deterministic permission engine outside the model

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas will eventually move files, run commands, and send messages on a user's
machine, with an LLM deciding *what* to do. LLM output is influenced by every
document ingested — including hostile ones (prompt injection, OWASP LLM01) —
so model output must be treated as untrusted input to the action layer. The
central safety question of the whole product: what stands between a model's
intent and a syscall?

## Decision

A three-part separation, enforced in code structure, not convention:
**intent** (the model emits a schema-validated tool call) → **permission** (a
deterministic policy engine evaluates it against explicit `permission_grants`,
capability scopes, and risk tiers T0–T3, with T2/T3 requiring human approval)
→ **action** (a sandboxed executor performs it, writing `tool_invocations` and
append-only `audit_events`). The policy engine is pure, framework-free domain
code with property-based tests. Nothing the model outputs can create, widen, or
bypass a grant.

## Alternatives considered

- **Prompt-level guardrails ("you must always ask before deleting").**
  Necessary UX politeness, worthless as a security boundary: instructions are
  exactly what injection overwrites. Rejected as the enforcement mechanism;
  retained as behavior shaping only.
- **LLM-as-judge for permission decisions.** Fashionable, and useful for
  *advisory* risk flagging, but a probabilistic gate in front of destructive
  actions means the security property degrades with model behavior. The gate
  must be boring, testable code. Rejected for enforcement; may later *add*
  advisory friction (e.g. flagging unusual batches for review).
- **OS-level sandboxing alone (containers, seccomp, macOS sandbox).** Strong
  complement, wrong granularity: the OS can stop Atlas writing outside a
  directory, but cannot express "moves need approval, reads don't" or produce
  a user-legible audit trail. We layer OS mechanisms *under* the policy engine
  (`25-computer-automation.md`), not instead of it.

## Consequences

- The permission engine becomes some of the most valuable code in the repo:
  small, pure, exhaustively property-tested (no grant ⇒ never allow; T3 never
  auto-approves; canonicalized paths cannot escape scopes).
- Every capability must be modeled explicitly before any tool ships — friction
  by design; the capability catalog lives in `25-computer-automation.md`.
- Approval UX becomes a core product surface (inbox, previews, undo), funded
  as milestones M15/M17, not an afterthought.
- Auditability: every allow/deny/approval writes an audit row; "why did Atlas
  do X?" is always answerable from the database alone.

## Revisit triggers

None for the principle — it is load-bearing for the product's existence.
Mechanism details (tier definitions, scope grammar) evolve via normal ADRs.
