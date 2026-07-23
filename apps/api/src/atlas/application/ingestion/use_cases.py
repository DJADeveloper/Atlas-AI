"""Ingestion use cases (docs/21 pipeline; M05: parse → chunk → embed → index).

Transaction discipline: each use case runs one Unit of Work and
dispatches to the queue only AFTER commit, so workers never race an
uncommitted row. The SHA-256 hash gate makes every step idempotent:
unchanged content becomes a `skipped` job, never a new version.

Stage layout (docs/21 §3, realized as two tasks): `IngestDocument`
covers parse+chunk — both recompute deterministically from file bytes —
and commits the version plus its staged (unembedded) chunk rows, the
persisted intermediate every later stage resumes from. `EmbedDocument`
covers embed+index: cache-first vectors, then the atomic swap (flip the
current pointer, delete other generations' chunks, one transaction). A
crash anywhere leaves the old index serving; a duplicate delivery finds
committed state and no-ops forward.
"""

import hashlib
import mimetypes
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from atlas.application.ports import EmbeddingProvider, IngestionDispatcher, UnitOfWork
from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.files import SourceFileStore
from atlas.domain.knowledge.parsing import Block, DocumentParser, ParsedDocument, ParseFailed
from atlas.domain.knowledge.values import ContentHash, SourceKind
from atlas.rag import chunk_blocks, embedded_text
from atlas.shared.errors import Conflict, NotFound, ValidationFailed

# Default ignore globs (docs/21 §watching; M04 risk: event storms).
DEFAULT_IGNORE_GLOBS: tuple[str, ...] = (
    ".git/**",
    "**/.git/**",
    "node_modules/**",
    "**/node_modules/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".venv/**",
    "**/.venv/**",
    ".DS_Store",
    "**/.DS_Store",
)

# Exponential backoff per attempt (docs/21: 30 s / 2 m / 10 m).
RETRY_DELAYS_SECONDS: tuple[int, ...] = (30, 120, 600)

IngestResult = Literal["succeeded", "chunked", "skipped", "retry_scheduled", "dead_lettered"]


@dataclass(frozen=True, slots=True)
class DetectReport:
    """What a change-detection sweep did."""

    batch_id: str
    enqueued_job_ids: tuple[UUID, ...] = ()
    skipped_unchanged: int = 0
    deduplicated: int = 0


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    result: IngestResult
    retry_delay_seconds: int | None = None
    detail: str | None = None


def _ignore_globs(source: Source) -> tuple[str, ...]:
    extra = source.config.get("ignore_globs", [])
    extra_globs = tuple(str(glob) for glob in extra) if isinstance(extra, list) else ()
    return DEFAULT_IGNORE_GLOBS + extra_globs


@dataclass(frozen=True)
class DetectChanges:
    """Hash-gate a set of paths (or a full scan) into ingestion jobs."""

    uow_factory: Callable[[], UnitOfWork]
    file_store: SourceFileStore
    dispatcher: IngestionDispatcher
    supports: Callable[[str], bool]

    async def execute(
        self,
        workspace_id: UUID,
        source_id: UUID,
        *,
        paths: Sequence[str] | None = None,
        batch_id: str,
    ) -> DetectReport:
        enqueued: list[UUID] = []
        skipped = 0
        deduplicated = 0
        async with self.uow_factory() as uow:
            source = await uow.sources.get(workspace_id, source_id)
            if source is None:
                raise NotFound(f"source {source_id} not found")

            candidates = (
                list(paths)
                if paths is not None
                else self.file_store.scan(source.uri, _ignore_globs(source))
            )
            for path in sorted(set(candidates)):
                if not self.supports(path):
                    continue
                stat = self.file_store.stat(source.uri, path)
                if stat is None:
                    continue  # vanished between event and sweep

                document = await uow.documents.get_by_path(workspace_id, source.id, path)
                if document is None:
                    document = Document(source_id=source.id, path=path)
                    await uow.documents.add(document)

                job = IngestionJob(
                    source_id=source.id,
                    document_id=document.id,
                    content_hash=stat.content_hash,
                    trace_id=batch_id,
                )
                if await self._fully_indexed(uow, workspace_id, document, stat.content_hash):
                    job.skip("unchanged content")
                    await uow.ingestion_jobs.add(job)
                    skipped += 1
                elif await uow.ingestion_jobs.try_add(job):
                    enqueued.append(job.id)
                else:
                    deduplicated += 1
            await uow.commit()

        for job_id in enqueued:  # only after commit
            self.dispatcher.dispatch(workspace_id, job_id, batch_id)
        return DetectReport(
            batch_id=batch_id,
            enqueued_job_ids=tuple(enqueued),
            skipped_unchanged=skipped,
            deduplicated=deduplicated,
        )

    async def _fully_indexed(
        self, uow: UnitOfWork, workspace_id: UUID, document: Document, content_hash: str
    ) -> bool:
        """Unchanged bytes skip only when the current version actually
        carries embedded chunks — a hash match against an M04-era (or
        crash-orphaned) version without an index re-enters the pipeline
        instead of hiding behind the gate (M05 backfill)."""
        if document.current_version_id is None:
            return False
        version = await uow.document_versions.get(workspace_id, document.current_version_id)
        if version is None or str(version.content_hash) != content_hash:
            return False
        embedded = await uow.chunks.count_embedded_for_version(workspace_id, version.id)
        return embedded > 0


