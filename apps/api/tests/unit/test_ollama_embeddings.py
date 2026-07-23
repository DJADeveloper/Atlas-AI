"""Ollama embedding adapter over a mock transport: prefixes, batching,
ordering, retry, and the 768d dimension gate (M05, ADR-0005)."""

import hashlib
import json

import httpx
import pytest

from atlas.infrastructure.providers.ollama import (
    OllamaEmbeddingError,
    OllamaEmbeddingProvider,
)

DIMENSIONS = 8


def _vector_for(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [digest[i] / 255.0 for i in range(DIMENSIONS)]


class _Server:
    """Mock Ollama: deterministic per-text vectors, scripted failures."""

    def __init__(self, fail_first: int = 0, dimensions: int = DIMENSIONS) -> None:
        self.requests: list[list[str]] = []
        self.fail_first = fail_first
        self.dimensions = dimensions

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(list(body["input"]))
        if self.fail_first > 0:
            self.fail_first -= 1
            return httpx.Response(500, json={"error": "loading model"})
        vectors = [_vector_for(text)[: self.dimensions] for text in body["input"]]
        padded = [v + [0.0] * (self.dimensions - len(v)) for v in vectors]
        return httpx.Response(200, json={"embeddings": padded})

    def provider(self, **kwargs: int) -> OllamaEmbeddingProvider:
        return OllamaEmbeddingProvider(
            "http://ollama.test",
            "nomic-embed-text",
            dimensions=DIMENSIONS,
            transport=httpx.MockTransport(self.handler),
            **kwargs,
        )


class TestPrefixes:
    async def test_documents_get_the_document_task_prefix(self) -> None:
        server = _Server()
        await server.provider().embed_documents(["alpha", "beta"])
        assert server.requests == [["search_document: alpha", "search_document: beta"]]

    async def test_queries_get_the_query_task_prefix(self) -> None:
        server = _Server()
        await server.provider().embed_query("find the contract")
        assert server.requests == [["search_query: find the contract"]]


class TestBatchingAndOrder:
    async def test_batches_of_thirty_two_by_default(self) -> None:
        server = _Server()
        texts = [f"text {i}" for i in range(70)]
        vectors = await server.provider().embed_documents(texts)
        assert [len(batch) for batch in server.requests] == [32, 32, 6]
        assert len(vectors) == 70

    async def test_order_is_preserved_across_concurrent_batches(self) -> None:
        server = _Server()
        texts = [f"text {i}" for i in range(50)]
        vectors = await server.provider(batch_size=8, concurrency=4).embed_documents(texts)
        for text, vector in zip(texts, vectors, strict=True):
            assert vector == tuple(_vector_for(f"search_document: {text}"))

    async def test_empty_input_makes_no_requests(self) -> None:
        server = _Server()
        assert await server.provider().embed_documents([]) == []
        assert server.requests == []


class TestRetryAndFailure:
    async def test_transient_5xx_is_retried_to_success(self) -> None:
        server = _Server(fail_first=1)
        vectors = await server.provider().embed_documents(["alpha"])
        assert len(vectors) == 1
        assert len(server.requests) == 2

    async def test_persistent_failure_raises_after_retries(self) -> None:
        server = _Server(fail_first=99)
        with pytest.raises(OllamaEmbeddingError):
            await server.provider().embed_documents(["alpha"])
        assert len(server.requests) == 3  # initial + two retries

    async def test_wrong_dimension_fails_loudly(self) -> None:
        server = _Server(dimensions=4)  # model/deployment mismatch
        with pytest.raises(OllamaEmbeddingError) as failure:
            await server.provider().embed_documents(["alpha"])
        assert "expected 8d" in str(failure.value)
