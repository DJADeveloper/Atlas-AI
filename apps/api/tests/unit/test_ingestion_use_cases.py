"""Ingestion use cases against in-memory fakes: the hash gate, staged
parse→chunk→embed→index pipeline, embedding cache, retry ladder,
dedupe, and lifecycle edges — zero I/O (M04/M05 unit layer)."""

from collections.abc import Sequence
from uuid import UUID

import pytest

from atlas.application.ingestion import (
    DetectChanges,
    EmbedDocument,
    IngestDocument,
    RegisterSource,
    ReindexSource,
)
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7
from tests.fakes import (
    FakeDispatcher,
    FakeEmbeddingProvider,
    FakeFileStore,
    FakeState,
    FakeUnitOfWork,
)

URI = "/home/u/notes"


class ExplodingProvider:
    """Ollama-down stand-in: every call fails."""

    model = "fake-embed"

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        raise RuntimeError("ollama is down")

    async def embed_query(self, text: str) -> tuple[float, ...]:
        raise RuntimeError("ollama is down")


class Rig:
    def __init__(self) -> None:
        self.state = FakeState()
        self.workspace_id = uuid7()
        self.files = FakeFileStore({URI: {}})
        self.dispatcher = FakeDispatcher()
        self.provider = FakeEmbeddingProvider()
        registry = default_registry()

        def uow_factory() -> FakeUnitOfWork:
            return FakeUnitOfWork(self.state)

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
            dispatcher=self.dispatcher,
        )
        self.embed = EmbedDocument(uow_factory=uow_factory, provider=self.provider)
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
        """Drive each job through the whole pipeline, as the two Celery
        tasks would; returns final results."""
        results: list[str] = []
        for job_id in job_ids:
            outcome = await self.ingest.execute(self.workspace_id, job_id)
            if outcome.result == "chunked":
                outcome = await self.embed.execute(self.workspace_id, job_id)
            results.append(outcome.result)
        return results


@pytest.fixture
def rig() -> Rig:
    return Rig()


