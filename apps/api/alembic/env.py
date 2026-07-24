"""Alembic environment (async, docs/11 §5).

- URL: ATLAS_DATABASE_URL env var wins over alembic.ini.
- Autogenerate compares against the ORM metadata, EXCLUDING audit_events:
  that table is partitioned, append-only, migration-owned DDL that
  autogenerate cannot model. The drift gate (`alembic check` in the
  integration suite) relies on this filter being exact.
"""

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from atlas.infrastructure.persistence.tables import Base

config = context.config

_env_url = os.environ.get("ATLAS_DATABASE_URL")
if _env_url:
    config.set_main_option("sqlalchemy.url", _env_url)

target_metadata = Base.metadata

_MIGRATION_OWNED_TABLES = {"audit_events", "audit_events_default"}
_MIGRATION_OWNED_INDEXES = {
    "ingestion_jobs_active_dedupe_idx",
    "conversations_ws_idx",
    "memories_scope_idx",
    "citations_chunk_idx",
}


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if type_ == "table" and name in _MIGRATION_OWNED_TABLES:
        return False
    return not (type_ == "index" and name in _MIGRATION_OWNED_INDEXES)


def _configure(connection: Connection | None = None) -> None:
    context.configure(
        connection=connection,
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )


def run_migrations_offline() -> None:
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
