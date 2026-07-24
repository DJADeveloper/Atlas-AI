"""SQL-backed prompt registry (M08; docs/11 §5).

Lazily get-or-creates the code-defined prompt versions on first use —
boot stays pure (no I/O in the composition root) — and caches the row
ids for the process lifetime. Immutability is enforced here: a stored
template whose content hash differs from the code refuses to load;
editing a prompt means registering a NEW version, never mutating one.
"""

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.ai.prompts import PROMPT_SPECS, PromptSpec, RegisteredPrompt
from atlas.infrastructure.persistence.tables import PromptRow, PromptVersionRow
from atlas.shared.ids import uuid7


class PromptVersionMutated(RuntimeError):
    """A registered version's stored template no longer matches the
    code. This is a build error, not a runtime condition."""


class SqlPromptRegistry:
    """`PromptProvider` over the prompts/prompt_versions tables."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        specs: tuple[PromptSpec, ...] = PROMPT_SPECS,
    ) -> None:
        self._session_factory = session_factory
        self._specs = specs
        self._cache: dict[str, RegisteredPrompt] | None = None
        self._lock = asyncio.Lock()

    async def get(self, name: str) -> RegisteredPrompt:
        if self._cache is None:
            async with self._lock:
                if self._cache is None:
                    try:
                        self._cache = await self._load()
                    except IntegrityError:
                        # Another process won the get-or-create race;
                        # the rows exist now — read them.
                        self._cache = await self._load()
        entry = self._cache.get(name)
        if entry is None:
            raise LookupError(f"no registered prompt named {name!r}")
        return entry

    async def _load(self) -> dict[str, RegisteredPrompt]:
        entries: dict[str, RegisteredPrompt] = {}
        async with self._session_factory() as session:
            for spec in self._specs:
                entries[spec.name] = RegisteredPrompt(
                    spec=spec, version_id=await _ensure_version(session, spec)
                )
            await session.commit()
        return entries


async def _ensure_version(session: AsyncSession, spec: PromptSpec) -> UUID:
    prompt = (
        await session.execute(select(PromptRow).where(PromptRow.name == spec.name))
    ).scalar_one_or_none()
    if prompt is None:
        prompt = PromptRow(id=uuid7(), name=spec.name, description=spec.description)
        session.add(prompt)
        await session.flush()
    version = (
        await session.execute(
            select(PromptVersionRow)
            .where(PromptVersionRow.prompt_id == prompt.id)
            .where(PromptVersionRow.version == spec.version)
        )
    ).scalar_one_or_none()
    if version is None:
        version = PromptVersionRow(
            id=uuid7(),
            prompt_id=prompt.id,
            version=spec.version,
            template=spec.template,
            variables=list(spec.variables),
            content_hash=spec.content_hash,
        )
        session.add(version)
        await session.flush()
    elif version.content_hash != spec.content_hash:
        raise PromptVersionMutated(
            f"{spec.label}: the stored template differs from the code - "
            "register a new version instead of editing an immutable one"
        )
    return version.id
