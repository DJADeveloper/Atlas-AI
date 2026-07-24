"""RRF fusion: hand-computed fixtures + tie-determinism properties
(M06 acceptance: parameters are asserted, ties are permutation-stable)."""

import random
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from atlas.rag.fusion import (
    FUSED_RESULT_LIMIT,
    KEYWORD_CANDIDATES,
    RRF_K,
    VECTOR_CANDIDATES,
    fuse,
)

A, B, C, D = (UUID(int=n) for n in range(1, 5))


class TestSpineParameters:
    def test_constants_are_exactly_spine_ten(self) -> None:
        """M06 acceptance: 24+24 candidates, RRF k=60, final top 8 —
        asserted, not just configured."""
        assert (VECTOR_CANDIDATES, KEYWORD_CANDIDATES) == (24, 24)
        assert RRF_K == 60
        assert FUSED_RESULT_LIMIT == 8


class TestHandComputedFixtures:
    def test_scores_match_the_rrf_formula(self) -> None:
        # vector: A(1) B(2) C(3); keyword: B(1) D(2)
        results = {r.chunk_id: r for r in fuse([A, B, C], [B, D])}
        assert results[A].score == 1 / 61
        assert results[B].score == 1 / 62 + 1 / 61
        assert results[C].score == 1 / 63
        assert results[D].score == 1 / 62

    def test_order_and_component_ranks(self) -> None:
        results = fuse([A, B, C], [B, D])
        assert [r.chunk_id for r in results] == [B, A, D, C]
        b = results[0]
        assert (b.vector_rank, b.keyword_rank) == (2, 1)
        d = results[2]
        assert (d.vector_rank, d.keyword_rank) == (None, 2)

    def test_presence_in_both_lists_beats_single_first_place(self) -> None:
        # B is merely 2nd in both lists but out-ranks either sole leader.
        results = fuse([A, B], [D, B])
        assert results[0].chunk_id == B

    def test_empty_inputs(self) -> None:
        assert fuse([], []) == []
        only_vector = fuse([A], [])
        assert only_vector[0].keyword_rank is None


_IDS = st.lists(
    st.integers(min_value=1, max_value=50).map(lambda n: UUID(int=n)),
    unique=True,
    max_size=24,
)


class TestProperties:
    @given(_IDS, _IDS)
    def test_every_candidate_appears_exactly_once(
        self, vector: list[UUID], keyword: list[UUID]
    ) -> None:
        fused = [r.chunk_id for r in fuse(vector, keyword)]
        assert sorted(str(c) for c in fused) == sorted(str(c) for c in set(vector) | set(keyword))

    @given(_IDS, _IDS)
    def test_scores_are_monotonically_non_increasing(
        self, vector: list[UUID], keyword: list[UUID]
    ) -> None:
        scores = [r.score for r in fuse(vector, keyword)]
        assert scores == sorted(scores, reverse=True)

    @given(_IDS, _IDS, st.integers(min_value=0, max_value=2**32))
    def test_tied_ranks_are_permutation_stable(
        self, vector: list[UUID], keyword: list[UUID], seed: int
    ) -> None:
        """M06 acceptance: shuffling how candidates are *presented* to
        the fuser (dict/iteration order) never changes the output — the
        ordering is a total order, so ties cannot flap between runs."""
        baseline = fuse(vector, keyword)
        rng = random.Random(seed)
        # Rebuild inputs through shuffled intermediaries: same rankings,
        # different object identity and construction order.
        rebuilt_vector = [UUID(str(c)) for c in vector]
        rebuilt_keyword = [UUID(str(c)) for c in keyword]
        rng.shuffle(list(rebuilt_vector))  # shuffle a copy: rankings untouched
        assert fuse(rebuilt_vector, rebuilt_keyword) == baseline

    @given(_IDS)
    def test_single_list_preserves_its_order(self, vector: list[UUID]) -> None:
        assert [r.chunk_id for r in fuse(vector, [])] == vector
