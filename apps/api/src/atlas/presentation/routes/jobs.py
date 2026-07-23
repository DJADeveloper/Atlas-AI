"""Ingestion-job visibility endpoints (M04: nothing fails silently)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from atlas.domain.knowledge.entities import IngestionJob
from atlas.domain.knowledge.values import IngestionState
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container, get_workspace_id
from atlas.shared.errors import NotFound

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


class JobView(BaseModel):
    id: UUID
    source_id: UUID
    document_id: UUID | None
    state: str
    dead_letter: bool
    attempts: int
    error: str | None
    trace_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @classmethod
    def from_entity(cls, job: IngestionJob) -> "JobView":
        return cls(
            id=job.id,
            source_id=job.source_id,
            document_id=job.document_id,
            state=job.state,
            dead_letter=job.is_dead_lettered,
            attempts=job.attempts,
            error=job.error,
            trace_id=job.trace_id,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
        )


@router.get("")
async def list_jobs(
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
    state: Annotated[IngestionState | None, Query()] = None,
    source_id: Annotated[UUID | None, Query()] = None,
    trace_id: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[JobView]:
    async with container.unit_of_work() as uow:
        jobs = await uow.ingestion_jobs.list_jobs(
            workspace_id, state=state, source_id=source_id, trace_id=trace_id, limit=limit
        )
    return [JobView.from_entity(job) for job in jobs]


@router.get("/{job_id}")
async def get_job(
    job_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> JobView:
    async with container.unit_of_work() as uow:
        job = await uow.ingestion_jobs.get(workspace_id, job_id)
    if job is None:
        raise NotFound(f"ingestion job {job_id} not found")
    return JobView.from_entity(job)
