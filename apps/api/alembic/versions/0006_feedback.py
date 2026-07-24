"""Feedback on answers (M09; docs/11 §2.4, docs/12 §4.3).

Immutable rows feeding the eval datasets (M11). `categories` is a
documented addition over the abbreviated docs/11 DDL: the API contract
(docs/12) accepts structured categories ("wrong_citation") and munging
them into the comment would lose the signal the eval reads.
Reversible: drop the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("message_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("user_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("categories", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_feedback_message_id_messages",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_feedback_user_id_users"),
        sa.CheckConstraint("rating IN ('up', 'down')", name="ck_feedback_rating_allowed"),
    )


def downgrade() -> None:
    op.drop_table("feedback")
