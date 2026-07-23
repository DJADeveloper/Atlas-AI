"""Knowledge entities: invariants and the ingestion state machine."""

from typing import Any

import pytest

from atlas.domain.knowledge.entities import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    Source,
)
from atlas.domain.knowledge.values import ContentHash
from atlas.shared.errors import Conflict, ValidationFailed
from atlas.shared.ids import uuid7

HASH = ContentHash("a" * 64)


def _source() -> Source:
    return Source(workspace_id=uuid7(), kind="folder", name="Projects", uri="/home/u/projects")


class TestValueObjects:
    def test_content_hash_accepts_sha256_hex(self) -> None:
        assert str(HASH) == "a" * 64

    @pytest.mark.parametrize("bad", ["", "xyz", "A" * 64, "a" * 63])
    def test_content_hash_rejects_non_digests(self, bad: str) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ContentHash(bad)


class TestSourceAndDocument:
    def test_source_requires_name_and_uri(self) -> None:
        with pytest.raises(ValidationFailed):
            Source(workspace_id=uuid7(), kind="folder", name=" ", uri="/x")

    def test_soft_delete_is_idempotence_guarded(self) -> None:
        source = _source()
        source.soft_delete()
        assert source.is_deleted
        with pytest.raises(Conflict):
            source.soft_delete()

    def test_document_current_version_flip(self) -> None:
        document = Document(source_id=uuid7(), path="notes/atlas.md")
        version = DocumentVersion(
            document_id=document.id, content_hash=HASH, size_bytes=10, parser="markdown"
        )
        document.set_current_version(version.id)
        assert document.current_version_id == version.id


class TestChunkInvariants:
    def test_valid_chunk(self) -> None:
        chunk = Chunk(
            document_version_id=uuid7(),
            ordinal=0,
            text="Atlas is an AI operating system.",
            token_count=7,
            heading_path=("Overview",),
            embedding=(0.1, 0.2),
            embedding_model="nomic-embed-text",
        )
        assert chunk.heading_path == ("Overview",)

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [("ordinal", -1), ("text", ""), ("token_count", 0), ("embedding", ())],
    )
    def test_invalid_chunks_rejected(self, field_name: str, value: object) -> None:
        base: dict[str, Any] = {
            "document_version_id": uuid7(),
            "ordinal": 0,
            "text": "t",
            "token_count": 1,
            "embedding": (0.1,),
            "embedding_model": "nomic-embed-text",
        }
        base[field_name] = value
        with pytest.raises(ValidationFailed):
            Chunk(**base)


class TestIngestionJobStateMachine:
    def test_happy_path(self) -> None:
        job = IngestionJob(source_id=uuid7())
        job.start(trace_id="t-1")
        assert (job.state, job.attempts) == ("running", 1)
        job.succeed()
        assert job.state == "succeeded"
        assert job.finished_at is not None

    def test_fail_then_retry_then_succeed(self) -> None:
        job = IngestionJob(source_id=uuid7())
        job.start()
        job.fail("parser exploded")
        assert (job.state, job.error) == ("failed", "parser exploded")
        job.retry()
        assert (job.state, job.started_at, job.finished_at) == ("pending", None, None)
        job.start()
        job.succeed()
        assert job.attempts == 2

    @pytest.mark.parametrize(
        ("setup", "illegal"),
        [
            ((), "succeed"),  # pending -> succeeded skips running
            (("start", "succeed"), "start"),  # terminal states are terminal
            (("start", "skip"), "retry"),  # skipped is not retryable
        ],
    )
    def test_illegal_transitions_raise(self, setup: tuple[str, ...], illegal: str) -> None:
        job = IngestionJob(source_id=uuid7())
        for step in setup:
            getattr(job, step)("x") if step in ("fail", "skip") else getattr(job, step)()
        with pytest.raises(Conflict):
            getattr(job, illegal)("x") if illegal in ("fail", "skip") else getattr(job, illegal)()


class TestDeadLetterSemantics:
    def test_failed_before_exhaustion_can_retry(self) -> None:
        job = IngestionJob(source_id=uuid7())
        job.start()
        job.fail("boom")
        assert job.can_retry
        assert not job.is_dead_lettered

    def test_failed_at_exhaustion_is_dead_lettered(self) -> None:
        job = IngestionJob(source_id=uuid7())
        for _ in range(3):
            job.start()
            job.fail("boom")
            if job.can_retry:
                job.retry()
        assert job.attempts == 3
        assert job.is_dead_lettered
        assert not job.can_retry

    def test_succeeded_is_never_dead_lettered(self) -> None:
        job = IngestionJob(source_id=uuid7())
        job.start()
        job.succeed()
        assert not job.is_dead_lettered
