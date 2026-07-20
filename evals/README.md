# Atlas — Golden Datasets & Evaluation Suites

This directory holds the version-controlled evaluation assets described in
[`docs/32-evaluation-architecture.md`](../docs/32-evaluation-architecture.md):

- `datasets/` — golden datasets (JSONL): retrieval, grounding, tool-selection,
  and injection cases. Seeded at **M06** (retrieval) and grown continuously via
  the feedback flywheel. Only synthetic or sanitized cases live in the repo;
  personal cases stay in the local database.
- `fixtures/` — the fixture corpus (~50 files across all supported formats,
  fictional persona) used by integration tests, E2E journeys, and eval runs.
  Lands at **M06** with the retrieval baseline.
- `suites/` — eval suite configs (which datasets, metrics, thresholds, and
  budgets a run uses). The PR fast-gate and nightly full suite are defined
  here from **M11**.

Nothing in this directory is populated at M01 by design: eval assets are
introduced by the milestone that makes them measurable, never speculatively
(see `docs/60-milestones.md`, M06 and M11).
