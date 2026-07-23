"""Source management endpoints (M04; shapes per docs/12 §sources)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from atlas.application.ingestion import DetectChanges, RegisterSource, ReindexSource
from atlas.domain.knowledge.entities import Source
from atlas.domain.knowledge.values import SourceStatus
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import current_batch_id, get_container, get_workspace_id
from atlas.shared.errors import NotFound

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])


class SourceView(BaseModel):
    id: UUID
    kind: str
    name: str
    uri: str
    status: str
    config: dict[str, object]
    last_indexed_at: datetime | None
    created_at: datetime

    @classmethod
    def from_entity(cls, source: Source) -> "SourceView":
        return cls(
            id=source.id,
            kind=source.kind,
            name=source.name,
            uri=source.uri,
            status=source.status,
            config=dict(source.config),
            last_indexed_at=source.last_indexed_at,
            created_at=source.created_at,
        )


class RegisterSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    uri: str = Field(min_length=1)
    kind: Literal["folder"] = "folder"
    config: dict[str, object] = Field(default_factory=dict)


class RegisterSourceResponse(BaseModel):
    source: SourceView
    batch_id: str
    enqueued: int
    skipped_unchanged: int


class PatchSourceRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "paused"] | None = None
    config: dict[str, object] | None = None


class ReindexResponse(BaseModel):
    batch_id: str
    enqueued: int
    skipped_unchanged: int
    deduplicated: int


def _detect(container: Container) -> DetectChanges:
    return DetectChanges(
        uow_factory=container.unit_of_work,
        file_store=container.file_store,
        dispatcher=container.dispatcher,
        supports=container.parser_registry.supports,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_source(
    body: RegisterSourceRequest,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
    batch_id: Annotated[str, Depends(current_batch_id)],
) -> RegisterSourceResponse:
    use_case = RegisterSource(
        uow_factory=container.unit_of_work,
        file_store=container.file_store,
        detect_changes=_detect(container),
    )
    source, report = await use_case.execute(
        workspace_id,
        name=body.name,
        uri=body.uri,
        kind=body.kind,
        config=body.config,
        batch_id=batch_id,
    )
    return RegisterSourceResponse(
        source=SourceView.from_entity(source),
        batch_id=report.batch_id,
        enqueued=len(report.enqueued_job_ids),
        skipped_unchanged=report.skipped_unchanged,
    )


@router.get("")
async def list_sources(
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> list[SourceView]:
    async with container.unit_of_work() as uow:
        sources = await uow.sources.list_all(workspace_id)
    return [SourceView.from_entity(source) for source in sources]


@router.patch("/{source_id}")
async def patch_source(
    source_id: UUID,
    body: PatchSourceRequest,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> SourceView:
    async with container.unit_of_work() as uow:
        source = await uow.sources.get(workspace_id, source_id)
        if source is None:
            raise NotFound(f"source {source_id} not found")
        if body.name is not None:
            source.name = body.name
        if body.status is not None:
            new_status: SourceStatus = body.status
            source.status = new_status
        if body.config is not None:
            source.config = dict(body.config)
        await uow.sources.save(source)
        await uow.commit()
    return SourceView.from_entity(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> None:
    async with container.unit_of_work() as uow:
        source = await uow.sources.get(workspace_id, source_id)
        if source is None:
            raise NotFound(f"source {source_id} not found")
        source.soft_delete()
        await uow.sources.save(source)
        await uow.commit()


@router.post("/{source_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_source(
    source_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
    batch_id: Annotated[str, Depends(current_batch_id)],
) -> ReindexResponse:
    use_case = ReindexSource(detect_changes=_detect(container))
    report = await use_case.execute(workspace_id, source_id, batch_id=batch_id)
    return ReindexResponse(
        batch_id=report.batch_id,
        enqueued=len(report.enqueued_job_ids),
        skipped_unchanged=report.skipped_unchanged,
        deduplicated=report.deduplicated,
    )
