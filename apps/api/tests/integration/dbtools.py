"""Helpers for database integration tests: fresh DBs and Alembic runs."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

API_DIR = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@contextmanager
def alembic_env(database_url: str) -> Iterator[Config]:
    """Pin ATLAS_DATABASE_URL for the duration of an Alembic command —
    env.py gives the env var precedence, so tests must own it."""
    previous = os.environ.get("ATLAS_DATABASE_URL")
    os.environ["ATLAS_DATABASE_URL"] = database_url
    try:
        yield alembic_config(database_url)
    finally:
        if previous is None:
            os.environ.pop("ATLAS_DATABASE_URL", None)
        else:
            os.environ["ATLAS_DATABASE_URL"] = previous


async def create_fresh_database(admin_url: str) -> str:
    """CREATE DATABASE with a unique name; returns its URL."""
    name = f"atlas_test_{uuid4().hex[:12]}"
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()
    return admin_url.rsplit("/", 1)[0] + f"/{name}"


async def list_public_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            return {row[0] for row in rows}
    finally:
        await engine.dispose()
