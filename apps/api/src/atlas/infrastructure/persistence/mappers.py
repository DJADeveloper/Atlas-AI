"""Entity ↔ row mappers.

The one mapping pattern for the codebase (M03 risk note): `*_to_row` for
inserts, `apply_*` to copy mutable state onto a loaded row for updates,
`*_from_row` to rebuild the domain entity. Rows never escape persistence.

Enumerated text columns are validated on the way OUT of the database and
narrowed to their domain Literal types — the CHECK constraints make a
mismatch impossible in practice, and the narrowing functions make it a
loud domain error rather than a silent lie if the two ever drift.
"""

from typing import Any

from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.values import (
    INGESTION_STAGES,
    INGESTION_STATES,
    SOURCE_KINDS,
    SOURCE_STATUSES,
    ContentHash,
    IngestionStage,
    IngestionState,
    SourceKind,
    SourceStatus,
)
from atlas.infrastructure.persistence.tables import (
    ChunkRow,
    DocumentRow,
    DocumentVersionRow,
    IngestionJobRow,
    SourceRow,
)
from atlas.shared.errors import ValidationFailed


def _as_kind(value: str) -> SourceKind:
    if value not in SOURCE_KINDS:
        raise ValidationFailed(f"unknown source kind in database: {value!r}")
    return value


def _as_status(value: str) -> SourceStatus:
    if value not in SOURCE_STATUSES:
        raise ValidationFailed(f"unknown source status in database: {value!r}")
    return value


def _as_state(value: str) -> IngestionState:
    if value not in INGESTION_STATES:
        raise ValidationFailed(f"unknown ingestion state in database: {value!r}")
    return value


def _as_stage(value: str) -> IngestionStage:
    if value not in INGESTION_STAGES:
        raise ValidationFailed(f"unknown ingestion stage in database: {value!r}")
    return value


def source_to_row(entity: Source) -> SourceRow:
    return SourceRow(
        id=entity.id,
        workspace_id=entity.workspace_id,
        kind=entity.kind,
        name=entity.name,
        uri=entity.uri,
        config=dict(entity.config),
        status=entity.status,
        last_indexed_at=entity.last_indexed_at,
        deleted_at=entity.deleted_at,
        created_at=entity.created_at,
    )


def apply_source(row: SourceRow, entity: Source) -> None:
    row.name = entity.name
    row.uri = entity.uri
    row.config = dict(entity.config)
    row.status = entity.status
    row.last_indexed_at = entity.last_indexed_at
    row.deleted_at = entity.deleted_at


def source_from_row(row: SourceRow) -> Source:
    return Source(
        id=row.id,
        workspace_id=row.workspace_id,
        kind=_as_kind(row.kind),
        name=row.name,
        uri=row.uri,
        config=dict(row.config),
        status=_as_status(row.status),
        last_indexed_at=row.last_indexed_at,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


def document_to_row(entity: Document) -> DocumentRow:
    return DocumentRow(
        id=entity.id,
        source_id=entity.source_id,
        path=entity.path,
        title=entity.title,
        mime_type=entity.mime_type,
        current_version_id=entity.current_version_id,
        deleted_at=entity.deleted_at,
        created_at=entity.created_at,
    )


def apply_document(row: DocumentRow, entity: Document) -> None:
    row.title = entity.title
    row.mime_type = entity.mime_type
    row.current_version_id = entity.current_version_id
    row.deleted_at = entity.deleted_at


def document_from_row(row: DocumentRow) -> Document:
    return Document(
        id=row.id,
        source_id=row.source_id,
        path=row.path,
        title=row.title,
        mime_type=row.mime_type,
        current_version_id=row.current_version_id,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


def version_to_row(entity: DocumentVersion) -> DocumentVersionRow:
    return DocumentVersionRow(
        id=entity.id,
        document_id=entity.document_id,
        content_hash=str(entity.content_hash),
        size_bytes=entity.size_bytes,
        parser=entity.parser,
        meta=dict(entity.meta),
        created_at=entity.created_at,
    )


def version_from_row(row: DocumentVersionRow) -> DocumentVersion:
    return DocumentVersion(
        id=row.id,
        document_id=row.document_id,
        content_hash=ContentHash(row.content_hash),
        size_bytes=row.size_bytes,
        parser=row.parser,
        meta=dict(row.meta),
        created_at=row.created_at,
    )


def chunk_to_row(entity: Chunk) -> ChunkRow:
    return ChunkRow(
        id=entity.id,
        document_version_id=entity.document_version_id,
        ordinal=entity.ordinal,
        text=entity.text,
        token_count=entity.token_count,
        content_hash=str(entity.content_hash),
        heading_path=list(entity.heading_path),
        meta=dict(entity.meta),
        embedding=None if entity.embedding is None else list(entity.embedding),
        embedding_model=entity.embedding_model,
        created_at=entity.created_at,
    )


def _vector_from_row(raw: Any) -> tuple[float, ...] | None:
    """pgvector returns numpy-like arrays or lists depending on driver path."""
    if raw is None:
        return None
    values = raw.to_list() if hasattr(raw, "to_list") else raw
    return tuple(float(component) for component in values)


def chunk_from_row(row: ChunkRow) -> Chunk:
    return Chunk(
        id=row.id,
        document_version_id=row.document_version_id,
        ordinal=row.ordinal,
        text=row.text,
        token_count=row.token_count,
        content_hash=ContentHash(row.content_hash),
        heading_path=tuple(row.heading_path),
        meta=dict(row.meta),
        embedding=_vector_from_row(row.embedding),
        embedding_model=row.embedding_model,
        created_at=row.created_at,
    )


def job_to_row(entity: IngestionJob) -> IngestionJobRow:
    return IngestionJobRow(
        id=entity.id,
        source_id=entity.source_id,
        document_id=entity.document_id,
        content_hash=entity.content_hash,
        state=entity.state,
        stage=entity.stage,
        attempts=entity.attempts,
        error=entity.error,
        trace_id=entity.trace_id,
        started_at=entity.started_at,
        finished_at=entity.finished_at,
        created_at=entity.created_at,
    )


def apply_job(row: IngestionJobRow, entity: IngestionJob) -> None:
    row.state = entity.state
    row.stage = entity.stage
    row.attempts = entity.attempts
    row.error = entity.error
    row.trace_id = entity.trace_id
    row.started_at = entity.started_at
    row.finished_at = entity.finished_at


def job_from_row(row: IngestionJobRow) -> IngestionJob:
    return IngestionJob(
        id=row.id,
        source_id=row.source_id,
        document_id=row.document_id,
        content_hash=row.content_hash,
        state=_as_state(row.state),
        stage=_as_stage(row.stage),
        attempts=row.attempts,
        error=row.error,
        trace_id=row.trace_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )
