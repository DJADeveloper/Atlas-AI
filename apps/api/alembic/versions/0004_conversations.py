"""Conversations, messages, and memories (M07; docs/11 §2.4-2.5).

Messages are immutable rows whose UUIDv7 PK order is chronological
order; usage accounting (tokens, cost, latency) is first-class data.
prompt_version is plain text until M08's registry adds prompt_versions;
project_id columns join at M13 with projects. Reversible: drop tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        # Rolling summary + watermark (docs/22 §2). No FK on the
        # watermark: messages FK-references conversations, and a
        # circular pair would force use_alter for a column that is a
        # cursor, not a relationship.
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_through_message_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_conversations_workspace_id_workspaces"
        ),
    )
    op.create_index(
        "conversations_ws_idx",
        "conversations",
        ["workspace_id", sa.text("updated_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "messages",
        sa.Column("id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("abstained", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system')",
            name="ck_messages_role_allowed",
        ),
    )
    op.create_index("messages_conversation_idx", "messages", ["conversation_id", "id"])

    op.create_table(
        "memories",
        sa.Column("id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_message_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("expires_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_memories"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_memories_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            name="fk_memories_source_message_id_messages",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "kind IN ('preference', 'project_fact', 'decision', 'entity', 'episodic')",
            name="ck_memories_kind_allowed",
        ),
    )
    op.create_index(
        "memories_scope_idx",
        "memories",
        ["workspace_id", "kind"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("memories_scope_idx", table_name="memories")
    op.drop_table("memories")
    op.drop_index("messages_conversation_idx", table_name="messages")
    op.drop_table("messages")
    op.drop_index("conversations_ws_idx", table_name="conversations")
    op.drop_table("conversations")
