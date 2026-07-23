"""Celery worker entrypoint: ``celery -A atlas.infrastructure.jobs.worker``.

This module is the worker's import-time composition root (the Celery
model requires an importable app object). The task body is a thin shell:
Celery owns transport and redelivery; the use case owns ALL job-state
semantics, so a task is safe to re-execute at every point (acks_late +
idempotent hash gate). The retry ladder maps 1:1 — the use case decides
`retry_scheduled` and the countdown, Celery merely schedules it.
"""

import asyncio
from typing import Any
from uuid import UUID

import structlog
from celery import Task
from celery.signals import worker_ready, worker_shutdown

from atlas.application.ingestion import IngestOutcome
from atlas.config.settings import load_settings
from atlas.infrastructure.jobs.celery_app import INGEST_TASK_NAME, create_celery_app
from atlas.infrastructure.jobs.dispatcher import CeleryIngestionDispatcher
from atlas.infrastructure.jobs.runtime import get_runtime
from atlas.infrastructure.watcher.main import WatcherThread
from atlas.observability.logging import bind_trace_id, clear_log_context

celery_app = create_celery_app(load_settings())

_logger = structlog.get_logger("atlas.worker")

_watcher = WatcherThread(load_settings(), CeleryIngestionDispatcher(celery_app))


@worker_ready.connect
def _start_watcher(**_kwargs: Any) -> None:
    """M04 deliverable: the watcher starts with the worker process."""
    _watcher.start()
    _logger.info("watcher.thread_started")


@worker_shutdown.connect
def _stop_watcher(**_kwargs: Any) -> None:
    _watcher.stop()
    _logger.info("watcher.thread_stopped")


def execute_ingestion_attempt(
    workspace_id: str, job_id: str, trace_id: str | None
) -> IngestOutcome:
    """One attempt, fully logged and trace-correlated (shared with tests)."""
    clear_log_context()
    if trace_id:
        bind_trace_id(trace_id)
    runtime = get_runtime()
    outcome = asyncio.run(runtime.ingest_document.execute(UUID(workspace_id), UUID(job_id)))
    _logger.info(
        "ingestion.attempt_finished",
        job_id=job_id,
        result=outcome.result,
        retry_delay_seconds=outcome.retry_delay_seconds,
        detail=outcome.detail,
    )
    return outcome


@celery_app.task(bind=True, name=INGEST_TASK_NAME, max_retries=3)
def ingest_document_task(
    self: "Task[[str, str, str | None], str]",
    workspace_id: str,
    job_id: str,
    trace_id: str | None = None,
) -> str:
    outcome = execute_ingestion_attempt(workspace_id, job_id, trace_id)
    if outcome.result == "retry_scheduled":
        raise self.retry(countdown=outcome.retry_delay_seconds)
    return outcome.result
