"""Anthropic `LLMProvider` adapter over the official SDK (docs/20 §3).

The adapter has exactly four jobs and does nothing else:

- **Auth** — the key arrives via settings (environment until the M14
  keychain adapter). A missing key raises `AuthFailed` *before* any
  network dial, so the hybrid chain degrades visibly to local rungs
  instead of hanging on a doomed connection.
- **Mapping** — `ChatRequest` → Messages API params (system messages
  lift into the top-level `system` field; tool results ride as
  `tool_result` user blocks) and back, including incremental tool-call
  assembly from `input_json_delta` fragments during streaming.
- **Error normalization** — SDK exceptions collapse into the domain
  taxonomy; the executor above branches on error TYPE only, never on
  vendor status codes. SDK retries are disabled (`max_retries=0`)
  because retry policy lives in `ResilientExecutor`, in exactly one
  place (docs/20 §5.2).
- **Usage extraction** — token counts come from vendor metadata; this
  adapter never estimates because Anthropic always reports them.

`stop_reason` is normalized onto the four-value port vocabulary. A
`refusal` stop is not a fifth value: it raises `ContentRefused`, the
same shape a pre-response refusal takes, so callers handle one case.
"""

import json
from collections.abc import AsyncIterator, Sequence

import anthropic
import httpx
from anthropic import omit
from anthropic.types import (
    Message,
    MessageParam,
    RawMessageStreamEvent,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)

from atlas.domain.ai.errors import (
    AuthFailed,
    ContentRefused,
    ContextWindowExceeded,
    MalformedResponse,
    ModelUnavailable,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
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
from atlas.shared.errors import ValidationFailed

# interactive_stream class defaults (docs/20 §5.1): 5 s connect, 300 s
# total. First-token/inter-token budgets arrive with the SSE layer.
_TOTAL_TIMEOUT_SECONDS = 300.0
_CONNECT_TIMEOUT_SECONDS = 5.0

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
}

_CONTEXT_WINDOW_MARKERS = ("prompt is too long", "exceed context limit")


