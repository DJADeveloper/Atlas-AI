"""Shared integration fixtures: one pgvector Postgres for the session,
fresh databases per test where isolation matters."""

import asyncio
from collections.abc import Iterator

import pytest
from alembic import command
from testcontainers.postgres import PostgresContainer

from tests.integration.dbtools import alembic_env, create_fresh_database

POSTGRES_IMAGE = "pgvector/pgvector:pg16"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        yield container


@pytest.fixture
def fresh_database_url(postgres_container: PostgresContainer) -> str:
    """An empty, never-touched database on the shared server."""
    admin_url = postgres_container.get_connection_url()
    return asyncio.run(create_fresh_database(admin_url))


@pytest.fixture
def migrated_database_url(fresh_database_url: str) -> str:
    """A fresh database migrated to head."""
    with alembic_env(fresh_database_url) as config:
        command.upgrade(config, "head")
    return fresh_database_url
