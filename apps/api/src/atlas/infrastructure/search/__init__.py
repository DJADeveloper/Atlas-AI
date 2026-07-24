"""Hybrid retrieval adapters (M06): pgvector + Postgres FTS."""

from atlas.infrastructure.search.hybrid import SqlCandidateSearcher

__all__ = ["SqlCandidateSearcher"]
