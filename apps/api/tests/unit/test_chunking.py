"""Structure-aware chunker: examples + Hypothesis invariants (M05).

The one hard invariant is the 1,024-token max (spine §10); everything
else — target sizes, boundary preferences, overlap — is a target the
fixture-distribution suite (`test_chunking_fixture.py`) measures.
"""

import hashlib

from hypothesis import given
from hypothesis import strategies as st

from atlas.domain.knowledge.parsing import Block
from atlas.rag import (
    HARD_MAX_TOKENS,
    OVERLAP_TOKENS,
    ChunkDraft,
    build_breadcrumb,
    chunk_blocks,
)
from atlas.shared.tokens import count_tokens


def _paragraph(text: str, page: int | None = None) -> Block:
    return Block(kind="paragraph", text=text, page=page)


def _heading(text: str, level: int) -> Block:
    return Block(kind="heading", text=text, level=level)


def _prose(sentences: int, words_per_sentence: int = 10) -> str:
    return " ".join(
        " ".join(f"word{s}x{w}" for w in range(words_per_sentence)) + "." for s in range(sentences)
    )


class TestBreadcrumbAndIdentity:
    def test_breadcrumb_joins_title_and_path(self) -> None:
        assert build_breadcrumb("Q3 planning", ("Budget", "Headcount")) == (
            "Q3 planning > Budget > Headcount"
        )
        assert build_breadcrumb(None, ("Budget",)) == "Budget"
        assert build_breadcrumb(None, ()) == ""

    def test_embedded_text_is_breadcrumb_prefixed_and_hashed(self) -> None:
        draft = ChunkDraft(
            text="The notice period is 30 days.",
            token_count=8,
            breadcrumb="Contract > Termination clauses",
            heading_path=("Termination clauses",),
        )
        assert draft.embedded_text == (
            "Contract > Termination clauses\n\nThe notice period is 30 days."
        )
        expected = hashlib.sha256(draft.embedded_text.encode()).hexdigest()
        assert draft.content_hash == expected

    def test_display_text_stays_clean_without_breadcrumb_leak(self) -> None:
        blocks = [_heading("Overview", 1), _paragraph("Body text here.")]
        (draft,) = chunk_blocks(blocks, title="Doc")
        assert "Doc > Overview" not in draft.text
        assert draft.embedded_text.startswith("Doc > Overview\n\n")


class TestHeadingStructure:
    def test_heading_path_follows_the_stack(self) -> None:
        blocks = [
            _heading("Intro", 1),
            _paragraph("intro text."),
            _heading("Details", 1),
            _heading("Sub", 2),
            _paragraph("sub text."),
        ]
        drafts = chunk_blocks(blocks, title=None)
        assert drafts[-1].heading_path[-1] in ("Sub", "Intro")  # single small chunk merges
        # With a boundary-close-worthy first section the paths separate:
        blocks = [
            _heading("Intro", 1),
            _paragraph(_prose(40)),
            _heading("Details", 1),
            _heading("Sub", 2),
            _paragraph(_prose(40)),
        ]
        drafts = chunk_blocks(blocks, title=None)
        assert drafts[0].heading_path == ("Intro",)
        # The second chunk STARTS at the "Details" heading, so that is its
        # path — "Sub" arrives inside the chunk, steering later chunks.
        assert drafts[-1].heading_path == ("Details",)

    def test_sibling_heading_replaces_stack_level(self) -> None:
        blocks = [
            _heading("A", 2),
            _paragraph(_prose(40)),
            _heading("B", 2),
            _paragraph(_prose(40)),
        ]
        drafts = chunk_blocks(blocks, title=None)
        assert drafts[0].heading_path == ("A",)
        assert drafts[-1].heading_path == ("B",)


