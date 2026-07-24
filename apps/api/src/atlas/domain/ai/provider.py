"""LLM provider port and message types (docs/20 §2.2, verbatim shape).

Pure vendor-neutral types — no SDK imports, per the dependency rule.
Adapters in `infrastructure/providers/` translate to wire formats;
`atlas/ai` routes and hardens; call sites see only these shapes.

`stop_reason` is normalized: adapters map vendor vocabularies
(`end_turn`, `finish_reason=stop`, Ollama `done_reason`) onto four
values so no caller ever branches on vendor knowledge. Tool calls are
model-produced INTENT, never executed here — the policy engine and
executors own permission and action (spine §2.2)."""

from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, Field

ChatRole = Literal["system", "user", "assistant", "tool"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"]


class ToolSpec(BaseModel):
    """A tool advertised to the model. JSON Schema, vendor-neutral."""

    name: str
    description: str
    input_schema: dict[str, object]


class ToolCall(BaseModel):
    """Model-produced intent. Never executed here (spine §2.2)."""

    id: str
    name: str
    arguments: dict[str, object]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    estimated: bool = False  # True when the adapter had to approximate


class ProviderChatMessage(BaseModel):
    role: ChatRole
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # set when role == "tool"


class ChatRequest(BaseModel):
    messages: Sequence[ProviderChatMessage]
    tools: Sequence[ToolSpec] = ()
    max_tokens: int
    temperature: float = 0.2
    stop_sequences: Sequence[str] = ()


class ChatResponse(BaseModel):
    message: ProviderChatMessage  # may carry tool_calls
    usage: Usage
    stop_reason: StopReason
    model: str  # concrete model id actually used
    provider: str


class ChatEvent(BaseModel):
    """One streaming increment. The terminal event carries usage."""

    type: Literal["text_delta", "tool_call_delta", "usage", "done", "error"]
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    error_code: str | None = None


class LLMProvider(Protocol):
    """Streaming is a first-class method, not a flag: chat streams over
    SSE (ADR-0008); background jobs use `complete`. Two methods keep
    both paths honestly typed."""

    @property
    def name(self) -> str:  # "anthropic" | "openai" | "ollama"
        ...

    @property
    def is_local(self) -> bool: ...

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse: ...

    def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]: ...
