"""Persistence adapters: SQLAlchemy models, mappers, repositories, UoW.

The ONLY package (plus `alembic/`) that may know SQL, SQLAlchemy, or
pgvector details. Domain entities cross this boundary as plain objects;
ORM rows never leave (M03 feature: repositories expose entities, not rows).
"""
