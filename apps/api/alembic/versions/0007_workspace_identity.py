"""Unique workspace identity per owner (M09).

Two fresh processes can seed the first-run user and workspace
concurrently (a page load fires parallel requests on first boot).
users.email already carries a unique constraint, so that half of the
race surfaces as an IntegrityError; workspaces had none, so the loser
would commit a duplicate "Local" workspace and split later data across
the two. Uniqueness turns both halves of the race into the same
IntegrityError the seeder retries. Reversible: drop the constraint.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workspaces_owner_user_id_name", "workspaces", ["owner_user_id", "name"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_workspaces_owner_user_id_name", "workspaces", type_="unique")
