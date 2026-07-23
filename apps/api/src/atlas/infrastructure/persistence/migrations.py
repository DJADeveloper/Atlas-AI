"""Migration status and startup upgrade (docs/40 §2.2, docs/12 /ready).

The API process may run `alembic upgrade head` on startup (compose and
desktop targets set ATLAS_RUN_MIGRATIONS_ON_STARTUP); workers never do.
`/ready` reports `migrations: ok` only when the database's revision is
the script head — a version-skewed sidecar fails readiness instead of
serving against the wrong schema.
"""

import os
from functools import cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# migrations.py -> persistence -> infrastructure -> atlas -> src -> apps/api
_API_DIR = Path(__file__).resolve().parents[4]


def _alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(_API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_API_DIR / "alembic"))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url)
    return config


@cache
def head_revision() -> str:
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    if len(heads) != 1:
        msg = f"expected exactly one Alembic head, found {heads}"
        raise RuntimeError(msg)
    return heads[0]


def run_migrations_sync(database_url: str) -> None:
    """Upgrade to head. Sync (Alembic drives its own loop); call via
    asyncio.to_thread from async contexts."""
    previous = os.environ.get("ATLAS_DATABASE_URL")
    os.environ["ATLAS_DATABASE_URL"] = database_url
    try:
        command.upgrade(_alembic_config(database_url), "head")
    finally:
        if previous is None:
            os.environ.pop("ATLAS_DATABASE_URL", None)
        else:
            os.environ["ATLAS_DATABASE_URL"] = previous


async def database_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        return result.scalar_one_or_none()
