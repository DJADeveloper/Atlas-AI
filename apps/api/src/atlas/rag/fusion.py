"""Reciprocal Rank Fusion (spine §10: k=60, 24+24 candidates → top 8).

RRF sidesteps the incomparable-score problem entirely: pgvector cosine
similarities and `ts_rank_cd` values live on different scales that drift
independently (re-embeds move one, corpus statistics move the other),
so scores are never mixed — only ranks are. Each candidate contributes
``1 / (k + rank)`` per list it appears in; k=60 keeps the tail flat
enough that a strong showing in ONE list cannot be buried by absence
from the other.

Component ranks are preserved on every fused result (M06 risk note:
debuggability — when fusion surprises, the raw ranks explain it).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

RRF_K = 60
VECTOR_CANDIDATES = 24
KEYWORD_CANDIDATES = 24
FUSED_RESULT_LIMIT = 8


@dataclass(frozen=True, slots=True)
class FusedResult:
    chunk_id: UUID
    score: float
    vector_rank: int | None  # 1-based rank in the vector candidate list
    keyword_rank: int | None  # 1-based rank in the keyword candidate list


def fuse(
    vector_ranking: Sequence[UUID],
    keyword_ranking: Sequence[UUID],
    *,
    k: int = RRF_K,
) -> list[FusedResult]:
    """Fuse two ordered candidate id lists into one ranked list.

    Deterministic under any tie: ordering is (score desc, best component
    rank asc, id) — a total order, so equal-scored results never depend
    on insertion or hash order (M06 permutation-stability criterion).
    """
    vector_ranks = {chunk_id: index + 1 for index, chunk_id in enumerate(vector_ranking)}
    keyword_ranks = {chunk_id: index + 1 for index, chunk_id in enumerate(keyword_ranking)}

    results = []
    for chunk_id in vector_ranks.keys() | keyword_ranks.keys():
        vector_rank = vector_ranks.get(chunk_id)
        keyword_rank = keyword_ranks.get(chunk_id)
        score = sum(1.0 / (k + rank) for rank in (vector_rank, keyword_rank) if rank is not None)
        results.append(FusedResult(chunk_id, score, vector_rank, keyword_rank))

    def sort_key(result: FusedResult) -> tuple[float, int, str]:
        best = min(rank for rank in (result.vector_rank, result.keyword_rank) if rank is not None)
        return (-result.score, best, str(result.chunk_id))

    return sorted(results, key=sort_key)
