"""SQLAlchemy 2.0 table definitions (docs/11 DDL, model form).

Conventions (docs/11 §1): UUIDv7 PKs minted by the application, timestamptz
UTC set by this layer, text+CHECK enums mirroring the domain's canonical
value objects, fixed naming convention so autogenerate diffs stay stable.
`audit_events` is deliberately NOT modeled here: it is partitioned,
append-only, migration-owned DDL (see `alembic/versions/0001_baseline.py`)
and excluded from autogenerate comparison in `alembic/env.py`.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from atlas.domain.conversation.entities import MESSAGE_ROLES
from atlas.domain.knowledge.values import (
    INGESTION_STAGES,
    INGESTION_STATES,
    SOURCE_KINDS,
    SOURCE_STATUSES,
)
from atlas.domain.memory.entities import MEMORY_KINDS
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
    # embedding/embedding_model are nullable as a pair (M05): the chunk
    # stage inserts staged rows, the embed stage completes them; only
    # fully embedded sets become a document's current version.
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal"),
        CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)",
            name="embedding_paired",
        ),
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("chunks_tsv_gin", "tsv", postgresql_using="gin"),
        # Embedding-cache lookup path (M05): vectors are reused by
        # (embedding_model, content_hash) — Postgres IS the cache.
        Index("chunks_embedding_cache", "embedding_model", "content_hash"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(Text)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
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
        CheckConstraint(_in_clause("stage", INGESTION_STAGES), name="stage_allowed"),
        Index("ingestion_jobs_state_idx", "state", "created_at"),
        Index("ingestion_jobs_document_idx", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    stage: Mapped[str] = mapped_column(Text, server_default=sql_text("'parse'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ConversationRow(_Stamped, Base):
    """The partial recency index conversations_ws_idx (workspace_id,
    updated_at DESC WHERE deleted_at IS NULL) is migration-owned DDL
    (0004, same reasoning as 0002's partial index) — excluded from
    autogenerate in env.py."""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Watermark carries no FK (0004): messages already FK-reference
    # conversations, and the pair would be circular for a mere cursor.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_through_message_id: Mapped[UUID | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MessageRow(_CreatedOnly, Base):
    """Immutable; UUIDv7 PK order = chronological order (docs/11 §2.4).
    prompt_version_id points into the M08 prompt registry."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_in_clause("role", MESSAGE_ROLES), name="role_allowed"),
        Index("messages_conversation_idx", "conversation_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    abstained: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class MemoryRow(_Stamped, Base):
    """Memory v1 (docs/11 §2.5); project_id joins at M13. Provenance is
    SET NULL so deleting a conversation never strands its memories."""

    __tablename__ = "memories"
    # memories_scope_idx (partial, WHERE deleted_at IS NULL) is
    # migration-owned DDL (0004), excluded from autogenerate in env.py.
    __table_args__ = (CheckConstraint(_in_clause("kind", MEMORY_KINDS), name="kind_allowed"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    kind: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PromptRow(_Stamped, Base):
    __tablename__ = "prompts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class PromptVersionRow(_CreatedOnly, Base):
    """Immutable: a version's template never changes (docs/11 §5);
    content_hash is the drift guard the registry checks on load."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("prompt_id", "version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    prompt_id: Mapped[UUID] = mapped_column(ForeignKey("prompts.id"))
    version: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(Text)
    variables: Mapped[list[str]] = mapped_column(JSONB, default=list)
    content_hash: Mapped[str] = mapped_column(Text)


class CitationRow(_CreatedOnly, Base):
    """Immutable; chunk_id has NO cascade — deleting cited evidence
    must fail loudly (the docs/11 §4 retention interlock)."""

    __tablename__ = "citations"
    __table_args__ = (UniqueConstraint("message_id", "marker"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunks.id"))
    marker: Mapped[int] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
