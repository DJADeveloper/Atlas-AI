"""Memories CRUD, v1 (M07; docs/12 + docs/22 §5-6).

The explicit path only: direct API writes activate immediately. The
extraction pipeline that proposes memories from conversation joins at
M13; `project_fact` is rejected until projects exist (the entity
enforces it — a project fact without a project is unrepresentable).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from atlas.domain.memory.entities import Memory, MemoryKind
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container, get_workspace_id

router = APIRouter(prefix="/api/v1", tags=["memories"])

MAX_MEMORY_LENGTH = 4_000


class MemoryView(BaseModel):
    id: UUID
    kind: MemoryKind
    content: str
    confidence: float | None
    source_message_id: UUID | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_memory(cls, memory: Memory) -> "MemoryView":
        return cls(
            id=memory.id,
            kind=memory.kind,
            content=memory.content,
            confidence=memory.confidence,
            source_message_id=memory.source_message_id,
            expires_at=memory.expires_at,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )


class MemoryListResponse(BaseModel):
    items: list[MemoryView]


class CreateMemoryBody(BaseModel):
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=MAX_MEMORY_LENGTH)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_message_id: UUID | None = None
    expires_at: datetime | None = None


class ReviseMemoryBody(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MEMORY_LENGTH)


@router.get("/memories")
async def list_memories(
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> MemoryListResponse:
    memories = await container.list_memories().execute(workspace_id)
    return MemoryListResponse(items=[MemoryView.from_memory(memory) for memory in memories])


@router.post("/memories", status_code=201)
async def create_memory(
    body: CreateMemoryBody,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> MemoryView:
    memory = await container.remember_fact().execute(
        workspace_id,
        body.kind,
        body.content,
        source_message_id=body.source_message_id,
        confidence=body.confidence,
        expires_at=body.expires_at,
    )
    return MemoryView.from_memory(memory)


@router.patch("/memories/{memory_id}")
async def revise_memory(
    memory_id: UUID,
    body: ReviseMemoryBody,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> MemoryView:
    memory = await container.revise_memory().execute(workspace_id, memory_id, body.content)
    return MemoryView.from_memory(memory)


@router.delete("/memories/{memory_id}", status_code=204)
async def forget_memory(
    memory_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> None:
    await container.forget_memory().execute(workspace_id, memory_id)