class TestSplittingAndPacking:
    def test_oversized_paragraph_splits_at_sentences(self) -> None:
        big = _prose(sentences=200)  # ~2,200 tokens
        drafts = chunk_blocks([_paragraph(big)])
        assert len(drafts) > 1
        assert all(d.token_count <= HARD_MAX_TOKENS for d in drafts)
        # sentence boundaries respected: pieces end at sentence ends
        assert all(d.text.rstrip().endswith(".") for d in drafts)

    def test_oversized_code_splits_at_lines(self) -> None:
        code = "\n".join(f"line_{i} = compute_{i}(argument_{i})" for i in range(300))
        drafts = chunk_blocks([Block(kind="code", text=code)])
        assert len(drafts) > 1
        for draft in drafts:
            assert draft.token_count <= HARD_MAX_TOKENS
            for line in draft.text.splitlines():
                if line.startswith("line_"):
                    assert line in code  # no line was cut mid-way

    def test_small_code_block_stays_whole(self) -> None:
        code = "def f(x):\n    return x + 1"
        blocks = [_paragraph(_prose(30)), Block(kind="code", text=code), _paragraph(_prose(30))]
        drafts = chunk_blocks(blocks)
        assert any(code in draft.text for draft in drafts)

    def test_page_span_recorded_for_paginated_blocks(self) -> None:
        blocks = [_paragraph(_prose(15), page=1), _paragraph(_prose(15), page=2)]
        (draft,) = chunk_blocks(blocks)
        assert (draft.page_start, draft.page_end) == (1, 2)

    def test_small_tail_merges_into_predecessor(self) -> None:
        # 495 + 110 tokens: the second paragraph alone would close as a
        # sub-256 tail; the merge welds it back under the hard max.
        blocks = [_paragraph(_prose(45)), _paragraph(_prose(10))]
        drafts = chunk_blocks(blocks)
        assert len(drafts) == 1
        assert drafts[0].token_count <= HARD_MAX_TOKENS

    def test_overlap_seeds_next_chunk_with_trailing_sentences(self) -> None:
        drafts = chunk_blocks([_paragraph(_prose(120))])
        assert len(drafts) >= 2
        follower = drafts[1]
        assert 0 < follower.overlap_tokens <= OVERLAP_TOKENS
        seed = follower.text.split("\n\n")[0]
        assert seed in drafts[0].text  # verbatim trailing content

    def test_empty_input_yields_no_chunks(self) -> None:
        assert chunk_blocks([]) == []


_WORDS = st.integers(min_value=1, max_value=40)
_TEXTS = st.builds(
    lambda n, seed: " ".join(f"w{seed}n{i}" for i in range(n)) + ".",
    _WORDS,
    st.integers(min_value=0, max_value=9),
)
_BLOCKS = st.lists(
    st.one_of(
        st.builds(lambda t: Block(kind="paragraph", text=t), _TEXTS),
        st.builds(
            lambda t, level: Block(kind="heading", text=t, level=level),
            _TEXTS,
            st.integers(min_value=1, max_value=4),
        ),
        st.builds(lambda t: Block(kind="code", text=t), _TEXTS),
    ),
    max_size=30,
)


class TestProperties:
    @given(_BLOCKS)
    def test_hard_max_is_never_exceeded(self, blocks: list[Block]) -> None:
        for draft in chunk_blocks(blocks, title="T"):
            assert 0 < draft.token_count <= HARD_MAX_TOKENS
            assert draft.text.strip()

    @given(_BLOCKS)
    def test_every_block_survives_intact(self, blocks: list[Block]) -> None:
        """No content loss, and blocks under the target (all of these)
        are never split — headings included (M05 acceptance)."""
        drafts = chunk_blocks(blocks, title="T")
        for block in blocks:
            assert any(block.text in draft.text for draft in drafts)

    @given(_BLOCKS)
    def test_deterministic(self, blocks: list[Block]) -> None:
        assert chunk_blocks(blocks, title="T") == chunk_blocks(blocks, title="T")

    @given(_BLOCKS)
    def test_overlap_stays_within_budget(self, blocks: list[Block]) -> None:
        for draft in chunk_blocks(blocks, title="T"):
            assert draft.overlap_tokens <= OVERLAP_TOKENS

    @given(_BLOCKS)
    def test_token_counts_are_honest(self, blocks: list[Block]) -> None:
        for draft in chunk_blocks(blocks, title="T"):
            assert draft.token_count == count_tokens(draft.text)
