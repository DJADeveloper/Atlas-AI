"""Baseline: identity, configuration, knowledge, and audit tables.

Hand-written from the ORM metadata (docs/11 §5): the vector column, HNSW
options, generated tsvector, partition DDL, and the append-only trigger
are invisible to autogenerate. The drift gate in the integration suite
(`alembic check` against these models) proves migration/model equivalence
on every PR. Constraint names follow the metadata naming convention so
that comparison stays clean. Fully reversible.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = TIMESTAMP(timezone=True)
_UUID = PGUUID(as_uuid=True)


def _stamped() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        *_stamped(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "workspaces",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("profile", sa.Text(), server_default=sa.text("'hybrid'"), nullable=False),
        *_stamped(),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_workspaces_owner_user_id_users"
        ),
        sa.CheckConstraint(
            "profile IN ('hybrid', 'local_only')", name="ck_workspaces_profile_allowed"
        ),
    )

    op.create_table(
        "settings",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", JSONB(), nullable=False),
        *_stamped(),
        sa.PrimaryKeyConstraint("id", name="pk_settings"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_settings_workspace_id_workspaces"
        ),
        sa.UniqueConstraint("workspace_id", "key", name="uq_settings_workspace_id_key"),
    )

    op.create_table(
        "feature_flags",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        *_stamped(),
        sa.PrimaryKeyConstraint("id", name="pk_feature_flags"),
        sa.UniqueConstraint("key", name="uq_feature_flags_key"),
    )

    op.create_table(
        "sources",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("config", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_indexed_at", _TS, nullable=True),
        sa.Column("deleted_at", _TS, nullable=True),
        *_stamped(),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_sources_workspace_id_workspaces"
        ),
        sa.UniqueConstraint("workspace_id", "uri", name="uq_sources_workspace_id_uri"),
        sa.CheckConstraint(
            "kind IN ('folder', 'git', 'drive', 'notion', 'slack', 'email')",
            name="ck_sources_kind_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'error')", name="ck_sources_status_allowed"
        ),
    )

    op.create_table(
        "documents",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("source_id", _UUID, nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("current_version_id", _UUID, nullable=True),
        sa.Column("deleted_at", _TS, nullable=True),
        *_stamped(),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_documents_source_id_sources"
        ),
        sa.UniqueConstraint("source_id", "path", name="uq_documents_source_id_path"),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("document_id", _UUID, nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("parser", sa.Text(), nullable=False),
        sa.Column("meta", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id", "content_hash", name="uq_document_versions_document_id_content_hash"
        ),
    )

    # The one circular reference in the schema (docs/11 §2): resolved by
    # adding the FK after both tables exist.
    op.create_foreign_key(
        "documents_current_version_fk",
        "documents",
        "document_versions",
        ["current_version_id"],
        ["id"],
    )

    op.create_table(
        "chunks",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("document_version_id", _UUID, nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("heading_path", ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("meta", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_chunks_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_version_id", "ordinal", name="uq_chunks_document_version_id_ordinal"
        ),
    )
    op.create_index(
        "chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("chunks_tsv_gin", "chunks", ["tsv"], postgresql_using="gin")

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("source_id", _UUID, nullable=False),
        sa.Column("document_id", _UUID, nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("started_at", _TS, nullable=True),
        sa.Column("finished_at", _TS, nullable=True),
        *_stamped(),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_ingestion_jobs_source_id_sources"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_ingestion_jobs_document_id_documents"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed', 'skipped')",
            name="ck_ingestion_jobs_state_allowed",
        ),
    )
    op.create_index("ingestion_jobs_state_idx", "ingestion_jobs", ["state", "created_at"])
    op.create_index("ingestion_jobs_document_idx", "ingestion_jobs", ["document_id"])

    # audit_events: partitioned, append-only, no FKs by design (docs/11 §2.6).
    # Migration-owned DDL — excluded from autogenerate in env.py. The DEFAULT
    # partition guarantees writes never fail on an unrolled month; monthly
    # partitions are runtime maintenance (M04+), keeping this migration
    # deterministic (no date arithmetic at migration time).
    op.execute(
        """
        CREATE TABLE audit_events (
          id uuid NOT NULL,
          workspace_id uuid NOT NULL,
          actor text NOT NULL,
          event_type text NOT NULL,
          subject_type text NOT NULL,
          subject_id uuid,
          payload jsonb NOT NULL DEFAULT '{}'::jsonb,
          trace_id text,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE TABLE audit_events_default PARTITION OF audit_events DEFAULT")
    op.execute("CREATE INDEX audit_events_ws_time_idx ON audit_events (workspace_id, created_at)")
    op.execute("CREATE INDEX audit_events_type_idx ON audit_events (event_type, created_at)")
    op.execute(
        """
        CREATE FUNCTION atlas_audit_events_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_immutable
          BEFORE UPDATE OR DELETE ON audit_events
          FOR EACH ROW EXECUTE FUNCTION atlas_audit_events_immutable()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS atlas_audit_events_immutable()")
    op.execute("DROP TABLE IF EXISTS audit_events")  # partitions drop with the parent
    op.drop_table("ingestion_jobs")
    op.drop_index("chunks_tsv_gin", table_name="chunks")
    op.drop_index("chunks_embedding_hnsw", table_name="chunks")
    op.drop_table("chunks")
    op.drop_constraint("documents_current_version_fk", "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("sources")
    op.drop_table("feature_flags")
    op.drop_table("settings")
    op.drop_table("workspaces")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
