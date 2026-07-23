"""Request-scope accessors bridging FastAPI to the composition root."""

from uuid import UUID, uuid4

from fastapi import Request

from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.observability.logging import current_trace_id
from atlas.presentation.composition import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


async def get_workspace_id(request: Request) -> UUID:
    """The single local workspace (spine: single-user now, multi-user
    ready). Resolved once per process, cached on app state; real auth
    replaces this dependency at M14/M25 without touching routes."""
    cached: UUID | None = getattr(request.app.state, "workspace_id", None)
    if cached is not None:
        return cached
    container: Container = request.app.state.container
    workspace_id = await ensure_default_workspace(container.session_factory)
    request.app.state.workspace_id = workspace_id
    return workspace_id


def current_batch_id(request: Request) -> str:
    """Ingestion batch id = the request's trace id (docs/12: the batch id
    is filterable via GET /jobs?trace_id=...)."""
    trace_id = current_trace_id()
    return trace_id if trace_id is not None else str(uuid4())
