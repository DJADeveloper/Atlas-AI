"""Composition root contract: pure construction, no shared singletons."""

from atlas.config.settings import Settings
from atlas.presentation.composition import build_container


def _closed_port_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://atlas:atlas@127.0.0.1:9/atlas",
        redis_url="redis://127.0.0.1:9/0",
        feature_flags={"voice": True},
    )


def test_build_container_performs_no_io() -> None:
    """Closed-port targets prove construction never connects."""
    container = build_container(_closed_port_settings())
    assert container.settings.profile == "hybrid"


def test_feature_flags_wired_from_settings() -> None:
    container = build_container(_closed_port_settings())
    assert container.feature_flags.is_enabled("voice") is True
    assert container.feature_flags.is_enabled("unknown") is False


def test_containers_are_independent_not_singletons() -> None:
    """M02 acceptance: no module-level singletons — every build is a
    fresh, independent object graph."""
    settings = _closed_port_settings()
    first = build_container(settings)
    second = build_container(settings)
    assert first is not second
    assert first.feature_flags is not second.feature_flags
    assert first.db_engine is not second.db_engine
    assert first.redis is not second.redis
