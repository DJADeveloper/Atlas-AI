"""Chunk staging and per-stage job visibility (M05).

The chunk stage inserts chunk rows before their vectors exist, so
``embedding``/``embedding_model`` become nullable **as a pair** (CHECK);
``content_hash`` digests each chunk's exact embedded text and, with
``embedding_model``, keys the embedding cache — Postgres itself is the
cache (docs/21 §6), served by the ``chunks_embedding_cache`` index.
``ingestion_jobs.stage`` records where in parse→chunk→embed→index a job
is (docs/21 §3). The chunks table has no writer before M05, so the new
NOT NULL column is safe on every upgrade path. Reversible: downgrade
deletes staged (unembedded) rows — they are pipeline intermediates,
recomputable from file bytes — and restores the NOT NULLs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("content_hash", sa.Text(), nullable=False))
    op.alter_column("chunks", "embedding", nullable=True)
    op.alter_column("chunks", "embedding_model", nullable=True)
    # Bare names here: the naming convention on the model MetaData is in
    # force for op.* directives too, so "embedding_paired" renders as
    # ck_chunks_embedding_paired — passing the full name would double it.
    op.create_check_constraint(
        "embedding_paired",
        "chunks",
        "(embedding IS NULL) = (embedding_model IS NULL)",
    )
    op.create_index("chunks_embedding_cache", "chunks", ["embedding_model", "content_hash"])

    op.add_column(
        "ingestion_jobs",
        sa.Column("stage", sa.Text(), server_default=sa.text("'parse'"), nullable=False),
    )
    op.create_check_constraint(
        "stage_allowed",
        "ingestion_jobs",
        "stage IN ('parse', 'chunk', 'embed', 'index')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ingestion_jobs_stage_allowed", "ingestion_jobs")
    op.drop_column("ingestion_jobs", "stage")

    op.drop_index("chunks_embedding_cache", table_name="chunks")
    op.drop_constraint("ck_chunks_embedding_paired", "chunks")
    op.execute("DELETE FROM chunks WHERE embedding IS NULL")
    op.alter_column("chunks", "embedding_model", nullable=False)
    op.alter_column("chunks", "embedding", nullable=False)
    op.drop_column("chunks", "content_hash")
