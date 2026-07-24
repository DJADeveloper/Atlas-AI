"""Composition root: the one place the object graph is assembled.

Clean Architecture becomes enforceable here (ADR-0002): adapters are
constructed at the process edge and handed to consumers as values.
Nothing in the codebase imports a live singleton — tests build as many
independent containers as they like. The worker bootstrap (M04) will
assemble the same ``Container`` from its own entrypoint.
"""

import asyncio
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.ai import BreakerBoard, CostMeter, ModelRouter, ResilientExecutor, as_routing_profile
from atlas.ai.context import ContextAssembler
from atlas.application.chat import (
    ChatRuntime,
    CreateConversation,
    GetConversation,
    ListConversations,
    SendMessage,
    StreamAnswer,
)
from atlas.application.memory import (
    ForgetMemory,
    ListMemories,
    RememberFact,
    ReviseMemory,
)
from atlas.application.retrieval import HybridSearch
from atlas.config.feature_flags import FeatureFlags, LayeredFeatureFlags
from atlas.config.settings import Settings
from atlas.domain.ai.provider import LLMProvider
from atlas.infrastructure.jobs.celery_app import create_celery_app
from atlas.infrastructure.jobs.dispatcher import CeleryChatDispatcher, CeleryIngestionDispatcher
from atlas.infrastructure.parsing import ParserRegistry, default_registry
from atlas.infrastructure.persistence.feature_flags import SqlFlagOverridesReader
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.providers.anthropic import AnthropicChatProvider
from atlas.infrastructure.providers.ollama import OllamaChatProvider, OllamaEmbeddingProvider
from atlas.infrastructure.search import SqlCandidateSearcher
from atlas.infrastructure.streams import RedisStreamBuffer
from atlas.infrastructure.watcher.filesystem import LocalFileStore


@dataclass(frozen=True)
class Container:
    """Long-lived process dependencies, built once per process."""

    settings: Settings
    feature_flags: LayeredFeatureFlags
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    dispatcher: CeleryIngestionDispatcher
    chat_dispatcher: CeleryChatDispatcher
    file_store: LocalFileStore
    parser_registry: ParserRegistry
    embedding_provider: OllamaEmbeddingProvider
    searcher: SqlCandidateSearcher
    chat_runtime: ChatRuntime
    stream_buffer: RedisStreamBuffer
    anthropic_chat: AnthropicChatProvider
    ollama_chat: OllamaChatProvider

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """One Unit of Work per use-case invocation (application port)."""
        return SqlAlchemyUnitOfWork(self.session_factory)

    def hybrid_search(self) -> HybridSearch:
        """spine §10 retrieval; reranker off by default."""
        return HybridSearch(self.searcher, self.embedding_provider)

    # Chat (M07). Use cases are cheap per-request values over the
    # long-lived runtime — the same pattern as hybrid_search().
    def create_conversation(self) -> CreateConversation:
        return CreateConversation(self.unit_of_work)

    def list_conversations(self) -> ListConversations:
        return ListConversations(self.unit_of_work)

    def get_conversation(self) -> GetConversation:
        return GetConversation(self.unit_of_work)

    def send_message(self) -> SendMessage:
        return SendMessage(
            uow_factory=self.unit_of_work,
            runtime=self.chat_runtime,
            dispatcher=self.chat_dispatcher,
        )

    def stream_answer(self) -> StreamAnswer:
        return StreamAnswer(
            uow_factory=self.unit_of_work,
            runtime=self.chat_runtime,
            dispatcher=self.chat_dispatcher,
        )

    # Memory v1 (M07).
    def remember_fact(self) -> RememberFact:
        return RememberFact(self.unit_of_work)

    def list_memories(self) -> ListMemories:
        return ListMemories(self.unit_of_work)

    def revise_memory(self) -> ReviseMemory:
        return ReviseMemory(self.unit_of_work)

    def forget_memory(self) -> ForgetMemory:
        return ForgetMemory(self.unit_of_work)


def build_container(settings: Settings) -> Container:
    """Construct the object graph.

    Pure construction: both clients connect lazily on first use, so this
    never performs I/O — the same guarantee `create_app` makes.
    """
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(settings.redis_url)
    celery = create_celery_app(settings)
    # The anthropic adapter is registered even without a key: it raises
    # AuthFailed pre-dial and the chain degrades to local rungs — the
    # hybrid-without-credentials contract (docs/20 §5.4), never silent.
    anthropic_chat = AnthropicChatProvider(settings.anthropic_api_key)
    ollama_chat = OllamaChatProvider(settings.ollama_url)
    providers: dict[str, LLMProvider] = {"anthropic": anthropic_chat, "ollama": ollama_chat}
    return Container(
        settings=settings,
        feature_flags=LayeredFeatureFlags(
            FeatureFlags(settings.feature_flags),
            SqlFlagOverridesReader(session_factory),
        ),
        db_engine=engine,
        session_factory=session_factory,
        redis=redis,
        dispatcher=CeleryIngestionDispatcher(celery),
        chat_dispatcher=CeleryChatDispatcher(celery),
        file_store=LocalFileStore(),
        parser_registry=default_registry(),
        embedding_provider=OllamaEmbeddingProvider(
            settings.ollama_url,
            settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            concurrency=settings.embedding_concurrency,
        ),
        searcher=SqlCandidateSearcher(session_factory, ef_search=settings.hnsw_ef_search),
        chat_runtime=ChatRuntime(
            router=ModelRouter(),
            executor=ResilientExecutor(
                providers, BreakerBoard(time.monotonic), sleep=asyncio.sleep
            ),
            assembler=ContextAssembler(),
            cost_meter=CostMeter(),
            profile=as_routing_profile(settings.profile),
        ),
        stream_buffer=RedisStreamBuffer(redis),
        anthropic_chat=anthropic_chat,
        ollama_chat=ollama_chat,
    )


async def close_container(container: Container) -> None:
    await container.embedding_provider.aclose()
    await container.ollama_chat.aclose()
    await container.anthropic_chat.aclose()
    await container.redis.aclose()
    await container.db_engine.dispose()
