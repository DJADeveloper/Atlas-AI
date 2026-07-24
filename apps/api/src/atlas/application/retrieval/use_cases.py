"""Hybrid retrieval use case (M06; spine §10 end-to-end).

One query becomes two concurrent candidate generations — pgvector
cosine over HNSW and Postgres FTS — fused by RRF and cut to the final
top 8. Scores never mix across modes; ranks do (see `atlas.rag.fusion`).
The optional reranker port runs over the fused list when configured;
absent a reranker, fusion order IS the result order.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.application.ports import (
    CandidateSearcher,
    EmbeddingProvider,
    Reranker,
    SearchFilters,
)
from atlas.rag import (
    FUSED_RESULT_LIMIT,
    KEYWORD_CANDIDATES,
    VECTOR_CANDIDATES,
    FusedResult,
    fuse,
)
from atlas.shared.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One retrieved chunk with its fused score, raw component ranks
    (debuggability, M06 risk note), and FTS highlight when present."""

    chunk_id: UUID
    document_id: UUID
    text: str
    score: float
    vector_rank: int | None
    keyword_rank: int | None
    heading_path: tuple[str, ...]
    highlight: str | None


@dataclass(frozen=True)
class HybridSearch:
    """spine §10: top 24 vector ∥ top 24 FTS → RRF k=60 → top 8."""

    searcher: CandidateSearcher
    embedder: EmbeddingProvider
    reranker: Reranker | None = None  # off by default (spine §10)

    async def execute(
        self,
        workspace_id: UUID,
        query: str,
        *,
        filters: SearchFilters | None = None,
        limit: int = FUSED_RESULT_LIMIT,
    ) -> list[SearchResult]:
        if not query.strip():
            raise ValidationFailed("search query must not be empty")
        if not 1 <= limit <= FUSED_RESULT_LIMIT:
            raise ValidationFailed(f"limit must be between 1 and {FUSED_RESULT_LIMIT}")
        resolved_filters = filters if filters is not None else SearchFilters()

        query_vector = await self.embedder.embed_query(query)
        vector_candidates, keyword_candidates = await asyncio.gather(
            self.searcher.find_vector_candidates(
                workspace_id, query_vector, limit=VECTOR_CANDIDATES, filters=resolved_filters
            ),
            self.searcher.find_keyword_candidates(
                workspace_id, query, limit=KEYWORD_CANDIDATES, filters=resolved_filters
            ),
        )

        by_id = {c.chunk_id: c for c in vector_candidates}
        by_id |= {c.chunk_id: c for c in keyword_candidates}  # keyword wins: it has highlights
        fused = fuse(
            [c.chunk_id for c in vector_candidates],
            [c.chunk_id for c in keyword_candidates],
        )

        if self.reranker is not None:
            reranked = await self.reranker.rerank(query, [by_id[f.chunk_id] for f in fused])
            fused = _reorder(fused, [c.chunk_id for c in reranked])

        return [
            SearchResult(
                chunk_id=f.chunk_id,
                document_id=by_id[f.chunk_id].document_id,
                text=by_id[f.chunk_id].text,
                score=f.score,
                vector_rank=f.vector_rank,
                keyword_rank=f.keyword_rank,
                heading_path=by_id[f.chunk_id].heading_path,
                highlight=by_id[f.chunk_id].highlight,
            )
            for f in fused[:limit]
        ]


def _reorder(fused: Sequence[FusedResult], order: Sequence[UUID]) -> list[FusedResult]:
    """Apply the reranker's ordering while keeping fusion metadata; ids
    the reranker dropped (it must not, but ports are boundaries) sink."""
    position = {chunk_id: index for index, chunk_id in enumerate(order)}
    return sorted(fused, key=lambda f: position.get(f.chunk_id, len(position)))
