"""Background jobs: Celery app, worker runtime, task definitions (ADR-0004).

Celery serves the bulk, stateless, retryable pipeline workload; durable
agent workflows go to Temporal at M19. The database — not the Celery
result backend — is the source of truth for job state (`ingestion_jobs`),
so the result backend stays disabled.
"""
