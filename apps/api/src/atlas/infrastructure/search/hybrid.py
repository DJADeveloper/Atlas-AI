"""SQL candidate generation (M06; docs/21 §7.1): one datastore, two
retrieval modes — the core bet of ADR-0003.

Both modes search only chunks of a document's CURRENT version (the join
goes through ``documents.current_version_id``), in the caller's
workspace, with soft-deleted documents and sources invisible — the same
scoping rules the repositories enforce. Filters land in SQL, never in
Python, so "a filtered search never returns a chunk outside the filter"
is a database guarantee (M06 acceptance).
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.application.ports import Candidate, SearchFilters
from atlas.infrastructure.persistence.tables import (
    ChunkRow,
    DocumentRow,
    DocumentVersionRow,
    SourceRow,
)

# ts_headline profile: <mark> spans, bounded length — the UI renders
# these verbatim inside a sanitized container (M09).
_HEADLINE_OPTIONS = "StartSel=<mark>, StopSel=</mark>, MaxWords=30, MinWords=5"


class SqlCandidateSearcher:
    """`CandidateSearcher` port over pgvector HNSW + Postgres FTS."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, ef_search: int = 40
    ) -> None:
        self._factory = session_factory
        self._ef_search = ef_search

    def _scoped(self, workspace_id: UUID, filters: SearchFilters) -> Select[Any]:
        stmt = (
            select(
                ChunkRow.id,
                DocumentRow.id.label("document_id"),
                ChunkRow.text,
                ChunkRow.heading_path,
            )
            .join(DocumentRow, DocumentRow.current_version_id == ChunkRow.document_version_id)
            .join(SourceRow, DocumentRow.source_id == SourceRow.id)
            .join(DocumentVersionRow, DocumentVersionRow.id == ChunkRow.document_version_id)
            .where(SourceRow.workspace_id == workspace_id)
            .where(SourceRow.deleted_at.is_(None))
            .where(DocumentRow.deleted_at.is_(None))
        )
        if filters.source_ids:
            stmt = stmt.where(DocumentRow.source_id.in_(filters.source_ids))
        if filters.file_types:
            extension_matches = [
                DocumentRow.path.ilike(f"%.{file_type.lstrip('.').lower()}")
                for file_type in filters.file_types
            ]
            stmt = stmt.where(or_(*extension_matches))
        if filters.modified_after is not None:
            stmt = stmt.where(DocumentVersionRow.created_at >= filters.modified_after)
        if filters.modified_before is not None:
            stmt = stmt.where(DocumentVersionRow.created_at <= filters.modified_before)
        return stmt

    async def find_vector_candidates(
        self,
        workspace_id: UUID,
        embedding: Sequence[float],
        *,
        limit: int,
        filters: SearchFilters,
    ) -> list[Candidate]:
        distance = ChunkRow.embedding.cosine_distance(list(embedding))
        stmt = (
            self._scoped(workspace_id, filters)
            .add_columns(distance.label("distance"))
            .where(ChunkRow.embedding.is_not(None))
            .order_by(distance, ChunkRow.id)
            .limit(limit)
        )
        async with self._factory() as session, session.begin():
            # SET LOCAL scopes the recall knob to this transaction only
            # (docs/41 §8: ef_search is per-query, not server-wide).
            await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(self._ef_search)}"))
            rows = (await session.execute(stmt)).all()
        return [
            Candidate(
                chunk_id=row.id,
                document_id=row.document_id,
                text=row.text,
                score=1.0 - float(row.distance),  # cosine similarity
                heading_path=tuple(row.heading_path),
            )
            for row in rows
        ]

    async def find_keyword_candidates(
        self,
        workspace_id: UUID,
        query: str,
        *,
        limit: int,
        filters: SearchFilters,
    ) -> list[Candidate]:
        tsquery = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank_cd(ChunkRow.tsv, tsquery)
        highlight = func.ts_headline("english", ChunkRow.text, tsquery, _HEADLINE_OPTIONS)
        stmt = (
            self._scoped(workspace_id, filters)
            .add_columns(rank.label("rank"), highlight.label("highlight"))
            .where(ChunkRow.tsv.op("@@")(tsquery))
            .order_by(rank.desc(), ChunkRow.id)
            .limit(limit)
        )
        async with self._factory() as session:
            rows = (await session.execute(stmt)).all()
        return [
            Candidate(
                chunk_id=row.id,
                document_id=row.document_id,
                text=row.text,
                score=float(row.rank),
                heading_path=tuple(row.heading_path),
                highlight=row.highlight,
            )
            for row in rows
        ]
