"""Ingestion use cases (docs/21 pipeline, M04 slice: parse → version).

Transaction discipline: each use case runs one Unit of Work and
dispatches to the queue only AFTER commit, so workers never race an
uncommitted row. The SHA-256 hash gate makes every step idempotent:
unchanged content becomes a `skipped` job, never a new version.
"""

import hashlib
import mimetypes
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from atlas.application.ports import IngestionDispatcher, UnitOfWork
from atlas.domain.knowledge.entities import (
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.files import SourceFileStore
from atlas.domain.knowledge.parsing import DocumentParser, ParseFailed
from atlas.domain.knowledge.values import ContentHash, SourceKind
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

IngestResult = Literal["succeeded", "skipped", "retry_scheduled", "dead_lettered"]


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

                current_hash = await self._current_hash(uow, workspace_id, document)
                job = IngestionJob(
                    source_id=source.id,
                    document_id=document.id,
                    content_hash=stat.content_hash,
                    trace_id=batch_id,
                )
                if current_hash == stat.content_hash:
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

    async def _current_hash(
        self, uow: UnitOfWork, workspace_id: UUID, document: Document
    ) -> str | None:
        if document.current_version_id is None:
            return None
        version = await uow.document_versions.get(workspace_id, document.current_version_id)
        return str(version.content_hash) if version is not None else None


@dataclass(frozen=True)
class IngestDocument:
    """One ingestion attempt: read → hash → parse → immutable version."""

    uow_factory: Callable[[], UnitOfWork]
    file_store: SourceFileStore
    parser_for: Callable[[str], DocumentParser]

    async def execute(self, workspace_id: UUID, job_id: UUID) -> IngestOutcome:
        async with self.uow_factory() as uow:
            job = await uow.ingestion_jobs.get(workspace_id, job_id)
            if job is None:
                raise NotFound(f"ingestion job {job_id} not found")
            if job.state in ("succeeded", "skipped"):
                return IngestOutcome("skipped", detail="already terminal")
            if job.document_id is None:
                raise ValidationFailed("document-less jobs are not supported at M04")

            job.start(trace_id=job.trace_id)
            try:
                outcome = await self._attempt(uow, workspace_id, job)
            except Exception as error:  # parse/data/infra faults become job state
                outcome = self._record_failure(job, error)
            await uow.ingestion_jobs.save(job)
            await uow.commit()
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
        existing = await uow.document_versions.list_for_document(workspace_id, document.id)
        if any(str(version.content_hash) == str(content_hash) for version in existing):
            job.skip("unchanged content")
            return IngestOutcome("skipped", detail="version already ingested")

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

        document.title = parsed.title or document.title
        document.mime_type = document.mime_type or mimetypes.guess_type(document.path)[0]
        document.set_current_version(version.id)
        await uow.documents.save(document)

        source.mark_indexed()
        await uow.sources.save(source)

        job.succeed()
        return IngestOutcome("succeeded")

    def _record_failure(self, job: IngestionJob, error: Exception) -> IngestOutcome:
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
