"""Structure-aware chunking (docs/21 §5; parameters fixed by spine §10).

One packer for every format: parsers emit the block IR, this module
spends the token budget on semantic units. The rules, in order:

1. Pack whole blocks until adding the next would exceed the 512 target.
2. Never split an atomic block (code, table) unless it alone exceeds the
   budget — then split at line/row boundaries; paragraphs split at
   sentence boundaries.
3. Prefer to close at a structural boundary (heading) once a chunk is
   "full enough" — a coherent 350-token chunk out-ranks an incoherent
   512-token one.
4. Seed the next chunk with ~15% trailing overlap, in whole sentences,
   so claims that straddle a boundary stay retrievable from one side.
5. Prefix the *embedded* text with the breadcrumb (title + heading
   path); display text stays clean for citations. The breadcrumb-
   prefixed text is what ``content_hash`` digests — the embedding-cache
   identity.

The 1,024 hard max is the only invariant; sizes are targets (spine §10).
"""

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from atlas.domain.knowledge.parsing import Block
from atlas.shared.tokens import count_tokens

TARGET_TOKENS = 512
HARD_MAX_TOKENS = 1024
OVERLAP_TOKENS = TARGET_TOKENS * 15 // 100  # 76

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ATOMIC_KINDS = frozenset({"code", "table"})


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A chunk before persistence identity (ordinal/version join later).

    ``text`` is clean display content; ``embedded_text`` carries the
    breadcrumb prefix and is what the vector — and the cache key —
    are computed from. ``overlap_tokens`` records how much of the text
    was seeded from the previous chunk (measured, not assumed).
    """

    text: str
    token_count: int
    breadcrumb: str
    heading_path: tuple[str, ...]
    overlap_tokens: int = 0
    page_start: int | None = None
    page_end: int | None = None

    @property
    def embedded_text(self) -> str:
        return embedded_text(self.breadcrumb, self.text)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.embedded_text.encode()).hexdigest()


def build_breadcrumb(title: str | None, heading_path: Sequence[str]) -> str:
    parts = ([title] if title else []) + list(heading_path)
    return " > ".join(parts)


def embedded_text(breadcrumb: str, text: str) -> str:
    """The exact string a chunk's vector — and cache key — derive from.
    The embed stage reconstructs it from the stored row alone, so the
    format is a contract: breadcrumb, blank line, display text."""
    return f"{breadcrumb}\n\n{text}" if breadcrumb else text


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_END.split(text) if part.strip()]


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _word_groups(text: str, max_tokens: int) -> list[str]:
    """Last-resort split for a single unit beyond ``max_tokens``."""
    groups: list[list[str]] = [[]]
    tokens = 0
    for word in text.split():
        word_tokens = count_tokens(word)
        if tokens + word_tokens > max_tokens and groups[-1]:
            groups.append([])
            tokens = 0
        groups[-1].append(word)
        tokens += word_tokens
    return [" ".join(group) for group in groups if group]


def _split_oversized(block: Block, max_tokens: int) -> list[str]:
    """Split one over-budget block at its natural boundaries: lines for
    code/tables (docs/21 §5.2), sentences for prose."""
    units = _lines(block.text) if block.kind in _ATOMIC_KINDS else _sentences(block.text)
    joiner = "\n" if block.kind in _ATOMIC_KINDS else " "
    pieces: list[str] = []
    current: list[str] = []
    tokens = 0
    for unit in units:
        unit_tokens = count_tokens(unit)
        if unit_tokens > max_tokens:
            if current:
                pieces.append(joiner.join(current))
                current, tokens = [], 0
            pieces.extend(_word_groups(unit, max_tokens))
            continue
        if tokens + unit_tokens > max_tokens and current:
            pieces.append(joiner.join(current))
            current, tokens = [], 0
        current.append(unit)
        tokens += unit_tokens
    if current:
        pieces.append(joiner.join(current))
    return pieces


@dataclass(slots=True)
class _Piece:
    text: str
    tokens: int
    page: int | None


@dataclass(slots=True)
class _Builder:
    """Accumulates pieces for one chunk; path/pages fix at first content."""

    title: str | None
    heading_path: tuple[str, ...] | None = None
    pieces: list[_Piece] = field(default_factory=list)
    tokens: int = 0
    overlap_tokens: int = 0

    def seed_overlap(self, text: str, tokens: int) -> None:
        self.pieces.append(_Piece(text, tokens, None))
        self.tokens += tokens
        self.overlap_tokens = tokens

    def append(self, piece: _Piece, heading_path: tuple[str, ...]) -> None:
        if self.heading_path is None:
            self.heading_path = heading_path
        self.pieces.append(piece)
        self.tokens += piece.tokens

    @property
    def has_content(self) -> bool:
        """Overlap alone is not content — it already lives in the
        previous chunk; an overlap-only builder at EOF emits nothing."""
        return self.heading_path is not None

    def emit(self) -> ChunkDraft:
        # has_content guards emit(); the fallback keeps misuse harmless.
        heading_path = self.heading_path if self.heading_path is not None else ()
        pages = [piece.page for piece in self.pieces if piece.page is not None]
        return ChunkDraft(
            text="\n\n".join(piece.text for piece in self.pieces),
            token_count=self.tokens,
            breadcrumb=build_breadcrumb(self.title, heading_path),
            heading_path=heading_path,
            overlap_tokens=self.overlap_tokens,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
        )


def _overlap_tail(text: str, budget: int) -> tuple[str, int]:
    """Trailing whole sentences within ``budget`` tokens ('' if even the
    last sentence is too large — no overlap beats a giant duplicate)."""
    taken: list[str] = []
    tokens = 0
    for sentence in reversed(_sentences(text)):
        sentence_tokens = count_tokens(sentence)
        if tokens + sentence_tokens > budget:
            break
        taken.insert(0, sentence)
        tokens += sentence_tokens
    return " ".join(taken), tokens


def chunk_blocks(
    blocks: Iterable[Block],
    *,
    title: str | None = None,
    target_tokens: int = TARGET_TOKENS,
    hard_max_tokens: int = HARD_MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[ChunkDraft]:
    """Pack parsed blocks into chunk drafts. Every draft's
    ``token_count`` is ≤ ``hard_max_tokens`` — the one invariant."""
    drafts: list[ChunkDraft] = []
    path: list[tuple[int, str]] = []
    builder = _Builder(title)
    # Close early at a heading once a chunk holds a coherent section's
    # worth; below that the heading joins mid-chunk over emitting slivers.
    boundary_close_min = target_tokens * 3 // 5
    # Never emit a sliver just because a large block arrives next.
    min_pack_tokens = target_tokens // 4

    def close() -> None:
        nonlocal builder
        if not builder.has_content:
            return
        draft = builder.emit()
        drafts.append(draft)
        builder = _Builder(title)
        seed, seed_tokens = _overlap_tail(draft.text, overlap_tokens)
        if seed:
            builder.seed_overlap(seed, seed_tokens)

    for block in blocks:
        current_path = tuple(text for _, text in path)
        if block.kind == "heading" and block.level is not None:
            if builder.tokens >= boundary_close_min:
                close()
            while path and path[-1][0] >= block.level:
                path.pop()
            path.append((block.level, block.text))
            current_path = tuple(text for _, text in path)

        block_tokens = count_tokens(block.text)
        piece_texts = (
            _split_oversized(block, target_tokens) if block_tokens > target_tokens else [block.text]
        )
        for piece_text in piece_texts:
            piece = _Piece(piece_text, count_tokens(piece_text), block.page)
            would_hold = builder.tokens + piece.tokens
            # Close on hard-max always; close on target overflow unless the
            # builder holds only a sliver (then over-packing beats emitting it).
            if would_hold > hard_max_tokens or (
                would_hold > target_tokens and builder.tokens >= min_pack_tokens
            ):
                close()
            builder.append(piece, current_path)

    close()
    return _merge_small_tail(drafts, target_tokens // 2, hard_max_tokens)


def _merge_small_tail(
    drafts: list[ChunkDraft], merge_below: int, hard_max_tokens: int
) -> list[ChunkDraft]:
    """A tiny final chunk (document tail) reads better welded to its
    predecessor than ranked alone — merge when the result stays legal."""
    if len(drafts) <= 1:
        return drafts
    tail, previous = drafts[-1], drafts[-2]
    merged_tokens = previous.token_count + tail.token_count
    if tail.token_count >= merge_below or merged_tokens > hard_max_tokens:
        return drafts
    pages = [p for p in (previous.page_start, tail.page_start) if p is not None]
    ends = [p for p in (previous.page_end, tail.page_end) if p is not None]
    merged = ChunkDraft(
        text=f"{previous.text}\n\n{tail.text}",
        token_count=merged_tokens,
        breadcrumb=previous.breadcrumb,
        heading_path=previous.heading_path,
        overlap_tokens=previous.overlap_tokens,
        page_start=min(pages) if pages else None,
        page_end=max(ends) if ends else None,
    )
    return [*drafts[:-2], merged]
