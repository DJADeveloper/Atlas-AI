"""Memory v1 entities: kinds, expiry, provenance (M07)."""

from datetime import UTC, datetime

import pytest

from atlas.domain.memory import MEMORY_KINDS, Memory
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7


class TestMemory:
    def test_kind_vocabulary_matches_the_schema(self) -> None:
        assert MEMORY_KINDS == ("preference", "project_fact", "decision", "entity", "episodic")

    def test_preference_with_provenance(self) -> None:
        source = uuid7()
        memory = Memory(
            workspace_id=uuid7(),
            kind="preference",
            content="Prefers answers in bullet points.",
            source_message_id=source,
            confidence=0.9,
        )
        assert memory.source_message_id == source
        assert not memory.is_expired()

    def test_project_fact_unusable_until_projects_exist(self) -> None:
        with pytest.raises(ValidationFailed):
            Memory(workspace_id=uuid7(), kind="project_fact", content="Belongs to a project.")

    def test_blank_content_and_bad_confidence_rejected(self) -> None:
        with pytest.raises(ValidationFailed):
            Memory(workspace_id=uuid7(), kind="decision", content="   ")
        with pytest.raises(ValidationFailed):
            Memory(workspace_id=uuid7(), kind="decision", content="x", confidence=1.5)

    def test_expiry_is_time_based(self) -> None:
        memory = Memory(
            workspace_id=uuid7(),
            kind="episodic",
            content="Was debugging the deploy pipeline this week.",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert memory.is_expired(now=datetime(2026, 6, 1, tzinfo=UTC))
        assert not memory.is_expired(now=datetime(2025, 6, 1, tzinfo=UTC))

    def test_revise_and_soft_delete(self) -> None:
        memory = Memory(workspace_id=uuid7(), kind="decision", content="Ship v1 without SSO.")
        memory.revise("Ship v1 with SSO after all.")
        assert "SSO after all" in memory.content
        memory.soft_delete()
        with pytest.raises(Conflict):
            memory.soft_delete()
