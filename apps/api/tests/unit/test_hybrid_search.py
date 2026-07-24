"""HybridSearch use case over fakes: spine §10 parameters actually
requested, fusion order surfaced, highlights carried, reranker seam."""

from collections.abc import Sequence
from uuid import UUID

import pytest

from atlas.application.ports import Candidate, SearchFilters
from atlas.application.retrieval import HybridSearch
from atlas.shared.errors import ValidationFailed
from atlas.shared.ids import uuid7
from tests.fakes import FakeEmbeddingProvider

DOC = uuid7()


def _candidate(chunk_id: UUID, score: float, highlight: str | None = None) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        document_id=DOC,
        text=f"text {chunk_id}",
        score=score,
        heading_path=("Section",),
        highlight=highlight,
    )


class RecordingSearcher:
    """Returns scripted candidates; records every request's shape."""

    def __init__(
        self, vector: list[Candidate] | None = None, keyword: list[Candidate] | None = None
    ) -> None:
        self.vector = vector or []
        self.keyword = keyword or []
        self.requests: list[tuple[str, UUID, int, SearchFilters]] = []

    async def find_vector_candidates(
        self,
        workspace_id: UUID,
        embedding: Sequence[float],
        *,
        limit: int,
        filters: SearchFilters,
    ) -> list[Candidate]:
        self.requests.append(("vector", workspace_id, limit, filters))
        return self.vector

    async def find_keyword_candidates(
        self, workspace_id: UUID, query: str, *, limit: int, filters: SearchFilters
    ) -> list[Candidate]:
        self.requests.append(("keyword", workspace_id, limit, filters))
        return self.keyword


class ReversingReranker:
    """Deterministic stand-in proving the port is honored."""

    def __init__(self) -> None:
        self.calls = 0

    async def rerank(self, query: str, candidates: Sequence[Candidate]) -> list[Candidate]:
        self.calls += 1
        return list(reversed(candidates))


class TestSpineParametersAreRequested:
    async def test_requests_twenty_four_from_each_mode(self) -> None:
        searcher = RecordingSearcher()
        workspace_id = uuid7()
        await HybridSearch(searcher, FakeEmbeddingProvider()).execute(workspace_id, "notice")
        assert [(mode, limit) for mode, _, limit, _ in searcher.requests] == [
            ("vector", 24),
            ("keyword", 24),
        ]
        assert all(ws == workspace_id for _, ws, _, _ in searcher.requests)

    async def test_filters_reach_both_modes_verbatim(self) -> None:
        searcher = RecordingSearcher()
        filters = SearchFilters(source_ids=(uuid7(),), mime_types=("text/markdown",))
        await HybridSearch(searcher, FakeEmbeddingProvider()).execute(uuid7(), "q", filters=filters)
        assert all(f == filters for _, _, _, f in searcher.requests)


class TestFusionAndResults:
    async def test_results_carry_ranks_scores_and_highlights(self) -> None:
        a, b, c = uuid7(), uuid7(), uuid7()
        searcher = RecordingSearcher(
            vector=[_candidate(a, 0.9), _candidate(b, 0.8)],
            keyword=[_candidate(b, 5.0, highlight="<mark>b</mark>"), _candidate(c, 4.0)],
        )
        results = await HybridSearch(searcher, FakeEmbeddingProvider()).execute(uuid7(), "q")
        assert [r.chunk_id for r in results] == [b, a, c]
        top = results[0]
        assert (top.vector_rank, top.keyword_rank) == (2, 1)
        assert top.highlight == "<mark>b</mark>"  # keyword metadata wins
        assert results[1].highlight is None
        assert results[0].score > results[1].score

    async def test_limit_cuts_to_top_n(self) -> None:
        vector = [_candidate(uuid7(), 1.0 - i / 100) for i in range(24)]
        searcher = RecordingSearcher(vector=vector)
        results = await HybridSearch(searcher, FakeEmbeddingProvider()).execute(
            uuid7(), "q", limit=3
        )
        assert len(results) == 3

    async def test_default_limit_is_top_eight(self) -> None:
        vector = [_candidate(uuid7(), 1.0 - i / 100) for i in range(24)]
        searcher = RecordingSearcher(vector=vector)
        results = await HybridSearch(searcher, FakeEmbeddingProvider()).execute(uuid7(), "q")
        assert len(results) == 8

    async def test_empty_index_returns_empty(self) -> None:
        results = await HybridSearch(RecordingSearcher(), FakeEmbeddingProvider()).execute(
            uuid7(), "anything"
        )
        assert results == []


class TestValidationAndReranker:
    async def test_blank_query_rejected(self) -> None:
        with pytest.raises(ValidationFailed):
            await HybridSearch(RecordingSearcher(), FakeEmbeddingProvider()).execute(uuid7(), "   ")

    async def test_limit_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationFailed):
            await HybridSearch(RecordingSearcher(), FakeEmbeddingProvider()).execute(
                uuid7(), "q", limit=9
            )

    async def test_reranker_reorders_when_configured(self) -> None:
        a, b = uuid7(), uuid7()
        searcher = RecordingSearcher(vector=[_candidate(a, 0.9), _candidate(b, 0.8)])
        reranker = ReversingReranker()
        results = await HybridSearch(searcher, FakeEmbeddingProvider(), reranker=reranker).execute(
            uuid7(), "q"
        )
        assert reranker.calls == 1
        assert [r.chunk_id for r in results] == [b, a]
        assert results[0].vector_rank == 2  # fusion metadata survives reranking

    async def test_no_reranker_by_default(self) -> None:
        assert HybridSearch(RecordingSearcher(), FakeEmbeddingProvider()).reranker is None
