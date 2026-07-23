#!/usr/bin/env bash
# Nightly E2E gate: long-running corpus suites (requires a container runtime).
set -euo pipefail
cd "$(dirname "$0")/../.."

(cd apps/api && uv run pytest -m slow -q)
