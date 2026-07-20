# atlas-api

The Atlas backend: FastAPI application and background workers, structured as a
modular monolith in Clean Architecture layers (see
[`docs/00-architecture-decisions.md`](../../docs/00-architecture-decisions.md)
§5 for the normative package layout).

## Quickstart

```bash
uv sync                                # install Python 3.12 env + deps
uv run uvicorn --factory atlas.presentation.app:create_app --reload
curl localhost:8000/health
```

With services (from the repo root):

```bash
docker compose -f infra/compose/docker-compose.yml --profile core up -d
curl localhost:8000/ready
```

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -m "not integration"     # fast suite, no containers needed
uv run pytest -m integration           # requires a container runtime
```

`# type: ignore` usage policy: see [`mypy-allowlist.md`](mypy-allowlist.md).
