"""Deterministic providers for E2E and demo runs (M09).

Selected by configuration (`ATLAS_CHAT_PROVIDER=echo`,
`ATLAS_EMBEDDING_PROVIDER=hash`) so the full stack — ingestion,
retrieval, grounding, SSE, persistence — runs end-to-end on CI runners
with no Ollama and no API key. These are compose-level test seams, not
mocks: everything above the provider port is the real production path.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Sequence

from atlas.domain.ai.provider import (
    ChatEvent,
    ChatRequest,
    ChatResponse,
    ProviderChatMessage,
    Usage,
)

_ECHO_DELTAS = ("Based on your documents", " [1]", ", here is what I found.")
_ECHO_TEXT = "".join(_ECHO_DELTAS)
_DELTA_DELAY_SECONDS = 0.05  # visible streaming without slowing E2E


class EchoChatProvider:
    """`LLMProvider` that answers instantly with one citation marker."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def is_local(self) -> bool:
        return True

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=ProviderChatMessage(role="assistant", content=_ECHO_TEXT),
            usage=Usage(input_tokens=1, output_tokens=len(_ECHO_DELTAS)),
            stop_reason="end_turn",
            model=model,
            provider="echo",
        )

    async def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        for delta in _ECHO_DELTAS:
            yield ChatEvent(type="text_delta", text=delta)
            await asyncio.sleep(_DELTA_DELAY_SECONDS)
        usage = Usage(input_tokens=1, output_tokens=len(_ECHO_DELTAS))
        yield ChatEvent(type="usage", usage=usage)
        yield ChatEvent(type="done", usage=usage)

    async def aclose(self) -> None:
        return None


class HashEmbeddingProvider:
    """`EmbeddingProvider` with digest-derived vectors — deterministic,
    dimension-correct, obviously not semantic."""

    def __init__(self, dimensions: int = 768) -> None:
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return "hash-test"

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple(digest[index % len(digest)] / 255.0 for index in range(self._dimensions))

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    async def aclose(self) -> None:
        return None