def _record_failure(job: IngestionJob, error: Exception) -> IngestOutcome:
    """Shared failure ladder (M04): fail → retry with backoff → dead-letter."""
    reason = (
        f"{error.context.get('reason')}: {error.detail}"
        if isinstance(error, ParseFailed)
        else f"{type(error).__name__}: {error}"
    )
    job.fail(reason[:2000])
    if job.can_retry:
        delay = RETRY_DELAYS_SECONDS[min(job.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        job.retry()
        return IngestOutcome("retry_scheduled", retry_delay_seconds=delay, detail=reason)
    return IngestOutcome("dead_lettered", detail=reason)


def _stage_chunks(version_id: UUID, parsed: ParsedDocument) -> list[Chunk]:
    """Chunk the parse IR into staged (unembedded) rows for the version."""
    blocks = parsed.blocks or (Block(kind="paragraph", text=parsed.text),)
    chunks: list[Chunk] = []
    for ordinal, draft in enumerate(chunk_blocks(blocks, title=parsed.title)):
        meta: dict[str, object] = {"breadcrumb": draft.breadcrumb}
        if draft.page_start is not None:
            meta["page_start"] = draft.page_start
            meta["page_end"] = draft.page_end
        chunks.append(
            Chunk(
                document_version_id=version_id,
                ordinal=ordinal,
                text=draft.text,
                token_count=draft.token_count,
                content_hash=ContentHash(draft.content_hash),
                heading_path=draft.heading_path,
                meta=meta,
            )
        )
    return chunks


@dataclass(frozen=True)
class IngestDocument:
    """Parse+chunk stages: read → hash → parse → version + staged chunks.

    Commits before handing the job to the embed queue, so the staged
    rows ARE the persisted intermediate — an Ollama outage retries embed
    without ever re-parsing a 200-page PDF (docs/21 §3)."""

    uow_factory: Callable[[], UnitOfWork]
    file_store: SourceFileStore
    parser_for: Callable[[str], DocumentParser]
    dispatcher: IngestionDispatcher

    async def execute(self, workspace_id: UUID, job_id: UUID) -> IngestOutcome:
        async with self.uow_factory() as uow:
            job = await uow.ingestion_jobs.get(workspace_id, job_id)
            if job is None:
                raise NotFound(f"ingestion job {job_id} not found")
            if job.state in ("succeeded", "skipped"):
                return IngestOutcome("skipped", detail="already terminal")
            if job.document_id is None:
                raise ValidationFailed("document-less jobs are not supported at M05")

            if job.state == "running" and job.stage in ("embed", "index"):
                # Duplicate parse delivery while embed owns the job:
                # nothing to redo here, just make sure embed gets poked.
                outcome = IngestOutcome("chunked", detail="stage already advanced")
            else:
                job.start(trace_id=job.trace_id)
                try:
                    outcome = await self._attempt(uow, workspace_id, job)
                except Exception as error:  # parse/data/infra faults become job state
                    outcome = _record_failure(job, error)
                await uow.ingestion_jobs.save(job)
            await uow.commit()

        if outcome.result == "chunked":  # only after commit
            self.dispatcher.dispatch_embed(workspace_id, job_id, job.trace_id)
        return outcome

    async def _attempt(
        self, uow: UnitOfWork, workspace_id: UUID, job: IngestionJob
    ) -> IngestOutcome:
        document_id = job.document_id
        if document_id is None:  # narrowed by caller; keep mypy honest
            raise ValidationFailed("job has no document")
        document = await uow.documents.get(workspace_id, document_id)
        if document is None:
            job.skip("document no longer present")
            return IngestOutcome("skipped", detail="document missing")
        source = await uow.sources.get(workspace_id, document.source_id)
        if source is None:
            job.skip("source no longer present")
            return IngestOutcome("skipped", detail="source missing")

        raw = self.file_store.read(source.uri, document.path)
        if raw is None:
            job.skip("file no longer present")
            return IngestOutcome("skipped", detail="file missing")

        content_hash = ContentHash(_sha256(raw))
        existing = await uow.document_versions.get_by_hash(
            workspace_id, document.id, str(content_hash)
        )
        if existing is not None:
            return await self._resume_existing(uow, workspace_id, job, document, existing, raw)

        parser = self.parser_for(document.path)
        parsed = parser.parse(raw, document.path)

        version = DocumentVersion(
            document_id=document.id,
            content_hash=content_hash,
            size_bytes=len(raw),
            parser=parser.name,
            meta=dict(parsed.meta),
        )
        await uow.document_versions.add(version)
        await uow.chunks.add_all(_stage_chunks(version.id, parsed))

        document.title = parsed.title or document.title
        document.mime_type = document.mime_type or mimetypes.guess_type(document.path)[0]
        await uow.documents.save(document)

        job.advance_stage("chunk")
        job.advance_stage("embed")
        return IngestOutcome("chunked")

    async def _resume_existing(
        self,
        uow: UnitOfWork,
        workspace_id: UUID,
        job: IngestionJob,
        document: Document,
        version: DocumentVersion,
        raw: bytes,
    ) -> IngestOutcome:
        """The version for these bytes already exists. Fully indexed and
        current → genuine skip. Anything else is an interrupted or
        pre-M05 pipeline: restage if needed and hand off to embed."""
        embedded = await uow.chunks.count_embedded_for_version(workspace_id, version.id)
        if document.current_version_id == version.id and embedded > 0:
            job.skip("unchanged content")
            return IngestOutcome("skipped", detail="version already indexed")

        staged = await uow.chunks.count_for_version(workspace_id, version.id)
        if staged == 0:
            parser = self.parser_for(document.path)
            parsed = parser.parse(raw, document.path)
            await uow.chunks.add_all(_stage_chunks(version.id, parsed))
        job.advance_stage("embed")
        return IngestOutcome("chunked", detail="resumed staged version")


@dataclass(frozen=True)
class EmbedDocument:
    """Embed+index stages: cache-first vectors, then the atomic swap.

    Two commits, deliberately (docs/21 §3): vectors commit before the
    flip so a crash between them re-runs as pure cache hits; the flip
    transaction moves the current pointer AND deletes other generations'
    chunks together — there is never a window where a document serves no
    chunks or mixed generations."""

    uow_factory: Callable[[], UnitOfWork]
    provider: EmbeddingProvider

    async def execute(self, workspace_id: UUID, job_id: UUID) -> IngestOutcome:
        async with self.uow_factory() as uow:
            job = await uow.ingestion_jobs.get(workspace_id, job_id)
            if job is None:
                raise NotFound(f"ingestion job {job_id} not found")
            if job.state in ("succeeded", "skipped"):
                return IngestOutcome("skipped", detail="already terminal")
            if job.state == "pending":  # embed-stage retry re-entry
                job.start(trace_id=job.trace_id)
            try:
                outcome = await self._attempt(uow, workspace_id, job)
            except Exception as error:  # provider/data/infra faults become job state
                outcome = _record_failure(job, error)
            await uow.ingestion_jobs.save(job)
            await uow.commit()
            return outcome

    async def _attempt(
        self, uow: UnitOfWork, workspace_id: UUID, job: IngestionJob
    ) -> IngestOutcome:
        if job.document_id is None or job.content_hash is None:
            raise ValidationFailed("embed stage requires a document and content hash")
        document = await uow.documents.get(workspace_id, job.document_id)
        if document is None:
            job.skip("document no longer present")
            return IngestOutcome("skipped", detail="document missing")
        source = await uow.sources.get(workspace_id, document.source_id)
        if source is None:
            job.skip("source no longer present")
            return IngestOutcome("skipped", detail="source missing")
        version = await uow.document_versions.get_by_hash(
            workspace_id, document.id, job.content_hash
        )
        if version is None:
            raise ValidationFailed("no staged version for this job's content hash")
        chunks = await uow.chunks.list_for_version(workspace_id, version.id)
        if not chunks:
            raise ValidationFailed(f"version {version.id} has no staged chunks")

        pending = [chunk for chunk in chunks if not chunk.is_embedded]
        if pending:
            if job.stage != "index":
                job.advance_stage("embed")
            completed = await self._embed(uow, workspace_id, pending)
            await uow.chunks.save_embeddings(completed)
            job.advance_stage("index")
            await uow.ingestion_jobs.save(job)
            await uow.commit()  # embed commit: a flip crash resumes as cache hits

        document.set_current_version(version.id)
        await uow.documents.save(document)
        await uow.chunks.delete_for_document_except(document.id, version.id)
        source.mark_indexed()
        await uow.sources.save(source)
        job.advance_stage("index")
        job.succeed()
        return IngestOutcome("succeeded")

    async def _embed(
        self, uow: UnitOfWork, workspace_id: UUID, pending: Sequence[Chunk]
    ) -> list[Chunk]:
        """Cache first (docs/21 §6: Postgres IS the cache), model for the
        misses only — re-embedding cost is proportional to the edit."""
        hashes = [str(chunk.content_hash) for chunk in pending]
        cached = await uow.chunks.find_embeddings(workspace_id, self.provider.model, hashes)
        misses = [chunk for chunk in pending if str(chunk.content_hash) not in cached]
        texts = [
            embedded_text(str(chunk.meta.get("breadcrumb", "")), chunk.text) for chunk in misses
        ]
        vectors = await self.provider.embed_documents(texts)
        fresh = {
            str(chunk.content_hash): vector for chunk, vector in zip(misses, vectors, strict=True)
        }
        merged = {**cached, **fresh}
        return [
            chunk.with_embedding(merged[str(chunk.content_hash)], self.provider.model)
            for chunk in pending
        ]


@dataclass(frozen=True)
class ReindexSource:
    """Full sweep of a source: every supported file re-enters the gate."""

    detect_changes: DetectChanges

    async def execute(self, workspace_id: UUID, source_id: UUID, *, batch_id: str) -> DetectReport:
        return await self.detect_changes.execute(
            workspace_id, source_id, paths=None, batch_id=batch_id
        )


@dataclass(frozen=True)
class RegisterSource:
    """Create a folder source and kick off its initial index."""

    uow_factory: Callable[[], UnitOfWork]
    file_store: SourceFileStore
    detect_changes: DetectChanges

    async def execute(
        self,
        workspace_id: UUID,
        *,
        name: str,
        uri: str,
        kind: SourceKind = "folder",
        config: dict[str, object] | None = None,
        batch_id: str,
    ) -> tuple[Source, DetectReport]:
        if kind != "folder":
            raise ValidationFailed(f"source kind {kind!r} arrives with connectors (M22)")
        if not self.file_store.exists(uri):
            raise ValidationFailed(f"folder does not exist or is unreadable: {uri}")
        source = Source(
            workspace_id=workspace_id, kind=kind, name=name, uri=uri, config=dict(config or {})
        )
        async with self.uow_factory() as uow:
            if await uow.sources.get_by_uri(workspace_id, uri) is not None:
                raise Conflict(f"source already registered for {uri}")
            await uow.sources.add(source)
            await uow.commit()
        report = await self.detect_changes.execute(workspace_id, source.id, batch_id=batch_id)
        return source, report


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
