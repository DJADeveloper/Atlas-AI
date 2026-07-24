"""RAG building blocks: structure-aware chunking (M05) and RRF fusion
(M06). Framework-free by contract — sits between application and domain
in the import-linter layering."""

from atlas.rag.chunking import (
    HARD_MAX_TOKENS,
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    ChunkDraft,
    build_breadcrumb,
    chunk_blocks,
    embedded_text,
)
from atlas.rag.fusion import (
    FUSED_RESULT_LIMIT,
    KEYWORD_CANDIDATES,
    RRF_K,
    VECTOR_CANDIDATES,
    FusedResult,
    fuse,
)

__all__ = [
    "FUSED_RESULT_LIMIT",
    "HARD_MAX_TOKENS",
    "KEYWORD_CANDIDATES",
    "OVERLAP_TOKENS",
    "RRF_K",
    "TARGET_TOKENS",
    "VECTOR_CANDIDATES",
    "ChunkDraft",
    "FusedResult",
    "build_breadcrumb",
    "chunk_blocks",
    "embedded_text",
    "fuse",
]
