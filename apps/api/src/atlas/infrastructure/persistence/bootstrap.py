"""Development/test seeding: the default local user and workspace.

Atlas is single-user today but every row hangs off a workspace from day
one (M03: scoping is not retrofittable). This helper is idempotent and
race-safe: on first boot a page load fires parallel requests, each of
which may try to seed. uq_users_email and uq_workspaces_owner_user_id_name
turn the race into an IntegrityError; the loser retries and reads the
winner's rows. Used by integration tests and, later, first-run
onboarding (M14).
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.infrastructure.persistence.tables import UserRow, WorkspaceRow
from atlas.shared.ids import uuid7

LOCAL_USER_EMAIL = "local@atlas.localhost"


async def ensure_default_workspace(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str = "Local",
) -> UUID:
    """Create (once) and return the id of the default workspace."""
    try:
        return await _get_or_seed(session_factory, name)
    except IntegrityError:
        # A concurrent process seeded between our read and our insert;
        # its rows won, so a second pass finds them.
        return await _get_or_seed(session_factory, name)


async def _get_or_seed(session_factory: async_sessionmaker[AsyncSession], name: str) -> UUID:
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(WorkspaceRow.id)
                .join(UserRow, WorkspaceRow.owner_user_id == UserRow.id)
                .where(UserRow.email == LOCAL_USER_EMAIL)
                .where(WorkspaceRow.name == name)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        user = (
            await session.execute(select(UserRow).where(UserRow.email == LOCAL_USER_EMAIL))
        ).scalar_one_or_none()
        if user is None:
            user = UserRow(id=uuid7(), email=LOCAL_USER_EMAIL, display_name="Local User")
            session.add(user)
            await session.flush()

        workspace = WorkspaceRow(id=uuid7(), owner_user_id=user.id, name=name)
        session.add(workspace)
        await session.commit()
        return workspace.id
