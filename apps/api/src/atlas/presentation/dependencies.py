"""Request-scope accessors bridging FastAPI to the composition root."""

from fastapi import Request

from atlas.presentation.composition import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container
