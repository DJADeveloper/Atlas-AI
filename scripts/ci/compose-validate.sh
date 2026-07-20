#!/usr/bin/env bash
# Compose drift gate: the dev stack definition must always parse (M01 risk).
set -euo pipefail
cd "$(dirname "$0")/../.."

docker compose -f infra/compose/docker-compose.yml --profile core config --quiet
docker compose -f infra/compose/docker-compose.yml --profile full config --quiet
echo "compose config valid"
