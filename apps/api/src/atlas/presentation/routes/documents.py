"""Document read endpoints (M06): the source-viewer's data — search
results point here, and M08's citations will too."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from atlas.domain.knowledge.entities import Chunk, Document
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container, get_workspace_id
from atlas.shared.errors import NotFound

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class DocumentView(BaseModel):
    id: UUID
    source_id: UUID
    path: str
    title: str | None
    mime_type: str | None
    current_version_id: UUID | None
    created_at: datetime

    @classmethod
    def from_entity(cls, document: Document) -> "DocumentView":
        return cls(
            id=document.id,
            source_id=document.source_id,
            path=document.path,
            title=document.title,
            mime_type=document.mime_type,
            current_version_id=document.current_version_id,
            created_at=document.created_at,
        )


class ChunkView(BaseModel):
    id: UUID
    ordinal: int
    text: str
    token_count: int
    heading_path: list[str]
    page_start: int | None
    page_end: int | None

    @classmethod
    def from_entity(cls, chunk: Chunk) -> "ChunkView":
        page_start = chunk.meta.get("page_start")
        page_end = chunk.meta.get("page_end")
        return cls(
            id=chunk.id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            token_count=chunk.token_count,
            heading_path=list(chunk.heading_path),
            page_start=page_start if isinstance(page_start, int) else None,
            page_end=page_end if isinstance(page_end, int) else None,
        )


@router.get("/{document_id}")
async def get_document(
    document_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> DocumentView:
    async with container.unit_of_work() as uow:
        document = await uow.documents.get(workspace_id, document_id)
    if document is None:
        raise NotFound(f"document {document_id} not found")
    return DocumentView.from_entity(document)


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    document_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> list[ChunkView]:
    async with container.unit_of_work() as uow:
        document = await uow.documents.get(workspace_id, document_id)
        if document is None:
            raise NotFound(f"document {document_id} not found")
        if document.current_version_id is None:
            return []
        chunks = await uow.chunks.list_for_version(workspace_id, document.current_version_id)
    return [ChunkView.from_entity(chunk) for chunk in chunks]
