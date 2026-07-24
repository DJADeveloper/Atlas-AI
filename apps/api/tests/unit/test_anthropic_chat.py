"""Anthropic chat adapter against a mock transport: wire mapping both
directions, stop-reason normalization, incremental tool-call assembly,
and the vendor-exception → domain-taxonomy table (docs/20 §3)."""

import json
from collections.abc import Callable

import httpx
import pytest

from atlas.domain.ai import (
    AuthFailed,
    ChatRequest,
    ContentRefused,
    ContextWindowExceeded,
    LLMProvider,
    ModelUnavailable,
    ProviderChatMessage,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
    ToolCall,
    ToolSpec,
)
from atlas.infrastructure.providers.anthropic import AnthropicChatProvider

REQUEST = ChatRequest(messages=[ProviderChatMessage(role="user", content="hello")], max_tokens=64)


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> AnthropicChatProvider:
    return AnthropicChatProvider(
        "test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _message_json(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": "hi"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    payload.update(overrides)
    return payload


def _sse(events: list[dict[str, object]]) -> httpx.Response:
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def _error_response(status: int, error_type: str, message: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers,
        json={"type": "error", "error": {"type": error_type, "message": message}},
    )


class TestCompleteMapping:
    async def test_round_trip_maps_request_and_response(self) -> None:
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_message_json())

        request = ChatRequest(
            messages=[
                ProviderChatMessage(role="system", content="be brief"),
                ProviderChatMessage(role="user", content="hello"),
            ],
            tools=[
                ToolSpec(name="search_notes", description="find", input_schema={"type": "object"})
            ],
            max_tokens=64,
            stop_sequences=["END"],
        )
        response = await _provider(handler).complete("claude-sonnet-5", request)

        body = captured[0]
        assert body["model"] == "claude-sonnet-5"
        assert body["max_tokens"] == 64
        assert body["temperature"] == 0.2
        assert body["system"] == "be brief"  # lifted out of messages
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert body["stop_sequences"] == ["END"]
        assert body["tools"] == [
            {"name": "search_notes", "description": "find", "input_schema": {"type": "object"}}
        ]
        assert response.message.content == "hi"
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 5
        assert response.usage.estimated is False
        assert response.stop_reason == "end_turn"
        assert response.provider == "anthropic"

    async def test_tool_conversation_wire_shapes(self) -> None:
        """Assistant tool calls become tool_use blocks; tool results
        ride back as tool_result blocks on a user turn."""
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_message_json())

        request = ChatRequest(
            messages=[
                ProviderChatMessage(role="user", content="what's 6x7?"),
                ProviderChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="toolu_9", name="calc", arguments={"expr": "6*7"})],
                ),
                ProviderChatMessage(role="tool", content="42", tool_call_id="toolu_9"),
            ],
            max_tokens=64,
        )
        await _provider(handler).complete("claude-sonnet-5", request)

        messages = captured[0]["messages"]
        assert messages == [
            {"role": "user", "content": "what's 6x7?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_9", "name": "calc", "input": {"expr": "6*7"}}
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_9", "content": "42"}],
            },
        ]

    async def test_tool_use_response_maps_to_tool_calls(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_message_json(
                    content=[
                        {"type": "text", "text": "let me check"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "search_notes",
                            "input": {"query": "tax"},
                        },
                    ],
                    stop_reason="tool_use",
                ),
            )

        response = await _provider(handler).complete("claude-sonnet-5", REQUEST)
        assert response.stop_reason == "tool_use"
        assert response.message.content == "let me check"
        assert response.message.tool_calls == [
            ToolCall(id="toolu_1", name="search_notes", arguments={"query": "tax"})
        ]

    async def test_max_tokens_stop_reason(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_message_json(stop_reason="max_tokens"))

        response = await _provider(handler).complete("claude-sonnet-5", REQUEST)
        assert response.stop_reason == "max_tokens"

    async def test_refusal_raises_content_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_message_json(stop_reason="refusal"))

        with pytest.raises(ContentRefused):
            await _provider(handler).complete("claude-sonnet-5", REQUEST)


