#!/usr/bin/env bash
# Integration gate: requires a container runtime (testcontainers).
set -euo pipefail
cd "$(dirname "$0")/../.."

(cd apps/api && uv run pytest -m integration)