class AnthropicChatProvider:
    """`LLMProvider` port adapter for the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout_seconds: float = _TOTAL_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client: anthropic.AsyncAnthropic | None = None
        if api_key is not None:
            self._client = anthropic.AsyncAnthropic(
                api_key=api_key,
                max_retries=0,  # ResilientExecutor owns retry policy (§5.2)
                timeout=httpx.Timeout(timeout_seconds, connect=_CONNECT_TIMEOUT_SECONDS),
                http_client=http_client,
            )

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def is_local(self) -> bool:
        return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse:
        client = self._require_client()
        system, messages = _to_wire(request.messages)
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=system if system is not None else omit,
                messages=messages,
                tools=_to_wire_tools(request) or omit,
                stop_sequences=list(request.stop_sequences) or omit,
            )
        except Exception as error:
            raise _normalized(error) from error
        return self._to_response(message)

    async def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        client = self._require_client()
        system, messages = _to_wire(request.messages)
        try:
            events = await client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=system if system is not None else omit,
                messages=messages,
                tools=_to_wire_tools(request) or omit,
                stop_sequences=list(request.stop_sequences) or omit,
                stream=True,
            )
        except Exception as error:
            raise _normalized(error) from error
        async for event in _translate_stream(events):
            yield event

    def _require_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            raise AuthFailed(
                "no Anthropic API key configured (set ATLAS_ANTHROPIC_API_KEY)",
            )
        return self._client

    def _to_response(self, message: Message) -> ChatResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=_as_arguments(block.input))
                )
        return ChatResponse(
            message=ProviderChatMessage(
                role="assistant", content="".join(text_parts), tool_calls=tool_calls
            ),
            usage=Usage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
            stop_reason=_normalized_stop(message.stop_reason),
            model=message.model,
            provider=self.name,
        )


async def _translate_stream(
    events: AsyncIterator[RawMessageStreamEvent],
) -> AsyncIterator[ChatEvent]:
    """Vendor stream → port events. Tool calls assemble incrementally
    from `input_json_delta` fragments and emit whole on block stop —
    partial JSON is a wire detail no caller should ever see."""
    input_tokens = 0
    output_tokens = 0
    tool_id: str | None = None
    tool_name = ""
    tool_json_parts: list[str] = []
    try:
        async for event in events:
            if event.type == "message_start":
                input_tokens = event.message.usage.input_tokens
                output_tokens = event.message.usage.output_tokens
            elif event.type == "content_block_start":
                if event.content_block.type == "tool_use":
                    tool_id = event.content_block.id
                    tool_name = event.content_block.name
                    tool_json_parts = []
            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    yield ChatEvent(type="text_delta", text=event.delta.text)
                elif event.delta.type == "input_json_delta":
                    tool_json_parts.append(event.delta.partial_json)
            elif event.type == "content_block_stop":
                if tool_id is not None:
                    yield ChatEvent(
                        type="tool_call_delta",
                        tool_call=_assemble_tool_call(tool_id, tool_name, tool_json_parts),
                    )
                    tool_id = None
            elif event.type == "message_delta":
                _normalized_stop(event.delta.stop_reason)  # refusal raises here
                if event.usage.output_tokens is not None:
                    output_tokens = event.usage.output_tokens
    except (anthropic.AnthropicError, httpx.HTTPError) as error:
        raise _normalized(error) from error
    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    yield ChatEvent(type="usage", usage=usage)
    yield ChatEvent(type="done", usage=usage)


def _to_wire(
    messages: Sequence[ProviderChatMessage],
) -> tuple[str | None, list[MessageParam]]:
    """Port messages → wire messages. System turns lift into the
    top-level `system` parameter; tool results become `tool_result`
    blocks on a user turn, per the Messages API shape."""
    system_parts: list[str] = []
    wire: list[MessageParam] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
        elif message.role == "user":
            wire.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            wire.append({"role": "assistant", "content": _assistant_blocks(message)})
        else:  # role == "tool"
            if message.tool_call_id is None:
                raise ValidationFailed("tool message is missing tool_call_id")
            result_block: ToolResultBlockParam = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            wire.append({"role": "user", "content": [result_block]})
    return ("\n\n".join(system_parts) if system_parts else None), wire


def _assistant_blocks(
    message: ProviderChatMessage,
) -> list[TextBlockParam | ToolUseBlockParam]:
    blocks: list[TextBlockParam | ToolUseBlockParam] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    return blocks


def _to_wire_tools(request: ChatRequest) -> list[ToolParam]:
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in request.tools
    ]


def _as_arguments(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MalformedResponse(f"tool_use input is {type(value).__name__}, expected an object")
    return {str(key): item for key, item in value.items()}


def _assemble_tool_call(tool_id: str, name: str, json_parts: list[str]) -> ToolCall:
    raw = "".join(json_parts) or "{}"
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as error:
        raise MalformedResponse(f"tool call arguments are not valid JSON: {error}") from error
    return ToolCall(id=tool_id, name=name, arguments=_as_arguments(arguments))


def _normalized_stop(raw: str | None) -> StopReason:
    """Vendor stop vocabulary → the four-value port vocabulary. A
    `refusal` surfaces as `ContentRefused` — honestly, not mapped away
    (docs/20 §3). `None` mid-stream simply means "not stopped yet"."""
    if raw is None:
        return "end_turn"
    if raw == "refusal":
        raise ContentRefused("the model declined to answer this request")
    mapped = _STOP_REASONS.get(raw)
    if mapped is None:
        raise MalformedResponse(f"unmapped stop_reason {raw!r}")
    return mapped


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None  # HTTP-date form: let the executor's backoff decide


def _normalized(error: Exception) -> ProviderError:
    """SDK exceptions → the domain taxonomy (docs/20 §3, the table).

    Order matters: `APITimeoutError` subclasses `APIConnectionError`,
    and the specific status errors subclass `APIStatusError`."""
    if isinstance(error, ProviderError):
        return error  # already ours (raised inside stream translation)
    if isinstance(error, anthropic.APITimeoutError | httpx.TimeoutException):
        return ProviderTimeout(str(error))
    if isinstance(error, anthropic.APIStatusError):
        return _normalized_status(error)
    if isinstance(error, anthropic.APIConnectionError | httpx.HTTPError):
        return ProviderUnavailable(str(error))
    return ProviderError(str(error))


def _normalized_status(error: anthropic.APIStatusError) -> ProviderError:
    if isinstance(error, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return AuthFailed(str(error))
    if isinstance(error, anthropic.RateLimitError):
        return RateLimited(str(error), retry_after_seconds=_retry_after_seconds(error.response))
    if isinstance(error, anthropic.NotFoundError):
        return ModelUnavailable(f"model or endpoint not found: {error}")
    if isinstance(error, anthropic.BadRequestError):
        return _normalized_bad_request(str(error))
    if error.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:  # includes 529 overloaded
        return ProviderUnavailable(str(error))
    return ProviderError(str(error))


def _normalized_bad_request(detail: str) -> ProviderError:
    if any(marker in detail.lower() for marker in _CONTEXT_WINDOW_MARKERS):
        return ContextWindowExceeded(detail)
    return ProviderError(detail)  # our request bug: non-retryable, visible
