"""Ollama chat adapter against a mock transport: /api/chat wire
mapping, ndjson streaming, done_reason normalization, minted tool-call
ids, estimated-usage honesty, and error mapping (docs/20 §3)."""

import json
from collections.abc import Callable

import httpx
import pytest

from atlas.domain.ai import (
    ChatRequest,
    LLMProvider,
    MalformedResponse,
    ModelUnavailable,
    ProviderChatMessage,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
)
from atlas.infrastructure.providers.ollama import OllamaChatProvider

REQUEST = ChatRequest(messages=[ProviderChatMessage(role="user", content="hello")], max_tokens=64)


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> OllamaChatProvider:
    return OllamaChatProvider("http://localhost:11434", transport=httpx.MockTransport(handler))


def _done_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": "llama3.1:8b",
        "message": {"role": "assistant", "content": "hi"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 12,
        "eval_count": 7,
    }
    payload.update(overrides)
    return payload


def _ndjson(chunks: list[dict[str, object]]) -> httpx.Response:
    body = "".join(json.dumps(chunk) + "\n" for chunk in chunks)
    return httpx.Response(200, content=body.encode())


class TestCompleteMapping:
    async def test_round_trip_maps_request_and_response(self) -> None:
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_done_payload())

        request = ChatRequest(
            messages=[
                ProviderChatMessage(role="system", content="be brief"),
                ProviderChatMessage(role="user", content="hello"),
            ],
            max_tokens=64,
            stop_sequences=["END"],
        )
        response = await _provider(handler).complete("llama3.1:8b", request)

        body = captured[0]
        assert body["model"] == "llama3.1:8b"
        assert body["stream"] is False
        # System messages pass through: Ollama takes them in-line.
        assert body["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ]
        assert body["options"] == {"temperature": 0.2, "num_predict": 64, "stop": ["END"]}
        assert response.message.content == "hi"
        assert response.usage.input_tokens == 12
        assert response.usage.output_tokens == 7
        assert response.usage.estimated is False
        assert response.stop_reason == "end_turn"
        assert response.provider == "ollama"

    async def test_tools_map_to_function_specs(self) -> None:
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_done_payload())

        request = ChatRequest(
            messages=[ProviderChatMessage(role="user", content="hello")],
            tools=[ToolSpec(name="calc", description="math", input_schema={"type": "object"})],
            max_tokens=64,
        )
        await _provider(handler).complete("llama3.1:8b", request)
        assert captured[0]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "calc",
                    "description": "math",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def test_tool_calls_get_minted_ids_and_tool_use_stop(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_done_payload(
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "calc", "arguments": {"expr": "6*7"}}},
                            {"function": {"name": "calc", "arguments": {"expr": "1+1"}}},
                        ],
                    }
                ),
            )

        response = await _provider(handler).complete("llama3.1:8b", REQUEST)
        assert response.stop_reason == "tool_use"
        assert response.message.tool_calls == [
            ToolCall(id="call_0", name="calc", arguments={"expr": "6*7"}),
            ToolCall(id="call_1", name="calc", arguments={"expr": "1+1"}),
        ]

    async def test_length_done_reason_maps_to_max_tokens(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_done_payload(done_reason="length"))

        response = await _provider(handler).complete("llama3.1:8b", REQUEST)
        assert response.stop_reason == "max_tokens"

    async def test_missing_counts_estimate_chars_over_four_and_say_so(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = _done_payload(message={"role": "assistant", "content": "abcdefgh"})
            del payload["prompt_eval_count"], payload["eval_count"]
            return httpx.Response(200, json=payload)

        request = ChatRequest(
            messages=[ProviderChatMessage(role="user", content="x" * 40)], max_tokens=64
        )
        response = await _provider(handler).complete("llama3.1:8b", request)
        assert response.usage.estimated is True
        assert response.usage.input_tokens == 10  # 40 chars / 4
        assert response.usage.output_tokens == 2  # 8 chars / 4


class TestErrorTaxonomy:
    async def test_connect_error_maps_to_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderUnavailable):
            await _provider(handler).complete("llama3.1:8b", REQUEST)

    async def test_timeout_maps_to_provider_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(ProviderTimeout):
            await _provider(handler).complete("llama3.1:8b", REQUEST)

    async def test_404_maps_to_model_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model 'nope' not found"})

        with pytest.raises(ModelUnavailable):
            await _provider(handler).complete("nope", REQUEST)

    async def test_500_maps_to_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"boom")

        with pytest.raises(ProviderUnavailable):
            await _provider(handler).complete("llama3.1:8b", REQUEST)

    async def test_inband_error_maps_to_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "model requires more system memory"})

        with pytest.raises(ProviderUnavailable):
            await _provider(handler).complete("llama3.1:8b", REQUEST)

    async def test_unparseable_body_maps_to_malformed_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        with pytest.raises(MalformedResponse):
            await _provider(handler).complete("llama3.1:8b", REQUEST)


class TestStreaming:
    async def test_ndjson_stream_yields_deltas_then_usage_then_done(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["stream"] is True
            return _ndjson(
                [
                    {"message": {"role": "assistant", "content": "Hel"}, "done": False},
                    {"message": {"role": "assistant", "content": "lo"}, "done": False},
                    _done_payload(message={"role": "assistant", "content": ""}),
                ]
            )

        events = [e async for e in _provider(handler).stream("llama3.1:8b", REQUEST)]
        assert [e.type for e in events] == ["text_delta", "text_delta", "usage", "done"]
        assert [e.text for e in events[:2]] == ["Hel", "lo"]
        assert events[-1].usage is not None
        assert events[-1].usage.input_tokens == 12
        assert events[-1].usage.output_tokens == 7

    async def test_streamed_tool_call_gets_minted_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ndjson(
                [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"function": {"name": "calc", "arguments": {"expr": "6*7"}}}
                            ],
                        },
                        "done": False,
                    },
                    _done_payload(message={"role": "assistant", "content": ""}),
                ]
            )

        events = [e async for e in _provider(handler).stream("llama3.1:8b", REQUEST)]
        assert events[0].type == "tool_call_delta"
        assert events[0].tool_call == ToolCall(id="call_0", name="calc", arguments={"expr": "6*7"})

    async def test_stream_without_done_chunk_is_malformed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ndjson([{"message": {"role": "assistant", "content": "Hel"}, "done": False}])

        with pytest.raises(MalformedResponse):
            async for _ in _provider(handler).stream("llama3.1:8b", REQUEST):
                pass

    async def test_stream_inband_error_raises_typed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ndjson([{"error": "out of memory"}])

        with pytest.raises(ProviderUnavailable):
            async for _ in _provider(handler).stream("llama3.1:8b", REQUEST):
                pass

    async def test_pre_first_byte_connect_failure_raises_typed(self) -> None:
        """No bytes flowed → the executor may fall back (docs/20 §5.2)."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderUnavailable):
            await anext(_provider(handler).stream("llama3.1:8b", REQUEST))


def test_satisfies_the_provider_port() -> None:
    provider: LLMProvider = OllamaChatProvider("http://localhost:11434")
    assert provider.name == "ollama"
    assert provider.is_local is True
