# ADR-0001: Monorepo

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas spans a Python backend, a Next.js UI, a Tauri desktop shell, shared TS
packages, eval datasets, infrastructure code, and documentation. The API
contract between backend and frontend will change constantly during Phases A–C.
We must choose between one repository or several (api, web, desktop, infra).

## Decision

One monorepo (`Atlas-AI`) containing `apps/`, `packages/`, `evals/`, `infra/`,
and `docs/`, with `pnpm` workspaces for JS and `uv` for Python.

## Alternatives considered

- **Polyrepo (repo per app).** Standard for large orgs with independent team
  ownership and release cadences. Loses here: every API change becomes a
  multi-repo dance (backend PR, client publish, frontend PR), contract drift
  becomes possible between merges, and CI/tooling config multiplies. With one
  developer, the coordination benefits polyrepo buys are worth nothing.
- **Monorepo with heavyweight build tooling (Bazel/Nx/Pants).** Correct at the
  scale where build graphs and remote caching pay for their complexity. Atlas's
  build graph is three apps and two packages; GitHub Actions path filters and
  workspace-level caching suffice. Adopting Bazel now is résumé-driven
  engineering.

## Consequences

- Atomic cross-cutting changes: OpenAPI schema, generated client, and UI usage
  update in a single reviewable PR — the contract-drift class of bug is
  structurally eliminated (enforced by the client-regen CI check).
- One CI configuration, one version of truth for docs and ADRs.
- CI must use path filtering to avoid running everything on every PR; the
  pipeline design in `53-cicd-strategy.md` accounts for this.
- Repo size grows with eval fixtures; large binary fixtures use Git LFS if they
  exceed ~5 MB each.

## Revisit triggers

A second independent team owning a component end-to-end; open-sourcing a
component under a separate license; CI wall-clock exceeding 15 minutes despite
path filtering and caching.
