"""RAG building blocks: structure-aware chunking (M05), RRF fusion
(M06), grounded context + streaming-safe citation extraction (M08).
Framework-free by contract — sits between application and domain in
the import-linter layering."""

from atlas.rag.chunking import (
    HARD_MAX_TOKENS,
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    ChunkDraft,
    build_breadcrumb,
    chunk_blocks,
    embedded_text,
)
from atlas.rag.citations import MarkerAccumulator, extract_markers
from atlas.rag.fusion import (
    FUSED_RESULT_LIMIT,
    KEYWORD_CANDIDATES,
    RRF_K,
    VECTOR_CANDIDATES,
    FusedResult,
    fuse,
)
from atlas.rag.grounding import (
    DEFAULT_ABSTAIN_BELOW,
    MAX_CHUNKS_PER_DOCUMENT,
    GroundedContext,
    GroundingChunk,
    select_grounding,
)

__all__ = [
    "DEFAULT_ABSTAIN_BELOW",
    "FUSED_RESULT_LIMIT",
    "HARD_MAX_TOKENS",
    "KEYWORD_CANDIDATES",
    "MAX_CHUNKS_PER_DOCUMENT",
    "OVERLAP_TOKENS",
    "RRF_K",
    "TARGET_TOKENS",
    "VECTOR_CANDIDATES",
    "ChunkDraft",
    "FusedResult",
    "GroundedContext",
    "GroundingChunk",
    "MarkerAccumulator",
    "build_breadcrumb",
    "chunk_blocks",
    "embedded_text",
    "extract_markers",
    "fuse",
    "select_grounding",
]
