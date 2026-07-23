"""Nightly-only: the real nomic-embed-text model behind the adapter.

The PR path never talks to Ollama (M05 risk note) — this suite runs
where `ATLAS_OLLAMA_URL` points at a live instance with the model
pulled (the nightly workflow arranges both).
"""

import os

import pytest

from atlas.infrastructure.providers.ollama import OllamaEmbeddingProvider

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        "ATLAS_OLLAMA_URL" not in os.environ,
        reason="needs a live Ollama with nomic-embed-text (nightly)",
    ),
]


async def test_real_model_returns_768d_vectors_and_prefixes_matter() -> None:
    provider = OllamaEmbeddingProvider(os.environ["ATLAS_OLLAMA_URL"])
    try:
        documents = await provider.embed_documents(
            ["The notice period is 30 days.", "Budget review for Q3 planning."]
        )
        assert len(documents) == 2
        assert all(len(vector) == 768 for vector in documents)
        assert all(any(component != 0.0 for component in vector) for vector in documents)

        # The task prefix is part of the model contract: the same words
        # embedded as a query land on a measurably different vector.
        query = await provider.embed_query("The notice period is 30 days.")
        assert len(query) == 768
        assert query != documents[0]
    finally:
        await provider.aclose()
