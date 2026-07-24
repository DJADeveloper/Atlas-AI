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
    source_ids: list[UUID] = Field(default_factory=list)
    mime_types: list[str] = Field(default_factory=list, max_length=20)
    created_after: datetime | None = None
    created_before: datetime | None = None

    def to_filters(self) -> SearchFilters:
        return SearchFilters(
            source_ids=tuple(self.source_ids),
            mime_types=tuple(self.mime_types),
            created_after=self.created_after,
            created_before=self.created_before,
        )


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=FUSED_RESULT_LIMIT, ge=1, le=FUSED_RESULT_LIMIT)
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
        workspace_id, body.query, filters=body.filters.to_filters(), limit=body.limit
    )
    return SearchResponse(
        query=body.query,
        results=[SearchResultView.from_result(result) for result in results],
    )