class TestHashGate:
    async def test_new_file_enqueues_then_succeeds(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Title\n\nBody.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        assert len(report.enqueued_job_ids) == 1
        assert rig.dispatcher.dispatched == [(rig.workspace_id, report.enqueued_job_ids[0], "b1")]

        results = await rig.ingest_all(report.enqueued_job_ids)
        assert results == ["succeeded"]
        assert len(rig.state.versions) == 1
        document = next(iter(rig.state.documents.values()))
        assert document.title == "Title"
        assert document.current_version_id is not None
        chunks = list(rig.state.chunks.values())
        assert chunks and all(c.is_embedded for c in chunks)

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

    async def test_unreachable_root_fails_loudly_rather_than_skipping(self, rig: Rig) -> None:
        """A root this process cannot see at all is a deployment fault —
        the API and worker not sharing the uploads volume, say. Reporting
        it as a skip would read as "nothing to do" and hide the cause."""
        source = rig.with_source()
        rig.write("a.md", b"# Present at enqueue time")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        del rig.files.trees[URI]  # the whole root, not one file

        outcome = await rig.ingest.execute(rig.workspace_id, report.enqueued_job_ids[0])

        assert outcome.result == "retry_scheduled"
        job = rig.state.jobs[report.enqueued_job_ids[0]]
        assert job.state != "skipped"
        assert job.error is not None
        assert "not readable from this process" in job.error
        assert "ATLAS_UPLOADS_DIR" in job.error


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


class TestStagedPipeline:
    """M05: parse+chunk stages then embed+index, with the atomic swap."""

    async def test_parse_stage_stages_chunks_without_flipping(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Title\n\nBody paragraph for staging.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        job_id = report.enqueued_job_ids[0]

        outcome = await rig.ingest.execute(rig.workspace_id, job_id)
        assert outcome.result == "chunked"
        job = rig.state.jobs[job_id]
        assert (job.state, job.stage) == ("running", "embed")
        assert rig.dispatcher.embed_dispatched == [(rig.workspace_id, job_id, "b1")]

        staged = list(rig.state.chunks.values())
        assert staged and all(not chunk.is_embedded for chunk in staged)
        document = next(iter(rig.state.documents.values()))
        assert document.current_version_id is None  # flip belongs to index
        assert rig.provider.calls == []

    async def test_embed_stage_completes_and_swaps(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Title\n\nBody paragraph for embedding.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        job_id = report.enqueued_job_ids[0]
        await rig.ingest.execute(rig.workspace_id, job_id)

        outcome = await rig.embed.execute(rig.workspace_id, job_id)
        assert outcome.result == "succeeded"
        job = rig.state.jobs[job_id]
        assert (job.state, job.stage) == ("succeeded", "index")
        chunks = list(rig.state.chunks.values())
        assert chunks and all(chunk.embedding_model == "fake-embed" for chunk in chunks)
        document = next(iter(rig.state.documents.values()))
        assert document.current_version_id is not None  # pointer flipped
        assert document.current_version_id in rig.state.versions
        assert rig.state.sources[source.id].last_indexed_at is not None

    async def test_breadcrumb_prefixed_text_is_what_gets_embedded(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Atlas Guide\n\nGrounded answers or silence.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(report.enqueued_job_ids)
        embedded_texts = [text for batch in rig.provider.calls for text in batch]
        assert any(text.startswith("Atlas Guide") for text in embedded_texts)

    async def test_identical_content_elsewhere_hits_the_cache(self, rig: Rig) -> None:
        """docs/21 §6: vectors are reused by (model, content_hash) — a
        byte-identical document embeds zero new texts."""
        source = rig.with_source()
        rig.write("a.md", b"# Same\n\nShared body.")
        first = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(first.enqueued_job_ids)
        embedded_after_first = rig.provider.total_texts_embedded
        assert embedded_after_first > 0

        rig.write("copy.md", b"# Same\n\nShared body.")
        second = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b2")
        results = await rig.ingest_all(second.enqueued_job_ids)
        assert results == ["succeeded"]
        assert rig.provider.total_texts_embedded == embedded_after_first

    async def test_unchanged_reindex_makes_zero_provider_calls(self, rig: Rig) -> None:
        """M05 acceptance: full reindex of unchanged content performs
        zero embedding provider calls."""
        source = rig.with_source()
        rig.write("a.md", b"# Doc\n\nStable content.")
        first = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(first.enqueued_job_ids)
        calls_before = rig.provider.total_texts_embedded

        rerun = await rig.reindex.execute(rig.workspace_id, source.id, batch_id="b2")
        assert rerun.enqueued_job_ids == ()
        assert rerun.skipped_unchanged == 1
        assert rig.provider.total_texts_embedded == calls_before

    async def test_current_version_without_index_is_backfilled(self, rig: Rig) -> None:
        """A hash-matching current version with no embedded chunks (an
        M04-era row) re-enters the pipeline instead of skipping."""
        source = rig.with_source()
        rig.write("a.md", b"# Doc\n\nBackfill me.")
        first = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(first.enqueued_job_ids)
        rig.state.chunks.clear()  # simulate the pre-M05 database shape

        second = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b2")
        assert len(second.enqueued_job_ids) == 1
        results = await rig.ingest_all(second.enqueued_job_ids)
        assert results == ["succeeded"]
        chunks = list(rig.state.chunks.values())
        assert chunks and all(chunk.is_embedded for chunk in chunks)
        assert len(rig.state.versions) == 1  # resumed, never re-created

    async def test_edit_after_index_replaces_old_generation_chunks(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# V1\n\nOriginal body.")
        first = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        await rig.ingest_all(first.enqueued_job_ids)

        rig.write("a.md", b"# V2\n\nRewritten body.")
        second = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b2")
        await rig.ingest_all(second.enqueued_job_ids)

        document = next(iter(rig.state.documents.values()))
        remaining_versions = {c.document_version_id for c in rig.state.chunks.values()}
        assert remaining_versions == {document.current_version_id}  # old chunks gone
        assert len(rig.state.versions) == 2  # version history stays for citations

    async def test_embed_failure_climbs_ladder_to_dead_letter(self, rig: Rig) -> None:
        """An Ollama outage retries the embed stage on the 30/120/600
        ladder without ever re-parsing, then dead-letters."""
        rig.embed = EmbedDocument(
            uow_factory=lambda: FakeUnitOfWork(rig.state), provider=ExplodingProvider()
        )
        source = rig.with_source()
        rig.write("a.md", b"# Doc\n\nWill not embed.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        job_id = report.enqueued_job_ids[0]
        assert (await rig.ingest.execute(rig.workspace_id, job_id)).result == "chunked"

        first = await rig.embed.execute(rig.workspace_id, job_id)
        assert (first.result, first.retry_delay_seconds) == ("retry_scheduled", 30)
        second = await rig.embed.execute(rig.workspace_id, job_id)
        assert (second.result, second.retry_delay_seconds) == ("retry_scheduled", 120)
        third = await rig.embed.execute(rig.workspace_id, job_id)
        assert third.result == "dead_lettered"

        job = rig.state.jobs[job_id]
        assert (job.state, job.is_dead_lettered) == ("failed", True)
        document = next(iter(rig.state.documents.values()))
        assert document.current_version_id is None  # the old index never lied

    async def test_duplicate_parse_delivery_only_repokes_embed(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Doc\n\nDelivered twice.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        job_id = report.enqueued_job_ids[0]
        await rig.ingest.execute(rig.workspace_id, job_id)
        versions_after_first = dict(rig.state.versions)

        duplicate = await rig.ingest.execute(rig.workspace_id, job_id)
        assert duplicate.result == "chunked"
        assert rig.state.versions == versions_after_first
        assert len(rig.dispatcher.embed_dispatched) == 2  # re-poked, not re-parsed

        assert (await rig.embed.execute(rig.workspace_id, job_id)).result == "succeeded"

    async def test_embed_on_terminal_job_is_a_noop(self, rig: Rig) -> None:
        source = rig.with_source()
        rig.write("a.md", b"# Doc\n\nDone already.")
        report = await rig.detect.execute(rig.workspace_id, source.id, batch_id="b1")
        job_id = report.enqueued_job_ids[0]
        await rig.ingest_all(report.enqueued_job_ids)
        outcome = await rig.embed.execute(rig.workspace_id, job_id)
        assert (outcome.result, outcome.detail) == ("skipped", "already terminal")
