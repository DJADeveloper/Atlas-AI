"""SQLAlchemy Unit of Work implementing `atlas.application.ports.UnitOfWork`.

Semantics: one session/transaction per UoW; repositories are bound to it;
only an explicit ``commit()`` persists — leaving the block without one
rolls back, whether or not an exception occurred. That makes "forgot to
commit" a loud test failure instead of a heisen-write.
"""

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.infrastructure.persistence.repositories import (
    SqlChunkRepository,
    SqlCitationRepository,
    SqlConversationRepository,
    SqlDocumentRepository,
    SqlDocumentVersionRepository,
    SqlIngestionJobRepository,
    SqlMemoryRepository,
    SqlMessageRepository,
    SqlSourceRepository,
)


class SqlAlchemyUnitOfWork:
    sources: SqlSourceRepository
    documents: SqlDocumentRepository
    document_versions: SqlDocumentVersionRepository
    chunks: SqlChunkRepository
    ingestion_jobs: SqlIngestionJobRepository
    conversations: SqlConversationRepository
    messages: SqlMessageRepository
    citations: SqlCitationRepository
    memories: SqlMemoryRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self.sources = SqlSourceRepository(self._session)
        self.documents = SqlDocumentRepository(self._session)
        self.document_versions = SqlDocumentVersionRepository(self._session)
        self.chunks = SqlChunkRepository(self._session)
        self.ingestion_jobs = SqlIngestionJobRepository(self._session)
        self.conversations = SqlConversationRepository(self._session)
        self.messages = SqlMessageRepository(self._session)
        self.citations = SqlCitationRepository(self._session)
        self.memories = SqlMemoryRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self._require_session()
        try:
            await session.rollback()  # no-op after an explicit commit
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        await self._require_session().commit()

    async def rollback(self) -> None:
        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            msg = "UnitOfWork used outside of its async context"
            raise RuntimeError(msg)
        return self._session
