"""Celery application factory."""

from celery import Celery

from atlas.config.settings import Settings

INGEST_QUEUE = "ingest.parse"  # queue naming per docs/41 §queues
INGEST_TASK_NAME = "atlas.ingest_document"
# Embed stage rides its own queue (M05): an Ollama outage backs up
# ingest.embed without starving parse work, and vice versa.
EMBED_QUEUE = "ingest.embed"
EMBED_TASK_NAME = "atlas.embed_document"


def create_celery_app(settings: Settings) -> Celery:
    app = Celery("atlas", broker=settings.redis_url)
    app.conf.update(
        task_default_queue=INGEST_QUEUE,
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,  # ingestion_jobs is the source of truth
        task_acks_late=True,  # at-least-once + idempotent tasks (docs/41)
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        task_ignore_result=True,
    )
    return app
