#!/usr/bin/env bash
# Type gate: mypy --strict (Python) and tsc strict (workspace packages).
set -euo pipefail
cd "$(dirname "$0")/../.."

(cd apps/api && uv run mypy)
pnpm -r typecheck
