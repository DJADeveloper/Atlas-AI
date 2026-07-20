"""Settings: source layering and profile resolution."""

from pathlib import Path

import pytest

from atlas.config.profiles import HYBRID, LOCAL_ONLY, Profile
from atlas.config.settings import Settings


class TestSourceLayering:
    def test_defaults_apply_with_no_sources(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)  # no .env here
        monkeypatch.delenv("ATLAS_PROFILE", raising=False)
        assert Settings().profile == "hybrid"

    def test_env_file_overrides_defaults(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / ".env").write_text("ATLAS_PROFILE=local-only\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ATLAS_PROFILE", raising=False)
        assert Settings().profile == "local-only"

    def test_env_var_overrides_env_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / ".env").write_text("ATLAS_PROFILE=local-only\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ATLAS_PROFILE", "hybrid")
        assert Settings().profile == "hybrid"

    def test_explicit_argument_overrides_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATLAS_PROFILE", "hybrid")
        assert Settings(profile="local-only").profile == "local-only"

    def test_invalid_profile_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATLAS_PROFILE", "cloud-everything")
        with pytest.raises(ValueError, match="profile"):
            Settings()


class TestProfileResolution:
    """M02 acceptance: switching ATLAS_PROFILE changes the resolved provider
    configuration without code changes."""

    def test_profile_switch_changes_provider_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATLAS_PROFILE", "local-only")
        resolved = Settings().provider_profile
        assert resolved is LOCAL_ONLY
        assert resolved.chat.provider == "ollama"
        assert resolved.chat.model == "llama3.1:8b"
        assert resolved.escalation is None

    def test_hybrid_routing_matches_spine(self) -> None:
        resolved = Settings(profile="hybrid").provider_profile
        assert resolved is HYBRID
        assert resolved.chat.model == "claude-sonnet-5"
        assert resolved.escalation is not None
        assert resolved.escalation.model == "claude-opus-4-8"
        assert resolved.classification.model == "claude-haiku-4-5-20251001"

    def test_embeddings_stay_local_in_both_profiles(self) -> None:
        profiles: tuple[Profile, ...] = ("hybrid", "local-only")
        for profile in profiles:
            resolved = Settings(profile=profile).provider_profile
            assert resolved.embedding.provider == "ollama"
            assert resolved.embedding.model == "nomic-embed-text"
