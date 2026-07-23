"""RAG building blocks: structure-aware chunking (M05); retrieval joins
at M06. Framework-free by contract — sits between application and domain
in the import-linter layering."""

from atlas.rag.chunking import (
    HARD_MAX_TOKENS,
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    ChunkDraft,
    build_breadcrumb,
    chunk_blocks,
)

__all__ = [
    "HARD_MAX_TOKENS",
    "OVERLAP_TOKENS",
    "TARGET_TOKENS",
    "ChunkDraft",
    "build_breadcrumb",
    "chunk_blocks",
]
