"""M05 acceptance: chunker quality over a 200-document fixture set.

- no chunk exceeds 1,024 tokens (the invariant);
- ≥ 90% of chunks fall within 256-768 tokens;
- consecutive chunks overlap ≈ 15% of the 512-token target (±5 points),
  measured as seeded-overlap tokens / target, averaged over every
  consecutive pair;
- no heading is split: every heading's text appears intact in a chunk.

The corpus is deterministic (seeded), shaped like real material:
heading-structured documents with occasional code, flat prose notes,
and paginated (PDF-like) documents.
"""

import random

from atlas.domain.knowledge.parsing import Block
from atlas.rag import HARD_MAX_TOKENS, TARGET_TOKENS, ChunkDraft, chunk_blocks

DOCUMENT_COUNT = 200


def _sentence(rng: random.Random) -> str:
    return " ".join(f"w{rng.randint(0, 999)}" for _ in range(rng.randint(8, 16))) + "."


def _paragraph_text(rng: random.Random) -> str:
    return " ".join(_sentence(rng) for _ in range(rng.randint(4, 10)))


def _structured_doc(rng: random.Random, index: int) -> tuple[str, list[Block]]:
    """Markdown-shaped: heading tree, paragraphs, occasional code."""
    blocks: list[Block] = [Block(kind="heading", text=f"Guide {index}", level=1)]
    for section in range(rng.randint(3, 8)):
        blocks.append(
            Block(kind="heading", text=f"Section {index}.{section}", level=rng.choice((2, 2, 3)))
        )
        for _ in range(rng.randint(2, 6)):
            blocks.append(Block(kind="paragraph", text=_paragraph_text(rng)))
        if rng.random() < 0.1:
            code = "\n".join(f"value_{line} = step_{line}()" for line in range(rng.randint(3, 12)))
            blocks.append(Block(kind="code", text=code))
    return f"Structured {index}", blocks


def _flat_doc(rng: random.Random, index: int) -> tuple[str, list[Block]]:
    """Plain-text-shaped: paragraphs only."""
    blocks = [
        Block(kind="paragraph", text=_paragraph_text(rng)) for _ in range(rng.randint(10, 25))
    ]
    return f"Notes {index}", blocks


def _paginated_doc(rng: random.Random, index: int) -> tuple[str, list[Block]]:
    """PDF-shaped: page-tagged paragraph runs."""
    blocks: list[Block] = []
    for page in range(1, rng.randint(4, 10)):
        for _ in range(rng.randint(2, 5)):
            blocks.append(Block(kind="paragraph", text=_paragraph_text(rng), page=page))
    return f"Paper {index}", blocks


def _corpus() -> list[tuple[str, list[Block]]]:
    rng = random.Random(42)
    documents: list[tuple[str, list[Block]]] = []
    for index in range(DOCUMENT_COUNT):
        if index % 10 < 6:
            documents.append(_structured_doc(rng, index))
        elif index % 10 < 8:
            documents.append(_flat_doc(rng, index))
        else:
            documents.append(_paginated_doc(rng, index))
    return documents


def _chunk_corpus() -> list[tuple[list[Block], list[ChunkDraft]]]:
    return [(blocks, chunk_blocks(blocks, title=title)) for title, blocks in _corpus()]


class TestFixtureDistribution:
    def test_hard_max_holds_everywhere(self) -> None:
        for _, drafts in _chunk_corpus():
            assert all(d.token_count <= HARD_MAX_TOKENS for d in drafts)

    def test_at_least_ninety_percent_within_target_band(self) -> None:
        sizes = [d.token_count for _, drafts in _chunk_corpus() for d in drafts]
        in_band = sum(1 for size in sizes if 256 <= size <= 768)
        share = in_band / len(sizes)
        assert len(sizes) >= 900  # the corpus is big enough to mean something
        assert share >= 0.90, f"only {share:.1%} of {len(sizes)} chunks in 256-768"

    def test_mean_overlap_near_fifteen_percent(self) -> None:
        ratios = [
            draft.overlap_tokens / TARGET_TOKENS
            for _, drafts in _chunk_corpus()
            for draft in drafts[1:]  # every consecutive pair, doc-local
        ]
        mean = sum(ratios) / len(ratios)
        assert 0.10 <= mean <= 0.20, f"mean overlap {mean:.1%} outside 15% ±5pts"

    def test_no_heading_split_across_chunks(self) -> None:
        for blocks, drafts in _chunk_corpus():
            for block in blocks:
                if block.kind == "heading":
                    assert any(block.text in draft.text for draft in drafts)