class TestErrorTaxonomy:
    async def test_missing_api_key_fails_before_any_dial(self) -> None:
        dialed = []

        def handler(request: httpx.Request) -> httpx.Response:
            dialed.append(request)
            return httpx.Response(200, json=_message_json())

        provider = AnthropicChatProvider(None)
        with pytest.raises(AuthFailed):
            await provider.complete("claude-sonnet-5", REQUEST)
        with pytest.raises(AuthFailed):
            await anext(provider.stream("claude-sonnet-5", REQUEST))
        assert dialed == []

    async def test_401_maps_to_auth_failed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(401, "authentication_error", "invalid x-api-key")

        with pytest.raises(AuthFailed):
            await _provider(handler).complete("claude-sonnet-5", REQUEST)

    async def test_429_maps_to_rate_limited_with_retry_after(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(429, "rate_limit_error", "slow down", **{"retry-after": "13"})

        with pytest.raises(RateLimited) as excinfo:
            await _provider(handler).complete("claude-sonnet-5", REQUEST)
        assert excinfo.value.retry_after_seconds == 13.0

    async def test_500_maps_to_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(500, "api_error", "internal error")

        with pytest.raises(ProviderUnavailable):
            await _provider(handler).complete("claude-sonnet-5", REQUEST)

    async def test_529_overloaded_maps_to_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(529, "overloaded_error", "overloaded")

        with pytest.raises(ProviderUnavailable):
            await _provider(handler).complete("claude-sonnet-5", REQUEST)

    async def test_404_maps_to_model_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(404, "not_found_error", "model: no-such-model")

        with pytest.raises(ModelUnavailable):
            await _provider(handler).complete("no-such-model", REQUEST)

    async def test_context_overflow_400_maps_to_context_window_exceeded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(
                400, "invalid_request_error", "prompt is too long: 210000 tokens > 200000 maximum"
            )

        with pytest.raises(ContextWindowExceeded):
            await _provider(handler).complete("claude-sonnet-5", REQUEST)

    async def test_connection_error_maps_to_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderUnavailable):
            await _provider(handler).complete("claude-sonnet-5", REQUEST)

    async def test_timeout_maps_to_provider_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(ProviderTimeout):
            await _provider(handler).complete("claude-sonnet-5", REQUEST)


class TestStreaming:
    async def test_text_stream_yields_deltas_then_usage_then_done(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["stream"] is True
            return _sse(
                [
                    {
                        "type": "message_start",
                        "message": _message_json(
                            content=[],
                            stop_reason=None,
                            usage={"input_tokens": 10, "output_tokens": 1},
                        ),
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Hel"},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "lo"},
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 5},
                    },
                    {"type": "message_stop"},
                ]
            )

        events = [e async for e in _provider(handler).stream("claude-sonnet-5", REQUEST)]
        assert [e.type for e in events] == ["text_delta", "text_delta", "usage", "done"]
        assert [e.text for e in events[:2]] == ["Hel", "lo"]
        assert events[-1].usage is not None
        assert events[-1].usage.input_tokens == 10
        assert events[-1].usage.output_tokens == 5  # message_delta wins over message_start

    async def test_tool_call_assembles_from_json_fragments(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse(
                [
                    {
                        "type": "message_start",
                        "message": _message_json(
                            content=[],
                            stop_reason=None,
                            usage={"input_tokens": 8, "output_tokens": 1},
                        ),
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "search_notes",
                            "input": {},
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "input_json_delta", "partial_json": '{"query": '},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "input_json_delta", "partial_json": '"tax"}'},
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                        "usage": {"output_tokens": 9},
                    },
                    {"type": "message_stop"},
                ]
            )

        events = [e async for e in _provider(handler).stream("claude-sonnet-5", REQUEST)]
        assert [e.type for e in events] == ["tool_call_delta", "usage", "done"]
        assert events[0].tool_call == ToolCall(
            id="toolu_1", name="search_notes", arguments={"query": "tax"}
        )

    async def test_streamed_refusal_raises_content_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse(
                [
                    {
                        "type": "message_start",
                        "message": _message_json(
                            content=[],
                            stop_reason=None,
                            usage={"input_tokens": 8, "output_tokens": 1},
                        ),
                    },
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "refusal", "stop_sequence": None},
                        "usage": {"output_tokens": 1},
                    },
                    {"type": "message_stop"},
                ]
            )

        with pytest.raises(ContentRefused):
            async for _ in _provider(handler).stream("claude-sonnet-5", REQUEST):
                pass

    async def test_pre_first_byte_failure_raises_typed_error(self) -> None:
        """The executor falls back only on pre-first-event failures
        (docs/20 §5.2); the adapter must surface them as typed raises."""

        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(529, "overloaded_error", "overloaded")

        with pytest.raises(ProviderUnavailable):
            await anext(_provider(handler).stream("claude-sonnet-5", REQUEST))


def test_satisfies_the_provider_port() -> None:
    provider: LLMProvider = AnthropicChatProvider(None)
    assert provider.name == "anthropic"
    assert provider.is_local is False
