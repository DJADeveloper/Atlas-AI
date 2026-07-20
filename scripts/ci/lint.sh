#!/usr/bin/env bash
# Lint gate: formatting and static lint, zero suppressions policy (M01).
set -euo pipefail
cd "$(dirname "$0")/../.."

uv run --project apps/api ruff format --check apps/api
uv run --project apps/api ruff check apps/api
(cd apps/api && uv run lint-imports)
