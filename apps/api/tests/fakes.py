"""In-memory implementations of the persistence and file ports.

Used by application-layer unit tests (L1 of the pyramid): use cases run
against these with zero I/O. They honor the same contracts the SQL
adapters prove in integration tests — workspace scoping included — so a
use case green here and against Postgres is green for the same reasons.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from types import TracebackType
from uuid import UUID

from atlas.domain.ai import (
    ChatEvent,
    ChatRequest,
    ChatResponse,
    ProviderChatMessage,
    ProviderError,
    ProviderUnavailable,
    Usage,
)
from atlas.domain.conversation.entities import Citation, Conversation, Feedback, Message
from atlas.domain.conversation.ports import ResolvedCitation
from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.files import FileStat
from atlas.domain.knowledge.values import IngestionState
from atlas.domain.memory.entities import Memory, MemoryKind
from atlas.infrastructure.streams import BufferedEvent
from atlas.shared.errors import NotFound, ValidationFailed


@dataclass
class FakeState:
    """Shared backing store, surviving across UoW instances like a DB."""

    workspaces: set[UUID] = field(default_factory=set)
    sources: dict[UUID, Source] = field(default_factory=dict)
    documents: dict[UUID, Document] = field(default_factory=dict)
    versions: dict[UUID, DocumentVersion] = field(default_factory=dict)
    chunks: dict[UUID, Chunk] = field(default_factory=dict)
    jobs: dict[UUID, IngestionJob] = field(default_factory=dict)
    conversations: dict[UUID, Conversation] = field(default_factory=dict)
    messages: dict[UUID, Message] = field(default_factory=dict)
    citations: dict[UUID, Citation] = field(default_factory=dict)
    feedback: dict[UUID, Feedback] = field(default_factory=dict)
    memories: dict[UUID, Memory] = field(default_factory=dict)

    def workspace_of_source(self, source_id: UUID) -> UUID | None:
        source = self.sources.get(source_id)
        return source.workspace_id if source else None


@dataclass
class FakeSourceRepository:
    state: FakeState
    pending: dict[UUID, Source] = field(default_factory=dict)

    async def add(self, source: Source) -> None:
        self.pending[source.id] = replace(source)

    async def save(self, source: Source) -> None:
        stored = self.state.sources.get(source.id) or self.pending.get(source.id)
        if stored is None or stored.workspace_id != source.workspace_id:
            raise NotFound(f"source {source.id} not found in its workspace")
        self.pending[source.id] = replace(source)

    def _visible(self, workspace_id: UUID) -> list[Source]:
        merged = {**self.state.sources, **self.pending}
        return [s for s in merged.values() if s.workspace_id == workspace_id and not s.is_deleted]

    async def get(self, workspace_id: UUID, source_id: UUID) -> Source | None:
        return next((replace(s) for s in self._visible(workspace_id) if s.id == source_id), None)

    async def get_by_uri(self, workspace_id: UUID, uri: str) -> Source | None:
        return next((replace(s) for s in self._visible(workspace_id) if s.uri == uri), None)

    async def list_active(self, workspace_id: UUID) -> list[Source]:
        return [replace(s) for s in self._visible(workspace_id) if s.status == "active"]

    async def list_all(self, workspace_id: UUID) -> list[Source]:
        return [replace(s) for s in self._visible(workspace_id)]


@dataclass
class FakeDocumentRepository:
    state: FakeState
    pending: dict[UUID, Document] = field(default_factory=dict)

    async def add(self, document: Document) -> None:
        self.pending[document.id] = replace(document)

    async def save(self, document: Document) -> None:
        if document.id not in self.state.documents and document.id not in self.pending:
            raise NotFound(f"document {document.id} not found")
        self.pending[document.id] = replace(document)

    def _visible(self, workspace_id: UUID) -> list[Document]:
        merged = {**self.state.documents, **self.pending}
        return [
            d
            for d in merged.values()
            if self.state.workspace_of_source(d.source_id) == workspace_id and not d.is_deleted
        ]

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        return next((replace(d) for d in self._visible(workspace_id) if d.id == document_id), None)

    async def get_by_path(self, workspace_id: UUID, source_id: UUID, path: str) -> Document | None:
        return next(
            (
                replace(d)
                for d in self._visible(workspace_id)
                if d.source_id == source_id and d.path == path
            ),
            None,
        )

    async def list_for_source(self, workspace_id: UUID, source_id: UUID) -> list[Document]:
        return sorted(
            (replace(d) for d in self._visible(workspace_id) if d.source_id == source_id),
            key=lambda d: d.path,
        )


@dataclass
class FakeDocumentVersionRepository:
    state: FakeState
    documents: FakeDocumentRepository
    pending: dict[UUID, DocumentVersion] = field(default_factory=dict)

    async def add(self, version: DocumentVersion) -> None:
        self.pending[version.id] = version  # frozen dataclass: safe to share

    async def get_by_hash(
        self, workspace_id: UUID, document_id: UUID, content_hash: str
    ) -> DocumentVersion | None:
        return next(
            (
                v
                for v in self._visible(workspace_id)
                if v.document_id == document_id and str(v.content_hash) == content_hash
            ),
            None,
        )

    def _visible(self, workspace_id: UUID) -> list[DocumentVersion]:
        merged = {**self.state.versions, **self.pending}
        docs = {
            d.id
            for d in {**self.state.documents, **self.documents.pending}.values()
            if self.state.workspace_of_source(d.source_id) == workspace_id
        }
        return [v for v in merged.values() if v.document_id in docs]

    async def get(self, workspace_id: UUID, version_id: UUID) -> DocumentVersion | None:
        return next((v for v in self._visible(workspace_id) if v.id == version_id), None)

    async def list_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> list[DocumentVersion]:
        return sorted(
            (v for v in self._visible(workspace_id) if v.document_id == document_id),
            key=lambda v: v.created_at,
        )


@dataclass
class FakeChunkRepository:
    state: FakeState
    versions: FakeDocumentVersionRepository
    pending: dict[UUID, Chunk] = field(default_factory=dict)
    deleted: set[UUID] = field(default_factory=set)

    async def add_all(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            self.pending[chunk.id] = chunk

    def _merged(self) -> dict[UUID, Chunk]:
        merged = {**self.state.chunks, **self.pending}
        return {cid: c for cid, c in merged.items() if cid not in self.deleted}

    async def list_for_version(self, workspace_id: UUID, version_id: UUID) -> list[Chunk]:
        return sorted(
            (c for c in self._merged().values() if c.document_version_id == version_id),
            key=lambda c: c.ordinal,
        )

    async def count_for_version(self, workspace_id: UUID, version_id: UUID) -> int:
        return len(await self.list_for_version(workspace_id, version_id))

    async def count_embedded_for_version(self, workspace_id: UUID, version_id: UUID) -> int:
        chunks = await self.list_for_version(workspace_id, version_id)
        return sum(1 for c in chunks if c.is_embedded)

    async def find_embeddings(
        self, workspace_id: UUID, embedding_model: str, content_hashes: Sequence[str]
    ) -> dict[str, tuple[float, ...]]:
        wanted = set(content_hashes)
        found: dict[str, tuple[float, ...]] = {}
        for chunk in self._merged().values():
            if (
                chunk.embedding is not None
                and chunk.embedding_model == embedding_model
                and str(chunk.content_hash) in wanted
            ):
                found[str(chunk.content_hash)] = chunk.embedding
        return found

    async def save_embeddings(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            if chunk.embedding is None:
                raise NotFound(f"chunk {chunk.id} has no embedding to save")
            self.pending[chunk.id] = chunk

    async def delete_for_document_except(self, document_id: UUID, keep_version_id: UUID) -> None:
        version_ids = {
            v.id
            for v in {**self.state.versions, **self.versions.pending}.values()
            if v.document_id == document_id and v.id != keep_version_id
        }
        for chunk_id, chunk in list(self._merged().items()):
            if chunk.document_version_id in version_ids:
                self.pending.pop(chunk_id, None)
                self.deleted.add(chunk_id)  # applied to state at commit


@dataclass
class FakeIngestionJobRepository:
    state: FakeState
    pending: dict[UUID, IngestionJob] = field(default_factory=dict)

    def _merged(self) -> dict[UUID, IngestionJob]:
        return {**self.state.jobs, **self.pending}

    async def add(self, job: IngestionJob) -> None:
        self.pending[job.id] = replace(job)

    async def try_add(self, job: IngestionJob) -> bool:
        for existing in self._merged().values():
            if (
                existing.document_id == job.document_id
                and existing.content_hash == job.content_hash
                and existing.state in ("pending", "running")
            ):
                return False
        self.pending[job.id] = replace(job)
        return True

    async def save(self, job: IngestionJob) -> None:
        if job.id not in self._merged():
            raise NotFound(f"ingestion job {job.id} not found")
        self.pending[job.id] = replace(job)

    def _visible(self, workspace_id: UUID) -> list[IngestionJob]:
        return [
            j
            for j in self._merged().values()
            if self.state.workspace_of_source(j.source_id) == workspace_id
        ]

    async def get(self, workspace_id: UUID, job_id: UUID) -> IngestionJob | None:
        return next((replace(j) for j in self._visible(workspace_id) if j.id == job_id), None)

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
        matching = [
            replace(j)
            for j in self._visible(workspace_id)
            if (state is None or j.state == state)
            and (source_id is None or j.source_id == source_id)
            and (trace_id is None or j.trace_id == trace_id)
        ]
        return sorted(matching, key=lambda j: j.created_at, reverse=True)[:limit]


@dataclass
class FakeConversationRepository:
    state: FakeState
    pending: dict[UUID, Conversation] = field(default_factory=dict)

    def _merged(self) -> dict[UUID, Conversation]:
        return {**self.state.conversations, **self.pending}

    async def add(self, conversation: Conversation) -> None:
        self.pending[conversation.id] = replace(conversation)

    async def save(self, conversation: Conversation) -> None:
        stored = self._merged().get(conversation.id)
        if stored is None or stored.workspace_id != conversation.workspace_id:
            raise NotFound(f"conversation {conversation.id} not found in its workspace")
        self.pending[conversation.id] = replace(conversation)

    def _visible(self, workspace_id: UUID) -> list[Conversation]:
        return [
            c
            for c in self._merged().values()
            if c.workspace_id == workspace_id and not c.is_deleted
        ]

    async def get(self, workspace_id: UUID, conversation_id: UUID) -> Conversation | None:
        return next(
            (replace(c) for c in self._visible(workspace_id) if c.id == conversation_id), None
        )

    async def list_recent(self, workspace_id: UUID, *, limit: int = 50) -> list[Conversation]:
        ordered = sorted(
            (replace(c) for c in self._visible(workspace_id)),
            key=lambda c: c.updated_at,
            reverse=True,
        )
        return ordered[:limit]


@dataclass
class FakeMessageRepository:
    state: FakeState
    conversations: FakeConversationRepository
    pending: dict[UUID, Message] = field(default_factory=dict)

    def _merged(self) -> dict[UUID, Message]:
        return {**self.state.messages, **self.pending}

    def _workspace_of(self, conversation_id: UUID) -> UUID | None:
        conversation = {**self.state.conversations, **self.conversations.pending}.get(
            conversation_id
        )
        return conversation.workspace_id if conversation else None

    async def add(self, message: Message) -> None:
        self.pending[message.id] = message  # frozen: safe to share

    def _visible(self, workspace_id: UUID) -> list[Message]:
        return [
            m
            for m in self._merged().values()
            if self._workspace_of(m.conversation_id) == workspace_id
        ]

    async def get(self, workspace_id: UUID, message_id: UUID) -> Message | None:
        return next((m for m in self._visible(workspace_id) if m.id == message_id), None)

    async def list_for_conversation(
        self, workspace_id: UUID, conversation_id: UUID, *, limit: int = 500
    ) -> list[Message]:
        matching = sorted(
            (m for m in self._visible(workspace_id) if m.conversation_id == conversation_id),
            key=lambda m: m.id.int,  # UUIDv7 order = chronological
        )
        return matching[:limit]

    async def count_for_conversation(self, workspace_id: UUID, conversation_id: UUID) -> int:
        return len(await self.list_for_conversation(workspace_id, conversation_id, limit=10**9))

    async def latest_for_conversation(
        self, workspace_id: UUID, conversation_id: UUID
    ) -> Message | None:
        matching = await self.list_for_conversation(workspace_id, conversation_id, limit=10**9)
        return matching[-1] if matching else None


@dataclass
class FakeCitationRepository:
    state: FakeState
    messages: FakeMessageRepository
    pending: dict[UUID, Citation] = field(default_factory=dict)

    def _merged(self) -> dict[UUID, Citation]:
        return {**self.state.citations, **self.pending}

    async def add_all(self, citations: list[Citation]) -> None:
        for citation in citations:
            self.pending[citation.id] = citation  # frozen: safe to share

    async def list_for_message(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        message = await self.messages.get(workspace_id, message_id)
        if message is None:
            return []
        return sorted(
            (c for c in self._merged().values() if c.message_id == message_id),
            key=lambda c: c.marker,
        )

    async def list_resolved_for_message(
        self, workspace_id: UUID, message_id: UUID
    ) -> list[ResolvedCitation]:
        resolved: list[ResolvedCitation] = []
        for citation in await self.list_for_message(workspace_id, message_id):
            chunk = self.state.chunks.get(citation.chunk_id)
            document_id = None
            title = None
            if chunk is not None:
                version = self.state.versions.get(chunk.document_version_id)
                if version is not None:
                    document = self.state.documents.get(version.document_id)
                    if document is not None:
                        document_id = document.id
                        title = document.title
            resolved.append(
                ResolvedCitation(
                    marker=citation.marker,
                    chunk_id=citation.chunk_id,
                    document_id=document_id if document_id is not None else citation.chunk_id,
                    document_title=title,
                    snippet=(chunk.text[:200] if chunk is not None else ""),
                    score=citation.score,
                )
            )
        return resolved


@dataclass
class FakeFeedbackRepository:
    state: FakeState
    messages: FakeMessageRepository
    pending: dict[UUID, Feedback] = field(default_factory=dict)

    async def add(self, feedback: Feedback) -> None:
        self.pending[feedback.id] = feedback  # frozen: safe to share

    async def list_for_message(self, workspace_id: UUID, message_id: UUID) -> list[Feedback]:
        if await self.messages.get(workspace_id, message_id) is None:
            return []
        merged = {**self.state.feedback, **self.pending}
        return sorted(
            (f for f in merged.values() if f.message_id == message_id),
            key=lambda f: f.id.int,
        )


@dataclass
class FakeMemoryRepository:
    state: FakeState
    pending: dict[UUID, Memory] = field(default_factory=dict)

    def _merged(self) -> dict[UUID, Memory]:
        return {**self.state.memories, **self.pending}

    async def add(self, memory: Memory) -> None:
        self.pending[memory.id] = replace(memory)

    async def save(self, memory: Memory) -> None:
        stored = self._merged().get(memory.id)
        if stored is None or stored.workspace_id != memory.workspace_id:
            raise NotFound(f"memory {memory.id} not found in its workspace")
        self.pending[memory.id] = replace(memory)

    async def get(self, workspace_id: UUID, memory_id: UUID) -> Memory | None:
        return next(
            (
                replace(m)
                for m in self._merged().values()
                if m.workspace_id == workspace_id and m.id == memory_id and not m.is_deleted
            ),
            None,
        )

    async def list_active(
        self, workspace_id: UUID, *, kind: MemoryKind | None = None, limit: int = 200
    ) -> list[Memory]:
        live = [
            replace(m)
            for m in self._merged().values()
            if m.workspace_id == workspace_id
            and not m.is_deleted
            and not m.is_expired()
            and (kind is None or m.kind == kind)
        ]
        return sorted(live, key=lambda m: m.created_at, reverse=True)[:limit]


class FakeUnitOfWork:
    """Commit merges pending into shared state; exit without commit discards."""

    sources: FakeSourceRepository
    documents: FakeDocumentRepository
    document_versions: FakeDocumentVersionRepository
    chunks: FakeChunkRepository
    ingestion_jobs: FakeIngestionJobRepository
    conversations: FakeConversationRepository
    messages: FakeMessageRepository
    citations: FakeCitationRepository
    feedback: FakeFeedbackRepository
    memories: FakeMemoryRepository

    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.sources = FakeSourceRepository(self._state)
        self.documents = FakeDocumentRepository(self._state)
        self.document_versions = FakeDocumentVersionRepository(self._state, self.documents)
        self.chunks = FakeChunkRepository(self._state, self.document_versions)
        self.ingestion_jobs = FakeIngestionJobRepository(self._state)
        self.conversations = FakeConversationRepository(self._state)
        self.messages = FakeMessageRepository(self._state, self.conversations)
        self.citations = FakeCitationRepository(self._state, self.messages)
        self.feedback = FakeFeedbackRepository(self._state, self.messages)
        self.memories = FakeMemoryRepository(self._state)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self._state.sources.update(self.sources.pending)
        self._state.documents.update(self.documents.pending)
        self._state.versions.update(self.document_versions.pending)
        self._state.chunks.update(self.chunks.pending)
        for chunk_id in self.chunks.deleted:
            self._state.chunks.pop(chunk_id, None)
        self._state.jobs.update(self.ingestion_jobs.pending)
        self._state.conversations.update(self.conversations.pending)
        self._state.messages.update(self.messages.pending)
        self._state.citations.update(self.citations.pending)
        self._state.feedback.update(self.feedback.pending)
        self._state.memories.update(self.memories.pending)

    async def rollback(self) -> None:
        self.sources.pending.clear()
        self.documents.pending.clear()
        self.document_versions.pending.clear()
        self.chunks.pending.clear()
        self.chunks.deleted.clear()
        self.ingestion_jobs.pending.clear()
        self.conversations.pending.clear()
        self.messages.pending.clear()
        self.citations.pending.clear()
        self.feedback.pending.clear()
        self.memories.pending.clear()


class FakeFileStore:
    """Dict-backed SourceFileStore: {source_uri: {relative_path: bytes}}."""

    def __init__(self, trees: dict[str, dict[str, bytes]] | None = None) -> None:
        self.trees: dict[str, dict[str, bytes]] = trees or {}

    def exists(self, source_uri: str) -> bool:
        return source_uri in self.trees

    def scan(self, source_uri: str, ignore_globs: tuple[str, ...]) -> list[str]:
        return sorted(self.trees.get(source_uri, {}))

    def stat(self, source_uri: str, relative_path: str) -> FileStat | None:
        raw = self.trees.get(source_uri, {}).get(relative_path)
        if raw is None:
            return None
        return FileStat(hashlib.sha256(raw).hexdigest(), len(raw))

    def read(self, source_uri: str, relative_path: str) -> bytes | None:
        return self.trees.get(source_uri, {}).get(relative_path)

    def ensure_root(self, source_uri: str) -> None:
        self.trees.setdefault(source_uri, {})

    def write(self, source_uri: str, relative_path: str, data: bytes) -> None:
        if "/" in relative_path or "\\" in relative_path:
            raise ValidationFailed(f"refusing a non-flat path: {relative_path}")
        self.trees.setdefault(source_uri, {})[relative_path] = data


class FakeDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[tuple[UUID, UUID, str | None]] = []
        self.embed_dispatched: list[tuple[UUID, UUID, str | None]] = []
        self.summarize_dispatched: list[tuple[UUID, UUID, str | None]] = []
        self.title_dispatched: list[tuple[UUID, UUID, str | None]] = []

    def dispatch(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        self.dispatched.append((workspace_id, job_id, trace_id))

    def dispatch_embed(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        self.embed_dispatched.append((workspace_id, job_id, trace_id))

    def dispatch_summarize(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None:
        self.summarize_dispatched.append((workspace_id, conversation_id, trace_id))

    def dispatch_title(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None:
        self.title_dispatched.append((workspace_id, conversation_id, trace_id))


class FakeEmbeddingProvider:
    """Deterministic vectors derived from the text digest.

    ``calls`` records every batch verbatim — M05's cache-hit acceptance
    criterion ("zero embedding provider calls") is asserted against it.
    """

    def __init__(self, dimensions: int = 8, model: str = "fake-embed") -> None:
        self.dimensions = dimensions
        self._model = model
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def total_texts_embedded(self) -> int:
        return sum(len(batch) for batch in self.calls)

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple(digest[i % len(digest)] / 255.0 for i in range(self.dimensions))

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        self.calls.append([text])
        return self._vector(text)


def scripted_response(
    model: str,
    provider: str,
    content: str = "An answer.",
    *,
    input_tokens: int = 3812,
    output_tokens: int = 402,
) -> ChatResponse:
    return ChatResponse(
        message=ProviderChatMessage(role="assistant", content=content),
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason="end_turn",
        model=model,
        provider=provider,
    )


class ScriptedChatProvider:
    """Scripted completions and streams; records every wire request so
    tests can assert on the packed context. Stream scripts may contain
    `asyncio.Event` gates (the stream waits until the test sets them —
    how disconnect-detachment tests hold a stream open) and
    `ProviderError` items (raised mid-stream at that position)."""

    def __init__(self, name: str, *, is_local: bool) -> None:
        self._name = name
        self._is_local = is_local
        self.requests: list[tuple[str, ChatRequest]] = []
        self.responses: list[ChatResponse | ProviderError] = []
        self.streams: list[list[ChatEvent | ProviderError | asyncio.Event] | ProviderError] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return self._is_local

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse:
        self.requests.append((model, request))
        if not self.responses:
            raise ProviderUnavailable(f"{self._name}: no scripted response")
        item = self.responses.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return item

    def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        self.requests.append((model, request))
        script = self.streams.pop(0) if self.streams else ProviderUnavailable("no script")
        return self._play(script)

    async def _play(
        self, script: list[ChatEvent | ProviderError | asyncio.Event] | ProviderError
    ) -> AsyncIterator[ChatEvent]:
        if isinstance(script, ProviderError):
            raise script
        for item in script:
            if isinstance(item, asyncio.Event):
                await item.wait()
                continue
            if isinstance(item, ProviderError):
                raise item
            yield item


class MemoryStreamBuffer:
    """`StreamBuffer` without Redis, for unit tests: same replay/tail
    semantics, polling instead of blocking reads, no pings."""

    def __init__(self) -> None:
        self.events: dict[UUID, list[BufferedEvent]] = {}
        self.finished: set[UUID] = set()
        self.active: set[UUID] = set()
        self.idempotency: dict[str, UUID] = {}

    async def append(self, message_id: UUID, event: BufferedEvent) -> None:
        self.events.setdefault(message_id, []).append(event)

    async def finish(self, message_id: UUID) -> None:
        self.finished.add(message_id)

    async def exists(self, message_id: UUID) -> bool:
        return message_id in self.events

    async def read(
        self, message_id: UUID, *, after: int = -1
    ) -> AsyncIterator[BufferedEvent | None]:
        position = 0
        while True:
            buffered = self.events.get(message_id, [])
            while position < len(buffered):
                event = buffered[position]
                position += 1
                if event.index <= after:
                    continue
                yield event
                if event.terminal:
                    return
            if message_id in self.finished and position >= len(self.events.get(message_id, [])):
                return
            await asyncio.sleep(0.002)

    async def try_claim_conversation(self, conversation_id: UUID) -> bool:
        if conversation_id in self.active:
            return False
        self.active.add(conversation_id)
        return True

    async def release_conversation(self, conversation_id: UUID) -> None:
        self.active.discard(conversation_id)

    async def recall_message_for(self, idempotency_key: str) -> UUID | None:
        return self.idempotency.get(idempotency_key)

    async def remember_message_for(self, idempotency_key: str, message_id: UUID) -> None:
        self.idempotency.setdefault(idempotency_key, message_id)
