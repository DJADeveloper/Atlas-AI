"""App factory contract: pure construction, expected surface."""

from fastapi import FastAPI

from atlas.config.settings import Settings
from atlas.presentation.app import create_app


def test_create_app_constructs_without_side_effects() -> None:
    """Construction must not touch the network.

    The settings point at a guaranteed-closed port; if the factory (or any
    import it triggers) attempted a connection, construction would raise or
    hang. Plain synchronous construction succeeding is the contract.
    """
    settings = Settings(
        database_url="postgresql+asyncpg://atlas:atlas@127.0.0.1:9/atlas",
        redis_url="redis://127.0.0.1:9/0",
    )
    app = create_app(settings)
    assert isinstance(app, FastAPI)
    assert app.state.settings is settings


def test_app_exposes_system_probes() -> None:
    app = create_app(Settings())
    assert {"/health", "/ready"} <= set(app.openapi()["paths"])


def test_app_title_and_version() -> None:
    app = create_app(Settings())
    assert app.title == "Atlas API"
    assert app.version
