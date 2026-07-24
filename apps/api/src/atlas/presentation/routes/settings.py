"""Workspace settings (M09; docs/12): the profile toggle.

The persisted value lives in the `settings` table (key "profile") and
is cached on app state so `/health` — a liveness probe that must never
touch the database — reflects the EFFECTIVE profile immediately after
a PATCH. Model routing reads the profile at process start; a runtime
profile switch taking effect mid-process joins the desktop packaging
work (M14), and the response says which value is live.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select

from atlas.config.profiles import Profile
from atlas.infrastructure.persistence.tables import SettingRow
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container, get_workspace_id
from atlas.shared.ids import uuid7

router = APIRouter(prefix="/api/v1", tags=["settings"])

_PROFILE_KEY = "profile"


class SettingsView(BaseModel):
    profile: Profile
    # The routing profile the RUNNING process was composed with; equals
    # `profile` after restart. Honesty over magic (spine §2.7).
    active_profile: Profile


class PatchSettingsBody(BaseModel):
    profile: Profile


async def _read_persisted_profile(container: Container, workspace_id: UUID) -> Profile | None:
    async with container.session_factory() as session:
        row = (
            await session.execute(
                select(SettingRow)
                .where(SettingRow.workspace_id == workspace_id)
                .where(SettingRow.key == _PROFILE_KEY)
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    value = row.value.get("value")
    return value if value in ("hybrid", "local-only") else None


def _effective_profile(request: Request) -> Profile:
    cached: Profile | None = getattr(request.app.state, "profile_override", None)
    if cached is not None:
        return cached
    settings = request.app.state.settings
    profile: Profile = settings.profile
    return profile


@router.get("/settings")
async def get_settings(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> SettingsView:
    persisted = await _read_persisted_profile(container, workspace_id)
    if persisted is not None:
        request.app.state.profile_override = persisted
    return SettingsView(
        profile=persisted if persisted is not None else _effective_profile(request),
        active_profile=container.settings.profile,
    )


@router.patch("/settings")
async def patch_settings(
    body: PatchSettingsBody,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> SettingsView:
    async with container.session_factory() as session:
        row = (
            await session.execute(
                select(SettingRow)
                .where(SettingRow.workspace_id == workspace_id)
                .where(SettingRow.key == _PROFILE_KEY)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                SettingRow(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    key=_PROFILE_KEY,
                    value={"value": body.profile},
                )
            )
        else:
            row.value = {"value": body.profile}
        await session.commit()
    request.app.state.profile_override = body.profile
    return SettingsView(profile=body.profile, active_profile=container.settings.profile)
