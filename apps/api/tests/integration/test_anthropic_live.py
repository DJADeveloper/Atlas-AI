"""Nightly-only: the live Anthropic Messages API behind the adapter.

The PR path never dials a cloud provider — this contract check runs
where `ANTHROPIC_API_KEY` is set (the nightly workflow injects the
repo secret; until that secret exists the suite skips, visibly).
Haiku is the cheapest rung and the wire contract is identical.
"""

import os

import pytest

from atlas.domain.ai import ChatRequest, ProviderChatMessage
from atlas.infrastructure.providers.anthropic import AnthropicChatProvider

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="needs ANTHROPIC_API_KEY (nightly; skips until the repo secret exists)",
    ),
]

_MODEL = "claude-haiku-4-5-20251001"


async def test_live_complete_and_stream_contract() -> None:
    provider = AnthropicChatProvider(os.environ["ANTHROPIC_API_KEY"])
    request = ChatRequest(
        messages=[ProviderChatMessage(role="user", content="Reply with exactly: atlas")],
        max_tokens=16,
    )
    try:
        response = await provider.complete(_MODEL, request)
        assert response.provider == "anthropic"
        assert response.message.content.strip()
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0
        assert response.stop_reason in {"end_turn", "max_tokens"}

        events = [event async for event in provider.stream(_MODEL, request)]
        assert any(event.type == "text_delta" and event.text for event in events)
        assert events[-1].type == "done"
        assert events[-1].usage is not None
        assert events[-1].usage.output_tokens > 0
    finally:
        await provider.aclose()
