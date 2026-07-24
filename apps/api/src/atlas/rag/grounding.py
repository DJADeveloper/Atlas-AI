"""Grounded context selection and abstention (M08; spine §10, §2.4).

`select_grounding` turns fused retrieval results into the numbered
evidence set an answer may cite:

- **Dedupe by document** — a single document may contribute at most
  two chunks, so one long document cannot monopolize the context and
  crowd out corroborating sources. Fusion order is preserved.
- **Markers are positions** — entry i carries marker i+1. The context
  assembler keeps a PREFIX of chunks when the budget bites, so markers
  never renumber: dropping the tail drops the highest markers.
- **Abstention is a threshold decision, not a vibe** — if the best
  fused score is below `abstain_below`, the index has no good answer
  and Atlas says so instead of guessing ("grounded or silent"). The
  threshold is configuration; changing it changes behavior in a
  deterministic test, and M11's harness tunes it against data.

RRF scores contextualize the default (fusion.RRF_K = 60): a chunk
ranked #1 by BOTH modes scores ≈ 0.0328; #1 by one mode alone scores
≈ 0.0164. The default threshold 0.016 therefore means "at least a
top-rank hit in one mode" — permissive on purpose until M11 measures
both failure directions.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

DEFAULT_ABSTAIN_BELOW = 0.016
MAX_CHUNKS_PER_DOCUMENT = 2


@dataclass(frozen=True, slots=True)
class GroundingChunk:
    """One citable evidence entry (plain data — the rag layer never
    imports application types)."""

    chunk_id: UUID
    document_id: UUID
    text: str
    score: float
    document_title: str | None = None


@dataclass(frozen=True, slots=True)
class GroundedContext:
    entries: tuple[GroundingChunk, ...]  # entry i answers to marker i+1
    abstain: bool

    def marker_for(self, chunk_id: UUID) -> int | None:
        for index, entry in enumerate(self.entries):
            if entry.chunk_id == chunk_id:
                return index + 1
        return None


def select_grounding(
    candidates: Sequence[GroundingChunk],
    *,
    abstain_below: float = DEFAULT_ABSTAIN_BELOW,
    max_per_document: int = MAX_CHUNKS_PER_DOCUMENT,
) -> GroundedContext:
    """Fusion-ordered candidates → deduped, numbered evidence, plus
    the abstention verdict."""
    if not candidates or candidates[0].score < abstain_below:
        return GroundedContext(entries=(), abstain=True)
    kept: list[GroundingChunk] = []
    per_document: dict[UUID, int] = {}
    for candidate in candidates:
        count = per_document.get(candidate.document_id, 0)
        if count >= max_per_document:
            continue
        per_document[candidate.document_id] = count + 1
        kept.append(candidate)
    return GroundedContext(entries=tuple(kept), abstain=False)
