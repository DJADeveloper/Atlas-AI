"""Conversation-context entities: invariants and lifecycle (M07)."""

from typing import Any

import pytest

from atlas.domain.conversation import Conversation, Message
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7


class TestConversation:
    def test_rename_touches_recency(self) -> None:
        conversation = Conversation(workspace_id=uuid7())
        before = conversation.updated_at
        conversation.rename("Quarterly planning")
        assert conversation.title == "Quarterly planning"
        assert conversation.updated_at >= before

    def test_blank_or_oversized_titles_rejected(self) -> None:
        conversation = Conversation(workspace_id=uuid7())
        with pytest.raises(ValidationFailed):
            conversation.rename("   ")
        with pytest.raises(ValidationFailed):
            conversation.rename("x" * 201)
        with pytest.raises(ValidationFailed):
            Conversation(workspace_id=uuid7(), title="x" * 201)

    def test_soft_delete_is_guarded(self) -> None:
        conversation = Conversation(workspace_id=uuid7())
        conversation.soft_delete()
        assert conversation.is_deleted
        with pytest.raises(Conflict):
            conversation.soft_delete()


class TestMessage:
    def test_user_message_requires_content(self) -> None:
        with pytest.raises(ValidationFailed):
            Message(conversation_id=uuid7(), role="user", content="")

    def test_assistant_message_may_start_empty(self) -> None:
        """Streaming persists the assistant row before tokens arrive."""
        message = Message(conversation_id=uuid7(), role="assistant", content="")
        assert message.content == ""

    def test_usage_fields_round_trip(self) -> None:
        message = Message(
            conversation_id=uuid7(),
            role="assistant",
            content="An answer.",
            model="claude-sonnet-5",
            provider="anthropic",
            prompt_version="chat.v1",
            input_tokens=3812,
            output_tokens=402,
            cost_usd=0.0174,
            latency_ms=2916,
            trace_id="t-1",
        )
        assert message.cost_usd == pytest.approx(0.0174)
        assert message.provider == "anthropic"

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [("input_tokens", -1), ("output_tokens", -5), ("latency_ms", -1), ("cost_usd", -0.01)],
    )
    def test_negative_accounting_rejected(self, field_name: str, value: float) -> None:
        kwargs: dict[str, Any] = {
            "conversation_id": uuid7(),
            "role": "assistant",
            "content": "x",
            field_name: value,
        }
        with pytest.raises(ValidationFailed):
            Message(**kwargs)
