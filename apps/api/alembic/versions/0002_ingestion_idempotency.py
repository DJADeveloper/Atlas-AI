"""Ingestion idempotency key: (document_id, content_hash) on active jobs.

M04 risk mitigation — watcher/reindex races collapse into one job: at
most one non-terminal (pending|running) job may exist per document and
content hash. The partial unique index is migration-owned DDL, excluded
from autogenerate comparison in env.py (Postgres normalizes partial
predicates in ways that make reflected comparison nondeterministic);
its behavior is enforced by integration test instead. Reversible.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("content_hash", sa.Text(), nullable=True))
    op.execute(
        """
        CREATE UNIQUE INDEX ingestion_jobs_active_dedupe_idx
          ON ingestion_jobs (document_id, content_hash)
          WHERE state IN ('pending', 'running')
        """
    )


def downgrade() -> None:
    op.drop_index("ingestion_jobs_active_dedupe_idx", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "content_hash")
