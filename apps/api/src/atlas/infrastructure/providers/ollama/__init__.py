"""Ollama adapters (local models)."""

from atlas.infrastructure.providers.ollama.embeddings import (
    OllamaEmbeddingError,
    OllamaEmbeddingProvider,
)

__all__ = ["OllamaEmbeddingError", "OllamaEmbeddingProvider"]
