"""Entity ↔ row mappers.

The one mapping pattern for the codebase (M03 risk note): `*_to_row` for
inserts, `apply_*` to copy mutable state onto a loaded row for updates,
`*_from_row` to rebuild the domain entity. Rows never escape persistence.

Enumerated text columns are validated on the way OUT of the database and
narrowed to their domain Literal types — the CHECK constraints make a
mismatch impossible in practice, and the narrowing functions make it a
loud domain error rather than a silent lie if the two ever drift.
"""

from decimal import Decimal
from typing import Any

from atlas.domain.conversation.entities import (
    MESSAGE_ROLES,
    Citation,
    Conversation,
    Message,
    MessageRole,
)
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
from atlas.domain.memory.entities import MEMORY_KINDS, Memory, MemoryKind
from atlas.infrastructure.persistence.tables import (
    ChunkRow,
    CitationRow,
    ConversationRow,
    DocumentRow,
    DocumentVersionRow,
    IngestionJobRow,
    MemoryRow,
    MessageRow,
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


def vector_from_row(raw: Any) -> tuple[float, ...] | None:
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
        embedding=vector_from_row(row.embedding),
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


def _as_role(value: str) -> MessageRole:
    if value not in MESSAGE_ROLES:
        raise ValidationFailed(f"unknown message role in database: {value!r}")
    return value


def _as_memory_kind(value: str) -> MemoryKind:
    if value not in MEMORY_KINDS:
        raise ValidationFailed(f"unknown memory kind in database: {value!r}")
    return value


def conversation_to_row(entity: Conversation) -> ConversationRow:
    return ConversationRow(
        id=entity.id,
        workspace_id=entity.workspace_id,
        title=entity.title,
        summary=entity.summary,
        summary_through_message_id=entity.summary_through_message_id,
        deleted_at=entity.deleted_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def apply_conversation(row: ConversationRow, entity: Conversation) -> None:
    row.title = entity.title
    row.summary = entity.summary
    row.summary_through_message_id = entity.summary_through_message_id
    row.deleted_at = entity.deleted_at
    row.updated_at = entity.updated_at


def conversation_from_row(row: ConversationRow) -> Conversation:
    return Conversation(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        summary=row.summary,
        summary_through_message_id=row.summary_through_message_id,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def message_to_row(entity: Message) -> MessageRow:
    return MessageRow(
        id=entity.id,
        conversation_id=entity.conversation_id,
        role=entity.role,
        content=entity.content,
        abstained=entity.abstained,
        model=entity.model,
        provider=entity.provider,
        prompt_version_id=entity.prompt_version_id,
        input_tokens=entity.input_tokens,
        output_tokens=entity.output_tokens,
        cost_usd=None if entity.cost_usd is None else Decimal(str(entity.cost_usd)),
        latency_ms=entity.latency_ms,
        trace_id=entity.trace_id,
        created_at=entity.created_at,
    )


def message_from_row(row: MessageRow) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=_as_role(row.role),
        content=row.content,
        abstained=row.abstained,
        model=row.model,
        provider=row.provider,
        prompt_version_id=row.prompt_version_id,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=None if row.cost_usd is None else float(row.cost_usd),
        latency_ms=row.latency_ms,
        trace_id=row.trace_id,
        created_at=row.created_at,
    )


def memory_to_row(entity: Memory) -> MemoryRow:
    return MemoryRow(
        id=entity.id,
        workspace_id=entity.workspace_id,
        kind=entity.kind,
        content=entity.content,
        source_message_id=entity.source_message_id,
        confidence=entity.confidence,
        expires_at=entity.expires_at,
        deleted_at=entity.deleted_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def apply_memory(row: MemoryRow, entity: Memory) -> None:
    row.content = entity.content
    row.confidence = entity.confidence
    row.expires_at = entity.expires_at
    row.deleted_at = entity.deleted_at
    row.updated_at = entity.updated_at


def memory_from_row(row: MemoryRow) -> Memory:
    return Memory(
        id=row.id,
        workspace_id=row.workspace_id,
        kind=_as_memory_kind(row.kind),
        content=row.content,
        source_message_id=row.source_message_id,
        confidence=row.confidence,
        expires_at=row.expires_at,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def citation_to_row(entity: Citation) -> CitationRow:
    return CitationRow(
        id=entity.id,
        message_id=entity.message_id,
        chunk_id=entity.chunk_id,
        marker=entity.marker,
        score=entity.score,
        created_at=entity.created_at,
    )


def citation_from_row(row: CitationRow) -> Citation:
    return Citation(
        id=row.id,
        message_id=row.message_id,
        chunk_id=row.chunk_id,
        marker=row.marker,
        score=row.score,
        created_at=row.created_at,
    )
