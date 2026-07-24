"""Grounded selection, abstention threshold, and streaming-safe
citation extraction (M08; spine §10, §2.4). The marker round-trip
property is an acceptance criterion: reconstruction must be lossless
no matter how the stream chops a marker apart."""

import random
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from atlas.rag import (
    GroundedContext,
    GroundingChunk,
    MarkerAccumulator,
    extract_markers,
    select_grounding,
)
from atlas.shared.ids import uuid7


def _chunk(
    document_id: UUID | None = None, score: float = 0.03, text: str = "evidence"
) -> GroundingChunk:
    resolved = document_id if document_id is not None else uuid7()
    return GroundingChunk(chunk_id=uuid7(), document_id=resolved, text=text, score=score)


class TestSelectGrounding:
    def test_one_document_contributes_at_most_two_chunks(self) -> None:
        monopolist = uuid7()
        other = uuid7()
        candidates = [
            _chunk(monopolist, 0.033),
            _chunk(monopolist, 0.032),
            _chunk(monopolist, 0.031),  # third from the same doc: dropped
            _chunk(other, 0.017),
        ]
        grounded = select_grounding(candidates)
        assert not grounded.abstain
        assert len(grounded.entries) == 3
        assert [e.document_id for e in grounded.entries].count(monopolist) == 2
        # Fusion order preserved; markers are positions.
        assert grounded.entries[0].chunk_id == candidates[0].chunk_id
        assert grounded.marker_for(candidates[3].chunk_id) == 3

    def test_empty_retrieval_abstains(self) -> None:
        assert select_grounding([]) == GroundedContext(entries=(), abstain=True)

    def test_threshold_is_configuration_not_vibes(self) -> None:
        """M08 acceptance: changing the config value deterministically
        changes behavior on identical input."""
        candidates = [_chunk(score=0.0165)]
        assert select_grounding(candidates, abstain_below=0.016).abstain is False
        assert select_grounding(candidates, abstain_below=0.017).abstain is True

    def test_weak_top_score_abstains(self) -> None:
        grounded = select_grounding([_chunk(score=0.001)])
        assert grounded.abstain
        assert grounded.entries == ()


class TestExtractMarkers:
    def test_first_appearance_order_deduplicated(self) -> None:
        text = "Pro is $12/mo [2], set in July [1]. Confirmed [2] later [12]."
        assert extract_markers(text) == [2, 1, 12]

    def test_non_marker_brackets_are_ignored(self) -> None:
        assert extract_markers("array[0] notation [abc] and [] stay out; [3] counts") == [0, 3]

    def test_hallucinated_markers_are_reported_not_repaired(self) -> None:
        # Validation against the evidence range is the caller's job.
        assert extract_markers("see [9]") == [9]


class TestMarkerAccumulator:
    def test_split_marker_completes_exactly_once(self) -> None:
        accumulator = MarkerAccumulator()
        assert accumulator.feed("Pro is $12/mo [1") == []
        assert accumulator.feed("]") == [1]
        assert accumulator.feed(" and again [1].") == []  # dedupe
        assert accumulator.feed(" Also [2]") == [2]
        assert accumulator.markers == (1, 2)

    @given(
        markers=st.lists(st.integers(min_value=1, max_value=99), min_size=0, max_size=10),
        words=st.lists(st.text(alphabet="abc ", min_size=1, max_size=8), min_size=1, max_size=12),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
    )
    def test_chunked_streams_reconstruct_the_exact_marker_set(
        self, markers: list[int], words: list[str], seed: int
    ) -> None:
        """M08 acceptance property: however the stream is chopped —
        including mid-marker — the accumulator reconstructs exactly the
        markers of the full text, in the same order."""
        rng = random.Random(seed)
        parts: list[str] = []
        for index, word in enumerate(words):
            parts.append(word)
            if index < len(markers):
                parts.append(f"[{markers[index]}]")
        text = " ".join(parts)

        deltas: list[str] = []
        position = 0
        while position < len(text):
            step = rng.randint(1, 5)
            deltas.append(text[position : position + step])
            position += step

        accumulator = MarkerAccumulator()
        emitted: list[int] = []
        for delta in deltas:
            emitted.extend(accumulator.feed(delta))

        assert list(accumulator.markers) == extract_markers(text)
        assert emitted == extract_markers(text)  # each completes exactly once
        assert accumulator.text == text
