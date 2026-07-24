"""SQLAlchemy repositories implementing the knowledge-context ports.

Scoping contract (M03): every query is bounded by ``workspace_id`` in SQL —
directly on `sources`, via joins for everything that hangs off a source.
A wrong workspace id yields None/empty, never someone else's rows.
Soft-deleted sources and documents are invisible to reads (docs/11 §1.4).

Writes flush immediately: the Row classes deliberately define no ORM
relationships (entities carry ids, not object graphs), so SQLAlchemy's
unit-of-work cannot topologically sort inserts across tables — an
unflushed add of a version and its chunks could reach the database in
FK-violating order. Flushing in ``add``/``add_all`` pins statement order
to call order and makes new rows visible to later queries in the same
transaction. Commit remains the UoW's decision.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import CursorResult, Select, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.domain.conversation.entities import Citation, Conversation, Message
from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.values import IngestionState
from atlas.domain.memory.entities import Memory, MemoryKind
from atlas.infrastructure.persistence.mappers import (
    apply_conversation,
    apply_document,
    apply_job,
    apply_memory,
    apply_source,
    chunk_from_row,
    chunk_to_row,
    citation_from_row,
    citation_to_row,
    conversation_from_row,
    conversation_to_row,
    document_from_row,
    document_to_row,
    job_from_row,
    job_to_row,
    memory_from_row,
    memory_to_row,
    message_from_row,
    message_to_row,
    source_from_row,
    source_to_row,
    vector_from_row,
    version_from_row,
    version_to_row,
)
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
from atlas.shared.errors import Conflict, NotFound, ValidationFailed


def _scoped_documents(workspace_id: UUID) -> Select[tuple[DocumentRow]]:
    return (
        select(DocumentRow)
        .join(SourceRow, DocumentRow.source_id == SourceRow.id)
        .where(SourceRow.workspace_id == workspace_id)
        .where(DocumentRow.deleted_at.is_(None))
    )


def _scoped_versions(workspace_id: UUID) -> Select[tuple[DocumentVersionRow]]:
    return (
        select(DocumentVersionRow)
        .join(DocumentRow, DocumentVersionRow.document_id == DocumentRow.id)
        .join(SourceRow, DocumentRow.source_id == SourceRow.id)
        .where(SourceRow.workspace_id == workspace_id)
    )


class SqlSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, source: Source) -> None:
        self._session.add(source_to_row(source))
        try:
            await self._session.flush()
        except IntegrityError as error:
            # A soft-deleted source still holds UNIQUE(workspace_id, uri);
            # surface a clean Conflict instead of a 500. Restore-on-register
            # is M13 source-management scope.
            raise Conflict(f"a source already exists for {source.uri}") from error

    async def save(self, source: Source) -> None:
        row = await self._session.get(SourceRow, source.id)
        if row is None or row.workspace_id != source.workspace_id:
            raise NotFound(f"source {source.id} not found in its workspace")
        apply_source(row, source)

    async def get(self, workspace_id: UUID, source_id: UUID) -> Source | None:
        stmt = (
            select(SourceRow)
            .where(SourceRow.workspace_id == workspace_id)
            .where(SourceRow.id == source_id)
            .where(SourceRow.deleted_at.is_(None))
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return source_from_row(row) if row is not None else None

    async def get_by_uri(self, workspace_id: UUID, uri: str) -> Source | None:
        stmt = (
            select(SourceRow)
            .where(SourceRow.workspace_id == workspace_id)
            .where(SourceRow.uri == uri)
            .where(SourceRow.deleted_at.is_(None))
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return source_from_row(row) if row is not None else None

    async def list_active(self, workspace_id: UUID) -> list[Source]:
        stmt = (
            select(SourceRow)
            .where(SourceRow.workspace_id == workspace_id)
            .where(SourceRow.status == "active")
            .where(SourceRow.deleted_at.is_(None))
            .order_by(SourceRow.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [source_from_row(row) for row in rows]

    async def list_all(self, workspace_id: UUID) -> list[Source]:
        stmt = (
            select(SourceRow)
            .where(SourceRow.workspace_id == workspace_id)
            .where(SourceRow.deleted_at.is_(None))
            .order_by(SourceRow.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [source_from_row(row) for row in rows]


class SqlDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, document: Document) -> None:
        self._session.add(document_to_row(document))
        await self._session.flush()

    async def save(self, document: Document) -> None:
        row = await self._session.get(DocumentRow, document.id)
        if row is None:
            raise NotFound(f"document {document.id} not found")
        apply_document(row, document)

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        stmt = _scoped_documents(workspace_id).where(DocumentRow.id == document_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return document_from_row(row) if row is not None else None

    async def get_by_path(self, workspace_id: UUID, source_id: UUID, path: str) -> Document | None:
        stmt = (
            _scoped_documents(workspace_id)
            .where(DocumentRow.source_id == source_id)
            .where(DocumentRow.path == path)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return document_from_row(row) if row is not None else None

    async def list_for_source(self, workspace_id: UUID, source_id: UUID) -> list[Document]:
        stmt = (
            _scoped_documents(workspace_id)
            .where(DocumentRow.source_id == source_id)
            .order_by(DocumentRow.path)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [document_from_row(row) for row in rows]


class SqlDocumentVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: DocumentVersion) -> None:
        self._session.add(version_to_row(version))
        await self._session.flush()

    async def get(self, workspace_id: UUID, version_id: UUID) -> DocumentVersion | None:
        stmt = _scoped_versions(workspace_id).where(DocumentVersionRow.id == version_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return version_from_row(row) if row is not None else None

    async def get_by_hash(
        self, workspace_id: UUID, document_id: UUID, content_hash: str
    ) -> DocumentVersion | None:
        stmt = (
            _scoped_versions(workspace_id)
            .where(DocumentVersionRow.document_id == document_id)
            .where(DocumentVersionRow.content_hash == content_hash)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return version_from_row(row) if row is not None else None

    async def list_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> list[DocumentVersion]:
        stmt = (
            _scoped_versions(workspace_id)
            .where(DocumentVersionRow.document_id == document_id)
            .order_by(DocumentVersionRow.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [version_from_row(row) for row in rows]


class SqlChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_all(self, chunks: Sequence[Chunk]) -> None:
        self._session.add_all(chunk_to_row(chunk) for chunk in chunks)
        await self._session.flush()

    def _scoped(self, workspace_id: UUID, version_id: UUID) -> Select[tuple[ChunkRow]]:
        return (
            select(ChunkRow)
            .join(DocumentVersionRow, ChunkRow.document_version_id == DocumentVersionRow.id)
            .join(DocumentRow, DocumentVersionRow.document_id == DocumentRow.id)
            .join(SourceRow, DocumentRow.source_id == SourceRow.id)
            .where(SourceRow.workspace_id == workspace_id)
            .where(ChunkRow.document_version_id == version_id)
        )

    async def list_for_version(self, workspace_id: UUID, version_id: UUID) -> list[Chunk]:
        stmt = self._scoped(workspace_id, version_id).order_by(ChunkRow.ordinal)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [chunk_from_row(row) for row in rows]

    async def count_for_version(self, workspace_id: UUID, version_id: UUID) -> int:
        stmt = select(func.count()).select_from(self._scoped(workspace_id, version_id).subquery())
        return (await self._session.execute(stmt)).scalar_one()

    async def count_embedded_for_version(self, workspace_id: UUID, version_id: UUID) -> int:
        scoped = self._scoped(workspace_id, version_id).where(ChunkRow.embedding.is_not(None))
        stmt = select(func.count()).select_from(scoped.subquery())
        return (await self._session.execute(stmt)).scalar_one()

    async def find_embeddings(
        self, workspace_id: UUID, embedding_model: str, content_hashes: Sequence[str]
    ) -> dict[str, tuple[float, ...]]:
        if not content_hashes:
            return {}
        stmt = (
            select(ChunkRow.content_hash, ChunkRow.embedding)
            .join(DocumentVersionRow, ChunkRow.document_version_id == DocumentVersionRow.id)
            .join(DocumentRow, DocumentVersionRow.document_id == DocumentRow.id)
            .join(SourceRow, DocumentRow.source_id == SourceRow.id)
            .where(SourceRow.workspace_id == workspace_id)
            .where(ChunkRow.embedding_model == embedding_model)
            .where(ChunkRow.content_hash.in_(set(content_hashes)))
            .where(ChunkRow.embedding.is_not(None))
        )
        found: dict[str, tuple[float, ...]] = {}
        for content_hash, raw in (await self._session.execute(stmt)).all():
            vector = vector_from_row(raw)
            if vector is not None:  # guarded by the query; keeps types honest
                found[content_hash] = vector
        return found

    async def save_embeddings(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            if chunk.embedding is None:
                raise ValidationFailed(f"chunk {chunk.id} has no embedding to save")
            stmt = (
                update(ChunkRow)
                .where(ChunkRow.id == chunk.id)
                .values(
                    embedding=list(chunk.embedding),
                    embedding_model=chunk.embedding_model,
                )
            )
            await self._session.execute(stmt)

    async def delete_for_document_except(self, document_id: UUID, keep_version_id: UUID) -> None:
        versions = (
            select(DocumentVersionRow.id)
            .where(DocumentVersionRow.document_id == document_id)
            .where(DocumentVersionRow.id != keep_version_id)
        )
        stmt = delete(ChunkRow).where(ChunkRow.document_version_id.in_(versions))
        await self._session.execute(stmt)


class SqlIngestionJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: IngestionJob) -> None:
        self._session.add(job_to_row(job))
        await self._session.flush()

    async def try_add(self, job: IngestionJob) -> bool:
        row = job_to_row(job)
        statement = (
            pg_insert(IngestionJobRow)
            .values(
                id=row.id,
                source_id=row.source_id,
                document_id=row.document_id,
                content_hash=row.content_hash,
                state=row.state,
                attempts=row.attempts,
                error=row.error,
                trace_id=row.trace_id,
                started_at=row.started_at,
                finished_at=row.finished_at,
                created_at=row.created_at,
                updated_at=row.created_at,
            )
            .on_conflict_do_nothing()
        )
        result = await self._session.execute(statement)
        return isinstance(result, CursorResult) and bool(result.rowcount)

    async def save(self, job: IngestionJob) -> None:
        row = await self._session.get(IngestionJobRow, job.id)
        if row is None:
            raise NotFound(f"ingestion job {job.id} not found")
        apply_job(row, job)

    def _scoped(self, workspace_id: UUID) -> Select[tuple[IngestionJobRow]]:
        return (
            select(IngestionJobRow)
            .join(SourceRow, IngestionJobRow.source_id == SourceRow.id)
            .where(SourceRow.workspace_id == workspace_id)
        )

    async def get(self, workspace_id: UUID, job_id: UUID) -> IngestionJob | None:
        stmt = self._scoped(workspace_id).where(IngestionJobRow.id == job_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return job_from_row(row) if row is not None else None

    async def list_by_state(
        self, workspace_id: UUID, state: IngestionState, limit: int = 100
    ) -> list[IngestionJob]:
        return await self.list_jobs(workspace_id, state=state, limit=limit)

    async def list_jobs(
        self,
        workspace_id: UUID,
        *,
        state: IngestionState | None = None,
        source_id: UUID | None = None,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> list[IngestionJob]:
        stmt = self._scoped(workspace_id)
        if state is not None:
            stmt = stmt.where(IngestionJobRow.state == state)
        if source_id is not None:
            stmt = stmt.where(IngestionJobRow.source_id == source_id)
        if trace_id is not None:
            stmt = stmt.where(IngestionJobRow.trace_id == trace_id)
        stmt = stmt.order_by(IngestionJobRow.created_at.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [job_from_row(row) for row in rows]


class SqlConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, conversation: Conversation) -> None:
        self._session.add(conversation_to_row(conversation))
        await self._session.flush()

    async def save(self, conversation: Conversation) -> None:
        row = await self._session.get(ConversationRow, conversation.id)
        if row is None or row.workspace_id != conversation.workspace_id:
            raise NotFound(f"conversation {conversation.id} not found in its workspace")
        apply_conversation(row, conversation)

    def _scoped(self, workspace_id: UUID) -> Select[tuple[ConversationRow]]:
        return (
            select(ConversationRow)
            .where(ConversationRow.workspace_id == workspace_id)
            .where(ConversationRow.deleted_at.is_(None))
        )

    async def get(self, workspace_id: UUID, conversation_id: UUID) -> Conversation | None:
        stmt = self._scoped(workspace_id).where(ConversationRow.id == conversation_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return conversation_from_row(row) if row is not None else None

    async def list_recent(self, workspace_id: UUID, *, limit: int = 50) -> list[Conversation]:
        stmt = self._scoped(workspace_id).order_by(ConversationRow.updated_at.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [conversation_from_row(row) for row in rows]


class SqlMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: Message) -> None:
        self._session.add(message_to_row(message))
        await self._session.flush()

    def _scoped(self, workspace_id: UUID) -> Select[tuple[MessageRow]]:
        return (
            select(MessageRow)
            .join(ConversationRow, MessageRow.conversation_id == ConversationRow.id)
            .where(ConversationRow.workspace_id == workspace_id)
        )

    async def get(self, workspace_id: UUID, message_id: UUID) -> Message | None:
        stmt = self._scoped(workspace_id).where(MessageRow.id == message_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return message_from_row(row) if row is not None else None

    async def list_for_conversation(
        self, workspace_id: UUID, conversation_id: UUID, *, limit: int = 500
    ) -> list[Message]:
        stmt = (
            self._scoped(workspace_id)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.id)  # UUIDv7 PK order IS chronological order
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [message_from_row(row) for row in rows]

    async def count_for_conversation(self, workspace_id: UUID, conversation_id: UUID) -> int:
        scoped = self._scoped(workspace_id).where(MessageRow.conversation_id == conversation_id)
        stmt = select(func.count()).select_from(scoped.subquery())
        return (await self._session.execute(stmt)).scalar_one()

    async def latest_for_conversation(
        self, workspace_id: UUID, conversation_id: UUID
    ) -> Message | None:
        stmt = (
            self._scoped(workspace_id)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.id.desc())  # UUIDv7 max = newest
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return message_from_row(row) if row is not None else None


class SqlCitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_all(self, citations: list[Citation]) -> None:
        for citation in citations:
            self._session.add(citation_to_row(citation))
        await self._session.flush()

    async def list_for_message(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        stmt = (
            select(CitationRow)
            .join(MessageRow, CitationRow.message_id == MessageRow.id)
            .join(ConversationRow, MessageRow.conversation_id == ConversationRow.id)
            .where(ConversationRow.workspace_id == workspace_id)
            .where(CitationRow.message_id == message_id)
            .order_by(CitationRow.marker)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [citation_from_row(row) for row in rows]


class SqlMemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, memory: Memory) -> None:
        self._session.add(memory_to_row(memory))
        await self._session.flush()

    async def save(self, memory: Memory) -> None:
        row = await self._session.get(MemoryRow, memory.id)
        if row is None or row.workspace_id != memory.workspace_id:
            raise NotFound(f"memory {memory.id} not found in its workspace")
        apply_memory(row, memory)

    async def get(self, workspace_id: UUID, memory_id: UUID) -> Memory | None:
        stmt = (
            select(MemoryRow)
            .where(MemoryRow.workspace_id == workspace_id)
            .where(MemoryRow.id == memory_id)
            .where(MemoryRow.deleted_at.is_(None))
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return memory_from_row(row) if row is not None else None

    async def list_active(
        self, workspace_id: UUID, *, kind: MemoryKind | None = None, limit: int = 200
    ) -> list[Memory]:
        stmt = (
            select(MemoryRow)
            .where(MemoryRow.workspace_id == workspace_id)
            .where(MemoryRow.deleted_at.is_(None))
            .where(or_(MemoryRow.expires_at.is_(None), MemoryRow.expires_at > func.now()))
            .order_by(MemoryRow.created_at.desc())
            .limit(limit)
        )
        if kind is not None:
            stmt = stmt.where(MemoryRow.kind == kind)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [memory_from_row(row) for row in rows]
