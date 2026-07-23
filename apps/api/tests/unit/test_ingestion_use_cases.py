"""Ingestion use cases against in-memory fakes: the hash gate, retry
ladder, dedupe, and lifecycle edges — zero I/O (M04 unit layer)."""

from uuid import UUID

import pytest

from atlas.application.ingestion import (
    DetectChanges,
    IngestDocument,
    RegisterSource,
    ReindexSource,
)
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7
from tests.fakes import FakeDispatcher, FakeFileStore, FakeState, FakeUnitOfWork

URI = "/home/u/notes"


class Rig:
    def __init__(self) -> None:
        self.state = FakeState()
        self.workspace_id = uuid7()
        self.files = FakeFileStore({URI: {}})
        self.dispatcher = FakeDispatcher()
        registry = default_registry()
        uow_factory = lambda: FakeUnitOfWork(self.state)  # noqa: E731
        self.detect = DetectChanges(
            uow_factory=uow_factory,
            file_store=self.files,
            dispatcher=self.dispatcher,
            supports=registry.supports,
        )
        self.ingest = IngestDocument(
            uow_factory=uow_factory,
            file_store=self.files,
            parser_for=registry.parser_for,
        )
        self.reindex = ReindexSource(detect_changes=self.detect)
        self.register = RegisterSource(
            uow_factory=uow_factory, file_store=self.files, detect_changes=self.detect
        )

    def with_source(self) -> Source:
        source = Source(workspace_id=self.workspace_id, kind="folder", name="Notes", uri=URI)
        self.state.sources[source.id] = source
        return source

    def write(self, path: str, content: bytes) -> None:
        self.files.trees[URI][path] = content

    async def ingest_all(self, job_ids: tuple[UUID, ...]) -> list[str]:
        return [(await self.ingest.execute(self.workspace_id, job)).result for job in job_ids]


@pytest.fixture
def rig() -> Rig:
    return Rig()


class TestHashGate:
    async def test_new_file_enqueues_then_succeeds(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Title\n\nBody.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        assert len(report.enqueued_job_ids) == 1
        assert rig.dispatcher.dispatched == [(rig.workspace_id, report.enqueued_job_ids[0])]

        results = await rig.ingest_all(report.enqueued_job_ids)
        assert results == ["succeeded"]
        assert len(rig.state.versions) == 1
        document = next(iter(rig.state.documents.values()))
        assert document.title == "Title"
        assert document.current_version_id is not None

    async def test_unchanged_rerun_creates_zero_versions(self, rig: Rig) -> None:
        """M04 acceptance: re-run over unchanged corpus → zero new
        versions, jobs skipped."""
        source = rig.with_source()
        rig.write("a.md", b"# Doc\ncontent")
        first = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(first.enqueued_job_ids)
        versions_before = dict(rig.state.versions)

        second = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b2")
        assert second.enqueued_job_ids == ()
        assert second.skipped_unchanged == 1
        assert rig.state.versions == versions_before
        skipped = [j for j in rig.state.jobs.values() if j.state == "skipped"]
        assert len(skipped) == 1
        assert skipped[0].trace_id == "b2"

    async def test_modified_file_creates_new_version_and_flips_pointer(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# V1")
        first = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(first.enqueued_job_ids)
        old_version = next(iter(rig.state.versions))

        rig.write("a.md", b"# V2 changed")
        second = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b2")
        assert len(second.enqueued_job_ids) == 1
        await rig.ingest_all(second.enqueued_job_ids)

        assert len(rig.state.versions) == 2
        document = next(iter(rig.state.documents.values()))
        assert document.current_version_id != old_version

    async def test_concurrent_detect_deduplicates_active_jobs(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Racing")
        first = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        second = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b2")
        assert len(first.enqueued_job_ids) == 1
        assert second.enqueued_job_ids == ()
        assert second.deduplicated == 1


class TestRetryLadder:
    async def test_parse_failure_climbs_backoff_then_dead_letters(self, rig: Rig) -> None:
        """M04 acceptance: 3 attempts with 30/120/600 backoff, then
        dead-letter, queryable via state=failed."""
        source = rig.with_source()
        rig.write("bad.md", b"   \n  ")  # parses as empty_document every time
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        job_id = report.enqueued_job_ids[0]

        first = await rig.ingest.execute(rig.workspace_id, job_id)
        assert (first.result, first.retry_delay_seconds) == ("retry_scheduled", 30)
        second = await rig.ingest.execute(rig.workspace_id, job_id)
        assert (second.result, second.retry_delay_seconds) == ("retry_scheduled", 120)
        third = await rig.ingest.execute(rig.workspace_id, job_id)
        assert (third.result, third.retry_delay_seconds) == ("dead_lettered", None)
        assert third.detail is not None
        assert "empty_document" in third.detail

        job = rig.state.jobs[job_id]
        assert (job.state, job.attempts, job.is_dead_lettered) == ("failed", 3, True)
        assert len(rig.state.versions) == 0

    async def test_vanished_file_skips_instead_of_failing(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("gone.md", b"# Soon deleted")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        del rig.files.trees[URI]["gone.md"]
        outcome = await rig.ingest.execute(rig.workspace_id, report.enqueued_job_ids[0])
        assert outcome.result == "skipped"
        assert rig.state.jobs[report.enqueued_job_ids[0]].state == "skipped"


class TestRegisterAndReindex:
    async def test_register_scans_immediately(self, rig: Rig) -> None:
        rig.write("one.md", b"# One")
        rig.write("two.txt", b"two")
        rig.write("skip.xyz", b"unsupported")
        source, report = await rig.register.execute(
            rig.workspace_id, name="Notes", uri=URI, batch_id="b0"
        )
        assert source.id in rig.state.sources
        assert len(report.enqueued_job_ids) == 2  # .xyz filtered by supports()

    async def test_register_duplicate_uri_conflicts(self, rig: Rig) -> None:
        rig.with_source()
        with pytest.raises(Conflict):
            await rig.register.execute(rig.workspace_id, name="Again", uri=URI, batch_id="b0")

    async def test_register_missing_folder_rejected(self, rig: Rig) -> None:
        with pytest.raises(ValidationFailed):
            await rig.register.execute(
                rig.workspace_id, name="Nope", uri="/does/not/exist", batch_id="b0"
            )

    async def test_reindex_reenqueues_everything_under_one_batch(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# A")
        rig.write("b.txt", b"B")
        initial = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(initial.enqueued_job_ids)

        rig.write("b.txt", b"B changed")
        report = await rig.reindex.execute(rig.workspace_id, source.id, batch_id="batch-42")
        assert report.skipped_unchanged == 1  # a.md unchanged
        assert len(report.enqueued_job_ids) == 1  # b.txt changed
        batch_jobs = [j for j in rig.state.jobs.values() if j.trace_id == "batch-42"]
        assert len(batch_jobs) == 2  # every document re-entered the gate
