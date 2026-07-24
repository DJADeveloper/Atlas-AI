"""Celery-backed dispatchers used by the API process.

Uses ``send_task`` by name so the API never imports the worker module
(no task-registry coupling between processes).
"""

from uuid import UUID

from celery import Celery

from atlas.infrastructure.jobs.celery_app import (
    CHAT_QUEUE,
    EMBED_QUEUE,
    EMBED_TASK_NAME,
    INGEST_QUEUE,
    INGEST_TASK_NAME,
    SUMMARIZE_TASK_NAME,
    TITLE_TASK_NAME,
)


class CeleryIngestionDispatcher:
    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    def dispatch(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        self._celery.send_task(
            INGEST_TASK_NAME,
            args=[str(workspace_id), str(job_id), trace_id],
            queue=INGEST_QUEUE,
        )

    def dispatch_embed(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        self._celery.send_task(
            EMBED_TASK_NAME,
            args=[str(workspace_id), str(job_id), trace_id],
            queue=EMBED_QUEUE,
        )


class CeleryChatDispatcher:
    """`ChatDispatcher` port adapter (M07): fire-and-forget folding and
    titling on their own queue."""

    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    def dispatch_summarize(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None:
        self._celery.send_task(
            SUMMARIZE_TASK_NAME,
            args=[str(workspace_id), str(conversation_id), trace_id],
            queue=CHAT_QUEUE,
        )

    def dispatch_title(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None:
        self._celery.send_task(
            TITLE_TASK_NAME,
            args=[str(workspace_id), str(conversation_id), trace_id],
            queue=CHAT_QUEUE,
        )
