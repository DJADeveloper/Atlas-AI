"""Ollama `LLMProvider` adapter over /api/chat (docs/20 §3).

The local rung of every chain and the only rung in local-only profile
(`is_local = True`). Same four jobs as the anthropic adapter, with the
local-model quirks handled here so callers never see them:

- **Mapping** — Ollama's chat shape is close to the port shape; tool
  calls ride as `message.tool_calls[].function` and carry no id, so
  the adapter mints stable per-response ids (`call_0`, `call_1`, ...)
  to keep the `ToolCall` contract honest.
- **Streaming** — newline-delimited JSON, one object per chunk; the
  final object carries `done_reason` and the token counts.
- **`done_reason` normalization** — `stop` → `end_turn`, `length` →
  `max_tokens`. Ollama does not distinguish a stop-sequence hit from a
  natural stop, so both arrive as `end_turn`; a response carrying tool
  calls is `tool_use` regardless.
- **Usage** — `prompt_eval_count`/`eval_count` when reported; when the
  server omits them (cached prompts do), the adapter estimates chars÷4
  and says so via `estimated=True` — an honest guess, never a silent
  one (docs/20 §3 job 4).
"""

import json
from collections.abc import AsyncIterator

import httpx

from atlas.domain.ai.errors import (
    MalformedResponse,
    ModelUnavailable,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)
from atlas.domain.ai.provider import (
    ChatEvent,
    ChatRequest,
    ChatResponse,
    ProviderChatMessage,
    StopReason,
    ToolCall,
    Usage,
)

# interactive_stream class defaults (docs/20 §5.1). Local models on
# CPU-only laptops generate slowly; the total budget stays generous.
_TOTAL_TIMEOUT_SECONDS = 300.0
_CONNECT_TIMEOUT_SECONDS = 5.0

_DONE_REASONS: dict[str, StopReason] = {"stop": "end_turn", "length": "max_tokens"}

_ESTIMATE_CHARS_PER_TOKEN = 4


class OllamaChatProvider:
    """`LLMProvider` port adapter for a local Ollama instance."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = _TOTAL_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=_CONNECT_TIMEOUT_SECONDS),
            transport=transport,
        )

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def is_local(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse:
        try:
            response = await self._client.post(
                "/api/chat", json=_to_wire(model, request, stream=False)
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeout(str(error)) from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"Ollama unreachable: {error}") from error
        _raise_for_status(response, model)
        payload = _parse_json(response.text)
        return self._to_response(model, request, payload)

    async def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        try:
            async with self._client.stream(
                "POST", "/api/chat", json=_to_wire(model, request, stream=True)
            ) as response:
                if response.status_code != httpx.codes.OK:
                    await response.aread()
                    _raise_for_status(response, model)
                minted_ids = 0
                emitted_chars = 0
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = _parse_json(line)
                    _raise_on_inband_error(chunk)
                    message = chunk.get("message")
                    if isinstance(message, dict):
                        text = message.get("content")
                        if isinstance(text, str) and text:
                            emitted_chars += len(text)
                            yield ChatEvent(type="text_delta", text=text)
                        for call in _tool_calls(message, start_at=minted_ids):
                            minted_ids += 1
                            yield ChatEvent(type="tool_call_delta", tool_call=call)
                    if chunk.get("done") is True:
                        _normalized_done(chunk, has_tool_calls=minted_ids > 0)
                        usage = _usage(chunk, request, output_chars=emitted_chars)
                        yield ChatEvent(type="usage", usage=usage)
                        yield ChatEvent(type="done", usage=usage)
                        return
        except httpx.TimeoutException as error:
            raise ProviderTimeout(str(error)) from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"Ollama unreachable: {error}") from error
        raise MalformedResponse("stream ended without a done chunk")

    def _to_response(
        self, model: str, request: ChatRequest, payload: dict[str, object]
    ) -> ChatResponse:
        _raise_on_inband_error(payload)
        message = payload.get("message")
        if not isinstance(message, dict):
            raise MalformedResponse("response has no message object")
        content = message.get("content")
        if not isinstance(content, str):
            raise MalformedResponse("message content is not a string")
        tool_calls = _tool_calls(message, start_at=0)
        return ChatResponse(
            message=ProviderChatMessage(role="assistant", content=content, tool_calls=tool_calls),
            usage=_usage(payload, request, output_chars=len(content)),
            stop_reason=_normalized_done(payload, has_tool_calls=bool(tool_calls)),
            model=model,
            provider=self.name,
        )


def _to_wire(model: str, request: ChatRequest, *, stream: bool) -> dict[str, object]:
    options: dict[str, object] = {
        "temperature": request.temperature,
        "num_predict": request.max_tokens,
    }
    if request.stop_sequences:
        options["stop"] = list(request.stop_sequences)
    wire: dict[str, object] = {
        "model": model,
        "messages": [_to_wire_message(message) for message in request.messages],
        "stream": stream,
        "options": options,
    }
    if request.tools:
        wire["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request.tools
        ]
    return wire


def _to_wire_message(message: ProviderChatMessage) -> dict[str, object]:
    wire: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {"function": {"name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    return wire


def _tool_calls(message: dict[str, object], *, start_at: int) -> list[ToolCall]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    calls: list[ToolCall] = []
    for offset, raw in enumerate(raw_calls):
        if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
            raise MalformedResponse("tool call is missing its function object")
        function: dict[str, object] = raw["function"]
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise MalformedResponse("tool call function lacks a name or arguments object")
        calls.append(
            ToolCall(
                id=f"call_{start_at + offset}",  # Ollama sends no id; mint a stable one
                name=name,
                arguments={str(key): value for key, value in arguments.items()},
            )
        )
    return calls


def _usage(payload: dict[str, object], request: ChatRequest, *, output_chars: int) -> Usage:
    prompt_tokens = payload.get("prompt_eval_count")
    output_tokens = payload.get("eval_count")
    if isinstance(prompt_tokens, int) and isinstance(output_tokens, int):
        return Usage(input_tokens=prompt_tokens, output_tokens=output_tokens)
    input_chars = sum(len(message.content) for message in request.messages)
    return Usage(
        input_tokens=input_chars // _ESTIMATE_CHARS_PER_TOKEN,
        output_tokens=output_chars // _ESTIMATE_CHARS_PER_TOKEN,
        estimated=True,
    )


def _normalized_done(payload: dict[str, object], *, has_tool_calls: bool) -> StopReason:
    if has_tool_calls:
        return "tool_use"
    raw = payload.get("done_reason", "stop")
    mapped = _DONE_REASONS.get(raw) if isinstance(raw, str) else None
    if mapped is None:
        raise MalformedResponse(f"unmapped done_reason {raw!r}")
    return mapped


def _parse_json(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise MalformedResponse(f"response is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise MalformedResponse(f"expected a JSON object, got {type(payload).__name__}")
    return payload


def _raise_on_inband_error(payload: dict[str, object]) -> None:
    error = payload.get("error")
    if error is not None:
        raise ProviderUnavailable(f"Ollama reported an error: {error}")


def _raise_for_status(response: httpx.Response, model: str) -> None:
    if response.status_code == httpx.codes.NOT_FOUND:
        raise ModelUnavailable(f"model {model!r} is not available on this Ollama (pull it first)")
    if response.is_server_error:
        raise ProviderUnavailable(f"Ollama returned {response.status_code}")
    if response.is_error:
        raise ProviderError(f"Ollama rejected the request: {response.status_code} {response.text}")
