"""Dropped-file uploads against in-memory fakes: the managed source is
created once, hostile filenames never escape it, unsupported and
oversized files are refused with a reason, and everything accepted joins
the ordinary ingestion pipeline."""

import pytest

from atlas.application.ingestion import DetectChanges, IncomingFile, UploadFiles, UploadReport
from atlas.application.ingestion.uploads import (
    MAX_UPLOAD_BYTES,
    UPLOADS_SOURCE_NAME,
    safe_filename,
    workspace_uploads_uri,
)
from atlas.infrastructure.parsing import default_registry
from atlas.shared.ids import uuid7
from tests.fakes import FakeDispatcher, FakeFileStore, FakeState, FakeUnitOfWork

UPLOADS_ROOT = "/home/u/.atlas/uploads"


class Rig:
    def __init__(self) -> None:
        self.state = FakeState()
        self.workspace_id = uuid7()
        self.files = FakeFileStore()
        self.dispatcher = FakeDispatcher()
        registry = default_registry()

        def uow_factory() -> FakeUnitOfWork:
            return FakeUnitOfWork(self.state)

        self.upload = UploadFiles(
            uow_factory=uow_factory,
            file_writer=self.files,
            detect_changes=DetectChanges(
                uow_factory=uow_factory,
                file_store=self.files,
                dispatcher=self.dispatcher,
                supports=registry.supports,
            ),
            supports=registry.supports,
            uploads_root=UPLOADS_ROOT,
        )

    @property
    def uri(self) -> str:
        return workspace_uploads_uri(UPLOADS_ROOT, self.workspace_id)

    async def drop(self, *files: IncomingFile, batch: str = "batch-1") -> UploadReport:
        return await self.upload.execute(self.workspace_id, files=list(files), batch_id=batch)


@pytest.fixture
def rig() -> Rig:
    return Rig()


class TestSafeFilename:
    @pytest.mark.parametrize(
        "raw",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system.ini",
            "/etc/shadow",
            "notes/../../escape.md",
            "sub/",
        ],
    )
    def test_traversal_is_reduced_to_a_bare_name(self, raw: str) -> None:
        safe = safe_filename(raw)
        assert safe is not None
        assert "/" not in safe
        assert "\\" not in safe
        assert safe not in {"..", "."}

    @pytest.mark.parametrize("raw", ["", "   ", "..", ".", ".hidden"])
    def test_untrustworthy_names_are_refused(self, raw: str) -> None:
        assert safe_filename(raw) is None


class TestUploadFiles:
    async def test_first_drop_creates_the_managed_source_and_enqueues(self, rig: Rig) -> None:
        report = await rig.drop(IncomingFile("meridian.md", b"# Meridian\n\n30 days notice.\n"))

        assert report.source.name == UPLOADS_SOURCE_NAME
        assert report.source.uri == rig.uri
        assert report.stored == ("meridian.md",)
        assert report.rejected == ()
        assert report.enqueued == 1
        assert rig.files.trees[rig.uri]["meridian.md"].startswith(b"# Meridian")
        assert len(rig.dispatcher.dispatched) == 1

    async def test_second_drop_reuses_the_same_source(self, rig: Rig) -> None:
        first = await rig.drop(IncomingFile("a.md", b"# A\n"))
        second = await rig.drop(IncomingFile("b.md", b"# B\n"), batch="batch-2")

        assert second.source.id == first.source.id
        assert len(rig.state.sources) == 1

    async def test_identical_redrop_creates_no_duplicate_work(self, rig: Rig) -> None:
        """The same bytes twice dedupe against the still-pending job; once
        that job has produced an embedded version the hash gate skips
        instead (both paths enqueue nothing)."""
        await rig.drop(IncomingFile("a.md", b"# A\n"))
        again = await rig.drop(IncomingFile("a.md", b"# A\n"), batch="batch-2")

        assert again.stored == ("a.md",)
        assert again.enqueued == 0
        assert len(rig.dispatcher.dispatched) == 1

    async def test_edited_content_replaces_the_namesake_and_reindexes(self, rig: Rig) -> None:
        await rig.drop(IncomingFile("a.md", b"# A\n"))
        edited = await rig.drop(IncomingFile("a.md", b"# A revised\n"), batch="batch-2")

        assert rig.files.trees[rig.uri]["a.md"] == b"# A revised\n"
        assert edited.enqueued == 1

    async def test_unsupported_type_is_rejected_with_a_reason(self, rig: Rig) -> None:
        report = await rig.drop(IncomingFile("archive.zip", b"PK\x03\x04"))

        assert report.stored == ()
        assert [(r.filename, r.reason) for r in report.rejected] == [
            ("archive.zip", "unsupported_type")
        ]
        assert report.enqueued == 0

    async def test_empty_and_oversized_files_are_rejected(self, rig: Rig) -> None:
        report = await rig.drop(
            IncomingFile("empty.md", b""),
            IncomingFile("huge.md", b"x" * (MAX_UPLOAD_BYTES + 1)),
        )

        assert {r.reason for r in report.rejected} == {"empty_file", "too_large"}
        assert rig.files.trees.get(rig.uri) == {}

    async def test_hostile_filename_never_escapes_the_managed_root(self, rig: Rig) -> None:
        report = await rig.drop(IncomingFile("../../../etc/notes.md", b"# Notes\n"))

        assert report.stored == ("notes.md",)
        assert list(rig.files.trees) == [rig.uri]

    async def test_a_mixed_drop_stores_the_good_and_reports_the_rest(self, rig: Rig) -> None:
        report = await rig.drop(
            IncomingFile("keep.md", b"# Keep\n"),
            IncomingFile("skip.zip", b"PK"),
            IncomingFile("also.txt", b"plain text\n"),
        )

        assert sorted(report.stored) == ["also.txt", "keep.md"]
        assert [r.filename for r in report.rejected] == ["skip.zip"]
        assert report.enqueued == 2

    async def test_workspaces_get_separate_roots(self, rig: Rig) -> None:
        other = uuid7()
        assert workspace_uploads_uri(UPLOADS_ROOT, other) != rig.uri
