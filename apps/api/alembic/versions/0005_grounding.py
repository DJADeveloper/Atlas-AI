"""Grounding: prompts, prompt_versions, citations (M08; docs/11 §2.4, §5).

Two immutable families:

- `prompt_versions` — a version's template never changes; content_hash
  guards against silent drift (the registry refuses to boot over a
  mutated template).
- `citations` — markers → chunks with provenance. `chunk_id`
  deliberately has NO cascade: deleting a cited chunk must fail loudly;
  that restrictive FK is the retention interlock (docs/11 §4).

`messages.prompt_version` (text, an M07 stopgap) becomes
`prompt_version_id` (FK). Pre-release data note: the text column is
dropped without value migration — the only writers so far are dev
environments; the registry did not exist to resolve labels against.
Reversible: downgrade restores the text column (empty) and drops the
new tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column("id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_prompts"),
        sa.UniqueConstraint("name", name="uq_prompts_name"),
    )

    op.create_table(
        "prompt_versions",
        sa.Column("id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("prompt_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("variables", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_versions"),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompts.id"], name="fk_prompt_versions_prompt_id_prompts"
        ),
        sa.UniqueConstraint("prompt_id", "version", name="uq_prompt_versions_prompt_id_version"),
    )

    op.create_table(
        "citations",
        sa.Column("id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("message_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("marker", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_citations"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_citations_message_id_messages",
            ondelete="CASCADE",
        ),
        # No cascade on purpose: the retention interlock (docs/11 §4).
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], name="fk_citations_chunk_id_chunks"),
        sa.UniqueConstraint("message_id", "marker", name="uq_citations_message_id_marker"),
    )
    op.create_index("citations_chunk_idx", "citations", ["chunk_id"])

    op.drop_column("messages", "prompt_version")
    op.add_column("messages", sa.Column("prompt_version_id", PGUUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_messages_prompt_version_id_prompt_versions",
        "messages",
        "prompt_versions",
        ["prompt_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_messages_prompt_version_id_prompt_versions", "messages", type_="foreignkey"
    )
    op.drop_column("messages", "prompt_version_id")
    op.add_column("messages", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.drop_index("citations_chunk_idx", table_name="citations")
    op.drop_table("citations")
    op.drop_table("prompt_versions")
    op.drop_table("prompts")
