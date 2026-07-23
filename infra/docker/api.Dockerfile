# syntax=docker/dockerfile:1
# Atlas API image. The worker service (M04) runs this same image with a
# different command (docs/40-deployment-architecture.md §2.1).
# Build context is the repository root.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Dependency layer: cached until the lockfile changes.
FROM base AS deps
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Runtime layer: project code on top of the dependency venv.
FROM base AS runtime
RUN groupadd --system atlas && useradd --system --gid atlas --create-home atlas
COPY --from=deps /app/.venv /app/.venv
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version apps/api/README.md apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
COPY apps/api/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
USER atlas
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "--factory", "atlas.presentation.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
