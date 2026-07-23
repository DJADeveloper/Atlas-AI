"""Ollama `EmbeddingProvider` adapter for nomic-embed-text (docs/21 §6).

The adapter owns everything callers must never think about:

- **Task prefixes**: nomic-embed-text is instruction-trained — documents
  embed as ``search_document: <text>``, queries as ``search_query:
  <text>``. Omitting them measurably degrades retrieval, so the prefix
  lives here, not in call sites.
- **Batching**: 32 texts per call, 2 concurrent batches — enough to
  saturate a laptop-class Ollama without starving interactive query
  embeddings on the same instance.
- **Quick retry**: two short back-offs smooth transient hiccups; real
  outages are the job ladder's problem (30 s/2 m/10 m, M04), not ours.
- **Dimension checking**: a vector that is not exactly 768 wide would
  poison the index; it fails loudly here instead.
"""

import asyncio
from collections.abc import Sequence

import httpx

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

_TIMEOUT_SECONDS = 120.0  # CPU-only laptops embed slowly; be patient
_RETRY_DELAYS_SECONDS = (0.2, 0.5)


class OllamaEmbeddingError(RuntimeError):
    """Embedding call failed after retries, or returned malformed data."""


class OllamaEmbeddingProvider:
    """`EmbeddingProvider` port adapter over Ollama's /api/embed."""

    def __init__(
        self,
        base_url: str,
        model: str = "nomic-embed-text",
        *,
        dimensions: int = 768,
        batch_size: int = 32,
        concurrency: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._semaphore_size = concurrency
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=_TIMEOUT_SECONDS,
            transport=transport,
        )

    @property
    def model(self) -> str:
        return self._model

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        prefixed = [f"{DOCUMENT_PREFIX}{text}" for text in texts]
        batches = [
            prefixed[start : start + self._batch_size]
            for start in range(0, len(prefixed), self._batch_size)
        ]
        semaphore = asyncio.Semaphore(self._semaphore_size)

        async def run(batch: list[str]) -> list[tuple[float, ...]]:
            async with semaphore:
                return await self._embed_batch(batch)

        results = await asyncio.gather(*(run(batch) for batch in batches))
        return [vector for batch_vectors in results for vector in batch_vectors]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        vectors = await self._embed_batch([f"{QUERY_PREFIX}{text}"])
        return vectors[0]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _embed_batch(self, batch: list[str]) -> list[tuple[float, ...]]:
        response = await self._request_with_retry(batch)
        payload = response.json()
        raw_vectors = payload.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(batch):
            raise OllamaEmbeddingError(
                f"expected {len(batch)} embeddings, got "
                f"{len(raw_vectors) if isinstance(raw_vectors, list) else type(raw_vectors)}"
            )
        vectors: list[tuple[float, ...]] = []
        for raw in raw_vectors:
            vector = tuple(float(component) for component in raw)
            if len(vector) != self._dimensions:
                raise OllamaEmbeddingError(
                    f"model {self._model} returned {len(vector)}d vector, "
                    f"expected {self._dimensions}d (spine §7 dimension rule)"
                )
            vectors.append(vector)
        return vectors

    async def _request_with_retry(self, batch: list[str]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS_SECONDS, None)):
            try:
                response = await self._client.post(
                    "/api/embed", json={"model": self._model, "input": batch}
                )
                if response.is_server_error and delay is not None:
                    last_error = OllamaEmbeddingError(
                        f"Ollama returned {response.status_code} (attempt {attempt + 1})"
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                last_error = error
                if delay is None:
                    break
                await asyncio.sleep(delay)
        raise OllamaEmbeddingError(f"embedding request failed: {last_error}") from last_error
