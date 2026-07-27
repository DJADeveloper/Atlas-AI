"""Dropped-file uploads: browser drop zone → managed folder source.

A browser cannot hand over a filesystem path, so dropped bytes land in a
folder Atlas owns (`settings.uploads_dir/<workspace_id>`) that is
registered as an ordinary folder source. Everything downstream — the
hash gate, parse, chunk, embed, index, and retention — is the M04/M05
pipeline unchanged: uploads add an entry point, not a second pipeline.

Re-dropping a file replaces its namesake, exactly as saving over it in a
file manager would; the hash gate then turns identical content into a
`skipped` job rather than a new version.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from atlas.application.ingestion.use_cases import DetectChanges
from atlas.application.ports import UnitOfWork
from atlas.domain.knowledge.entities import Source
from atlas.domain.knowledge.files import SourceFileWriter
from atlas.shared.errors import Conflict

UPLOADS_SOURCE_NAME = "Dropped files"

# Mirrors the parsers' refusal threshold (docs/21: files beyond 50 MB
# are refused) so an upload that could never be parsed is rejected at
# the door, with a reason, instead of failing later inside a job.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

RejectionReason = Literal["invalid_name", "unsupported_type", "empty_file", "too_large"]


@dataclass(frozen=True, slots=True)
class IncomingFile:
    """One file as the transport handed it over."""

    filename: str
    data: bytes


@dataclass(frozen=True, slots=True)
class RejectedFile:
    filename: str
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class UploadReport:
    source: Source
    batch_id: str
    stored: tuple[str, ...]
    rejected: tuple[RejectedFile, ...]
    enqueued: int
    skipped_unchanged: int


def workspace_uploads_uri(uploads_root: str, workspace_id: UUID) -> str:
    """The managed root for one workspace (pure path math, no I/O)."""
    return str(Path(uploads_root).expanduser() / str(workspace_id))


def safe_filename(raw: str) -> str | None:
    """The bare filename, or None when it cannot be trusted.

    Browsers send a basename, but the field is attacker-controlled:
    separators are normalized first so a Windows-style `..\\..\\x.md`
    cannot survive as a relative path on a POSIX host, and the result
    keeps only the final component. Dotfiles are refused because the
    scanner treats them as noise.
    """
    candidate = PurePosixPath(raw.replace("\\", "/")).name.strip()
    if not candidate or candidate.startswith("."):
        return None
    return candidate


@dataclass(frozen=True)
class UploadFiles:
    """Store dropped files in the managed source and index them."""

    uow_factory: Callable[[], UnitOfWork]
    file_writer: SourceFileWriter
    detect_changes: DetectChanges
    supports: Callable[[str], bool]
    uploads_root: str

    async def execute(
        self,
        workspace_id: UUID,
        *,
        files: Sequence[IncomingFile],
        batch_id: str,
    ) -> UploadReport:
        source = await self._ensure_source(workspace_id)
        stored: list[str] = []
        rejected: list[RejectedFile] = []
        for item in files:
            name = safe_filename(item.filename)
            if name is None:
                rejected.append(RejectedFile(item.filename, "invalid_name"))
                continue
            reason = self._rejection(name, item.data)
            if reason is not None:
                rejected.append(RejectedFile(name, reason))
                continue
            self.file_writer.write(source.uri, name, item.data)
            stored.append(name)

        # paths= (never None) keeps this to the dropped files: a full
        # rescan of the uploads folder would be wasted work.
        report = await self.detect_changes.execute(
            workspace_id, source.id, paths=stored, batch_id=batch_id
        )
        return UploadReport(
            source=source,
            batch_id=report.batch_id,
            stored=tuple(stored),
            rejected=tuple(rejected),
            enqueued=len(report.enqueued_job_ids),
            skipped_unchanged=report.skipped_unchanged,
        )

    def _rejection(self, name: str, data: bytes) -> RejectionReason | None:
        if not self.supports(name):
            return "unsupported_type"
        if not data:
            return "empty_file"
        if len(data) > MAX_UPLOAD_BYTES:
            return "too_large"
        return None

    async def _ensure_source(self, workspace_id: UUID) -> Source:
        """Get-or-create the managed source, tolerating a parallel drop."""
        uri = workspace_uploads_uri(self.uploads_root, workspace_id)
        self.file_writer.ensure_root(uri)
        existing = await self._by_uri(workspace_id, uri)
        if existing is not None:
            return existing
        source = Source(workspace_id=workspace_id, kind="folder", name=UPLOADS_SOURCE_NAME, uri=uri)
        try:
            async with self.uow_factory() as uow:
                await uow.sources.add(source)
                await uow.commit()
        except Conflict:
            # A concurrent upload registered it first; its row wins.
            winner = await self._by_uri(workspace_id, uri)
            if winner is None:  # pragma: no cover - conflict implies a row
                raise
            return winner
        return source

    async def _by_uri(self, workspace_id: UUID, uri: str) -> Source | None:
        async with self.uow_factory() as uow:
            return await uow.sources.get_by_uri(workspace_id, uri)
