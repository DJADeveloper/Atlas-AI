#!/usr/bin/env bash
# Fast test gate: Python unit suite (no containers) + JS workspace tests.
set -euo pipefail
cd "$(dirname "$0")/../.."

(cd apps/api && uv run pytest -m "not integration")
pnpm -r test
