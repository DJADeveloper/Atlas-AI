"""Ollama adapters (local models)."""

from atlas.infrastructure.providers.ollama.chat import OllamaChatProvider
from atlas.infrastructure.providers.ollama.embeddings import (
    OllamaEmbeddingError,
    OllamaEmbeddingProvider,
)

__all__ = ["OllamaChatProvider", "OllamaEmbeddingError", "OllamaEmbeddingProvider"]
