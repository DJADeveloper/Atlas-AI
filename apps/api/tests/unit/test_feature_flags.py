"""Feature-flag stub behavior."""

import pytest

from atlas.config.feature_flags import FeatureFlags
from atlas.config.settings import Settings


def test_unknown_flags_are_off() -> None:
    assert FeatureFlags().is_enabled("anything") is False


def test_seeded_flags_resolve() -> None:
    flags = FeatureFlags({"voice": True, "graph": False})
    assert flags.is_enabled("voice") is True
    assert flags.is_enabled("graph") is False


def test_snapshot_is_a_defensive_copy() -> None:
    flags = FeatureFlags({"voice": True})
    snapshot = flags.snapshot()
    snapshot["voice"] = False
    assert flags.is_enabled("voice") is True


def test_seeds_flow_from_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_FEATURE_FLAGS", '{"voice": true}')
    flags = FeatureFlags(Settings().feature_flags)
    assert flags.is_enabled("voice") is True
