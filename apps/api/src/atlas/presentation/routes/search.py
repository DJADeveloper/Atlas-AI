"""Hybrid search endpoint (M06; shapes per docs/12 §search).

The response carries fused scores AND raw component ranks — when
retrieval surprises, the ranks explain which mode believed what
(M06 risk note: debuggability over opaque scores).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from atlas.application.ports import SearchFilters
from atlas.application.retrieval import SearchResult
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container, get_workspace_id
from atlas.rag import FUSED_RESULT_LIMIT

router = APIRouter(prefix="/api/v1", tags=["search"])


class SearchFiltersBody(BaseModel):
    """Shapes per docs/12 §4.4; project_id joins at M13 (projects)."""

    source_ids: list[UUID] = Field(default_factory=list)
    file_types: list[str] = Field(default_factory=list, max_length=20)
    modified_after: datetime | None = None
    modified_before: datetime | None = None

    def to_filters(self) -> SearchFilters:
        return SearchFilters(
            source_ids=tuple(self.source_ids),
            file_types=tuple(self.file_types),
            modified_after=self.modified_after,
            modified_before=self.modified_before,
        )


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=FUSED_RESULT_LIMIT, ge=1, le=FUSED_RESULT_LIMIT)
    # Accepted per docs/12; a no-op until a reranker adapter is
    # configured (spine §10: rerank optional, off by default).
    rerank: bool = False
    filters: SearchFiltersBody = Field(default_factory=SearchFiltersBody)


class SearchResultView(BaseModel):
    chunk_id: UUID
    document_id: UUID
    text: str
    score: float
    vector_rank: int | None
    keyword_rank: int | None
    heading_path: list[str]
    highlight: str | None

    @classmethod
    def from_result(cls, result: SearchResult) -> "SearchResultView":
        return cls(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            text=result.text,
            score=result.score,
            vector_rank=result.vector_rank,
            keyword_rank=result.keyword_rank,
            heading_path=list(result.heading_path),
            highlight=result.highlight,
        )


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultView]


@router.post("/search")
async def search(
    body: SearchRequest,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> SearchResponse:
    results = await container.hybrid_search().execute(
        workspace_id, body.query, filters=body.filters.to_filters(), limit=body.top_k
    )
    return SearchResponse(
        query=body.query,
        results=[SearchResultView.from_result(result) for result in results],
    )
