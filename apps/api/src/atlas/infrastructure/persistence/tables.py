"""SQLAlchemy 2.0 table definitions (docs/11 DDL, model form).

Conventions (docs/11 §1): UUIDv7 PKs minted by the application, timestamptz
UTC set by this layer, text+CHECK enums mirroring the domain's canonical
value objects, fixed naming convention so autogenerate diffs stay stable.
`audit_events` is deliberately NOT modeled here: it is partitioned,
append-only, migration-owned DDL (see `alembic/versions/0001_baseline.py`)
and excluded from autogenerate comparison in `alembic/env.py`.
"""

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from atlas.domain.knowledge.values import INGESTION_STATES, SOURCE_KINDS, SOURCE_STATUSES
from atlas.shared.clock import utc_now

EMBEDDING_DIM = 768  # deployment-profile constant (spine §7)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        UUID: PGUUID(as_uuid=True),
        datetime: TIMESTAMP(timezone=True),
        dict[str, Any]: JSONB,
    }


class _Stamped:
    """created_at + updated_at, set by this layer from the shared clock."""

    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class _CreatedOnly:
    """Immutable tables carry created_at only (docs/11 §1.2)."""

    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class UserRow(_Stamped, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)


class WorkspaceRow(_Stamped, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(_in_clause("profile", ("hybrid", "local_only")), name="profile_allowed"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    profile: Mapped[str] = mapped_column(Text, server_default=sql_text("'hybrid'"))


class SettingRow(_Stamped, Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    key: Mapped[str] = mapped_column(Text)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)


class FeatureFlagRow(_Stamped, Base):
    __tablename__ = "feature_flags"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(Text, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class SourceRow(_Stamped, Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "uri"),
        CheckConstraint(_in_clause("kind", SOURCE_KINDS), name="kind_allowed"),
        CheckConstraint(_in_clause("status", SOURCE_STATUSES), name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    uri: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'active'"))
    last_indexed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DocumentRow(_Stamped, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source_id", "path"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    path: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "document_versions.id",
            use_alter=True,
            name="documents_current_version_fk",
        ),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DocumentVersionRow(_CreatedOnly, Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "content_hash"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    parser: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))


class ChunkRow(_CreatedOnly, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal"),
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("chunks_tsv_gin", "tsv", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
    )


class IngestionJobRow(_Stamped, Base):
    __tablename__ = "ingestion_jobs"
    # The partial unique index ingestion_jobs_active_dedupe_idx
    # (document_id, content_hash) WHERE state IN ('pending','running') is
    # migration-owned DDL (0002), excluded from autogenerate in env.py.
    __table_args__ = (
        CheckConstraint(_in_clause("state", INGESTION_STATES), name="state_allowed"),
        Index("ingestion_jobs_state_idx", "state", "created_at"),
        Index("ingestion_jobs_document_idx", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
