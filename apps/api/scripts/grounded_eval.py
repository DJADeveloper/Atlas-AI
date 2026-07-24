"""Grounded-answer eval (M08 acceptance; numbers pinned by M11).

Ingests the golden corpus through the real pipeline, then drives the
REAL chat path — retrieval, grounded prompt, executor, citation
persistence — over two fixture sets:

- 40 answerable questions (`evals/grounded/answerable_v1.jsonl`):
  target ≥ 95% of answers carry ≥ 1 citation, and 100% of persisted
  citation rows point at offered evidence (structural, but measured).
- 20 unanswerable questions (`evals/grounded/unanswerable_v1.jsonl`):
  target ≥ 90% abstain with zero fabricated citations.

    ANTHROPIC_API_KEY=... uv run python scripts/grounded_eval.py \
        --chat anthropic --embeddings ollama --output grounded_eval.json

`--chat fake` answers every question with "[1]" — a smoke mode that
exercises the whole harness (ingestion, retrieval, persistence)
without any model, used to keep the script itself honest in CI.

Corpus seeding mirrors scripts/retrieval_eval.py (kept self-contained
on purpose: harness scripts are standalone entry points, not a
library).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from atlas.ai import BreakerBoard, CostMeter, ModelRouter, ResilientExecutor
from atlas.ai.context import ContextAssembler
from atlas.application.chat import ChatRuntime, CreateConversation, SendMessage
from atlas.application.ingestion import DetectChanges, EmbedDocument, IngestDocument
from atlas.application.ports import EmbeddingProvider
from atlas.application.retrieval import HybridSearch
from atlas.domain.ai.provider import (
    ChatEvent,
    ChatRequest,
    ChatResponse,
    LLMProvider,
    ProviderChatMessage,
    Usage,
)
from atlas.domain.knowledge.entities import Source
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.migrations import run_migrations_sync
from atlas.infrastructure.persistence.prompts import SqlPromptRegistry
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.providers.anthropic import AnthropicChatProvider
from atlas.infrastructure.providers.ollama import OllamaChatProvider, OllamaEmbeddingProvider
from atlas.infrastructure.search import SqlCandidateSearcher
from atlas.infrastructure.watcher.filesystem import LocalFileStore
from atlas.rag import extract_markers

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "evals" / "fixtures" / "corpus"
ANSWERABLE_PATH = REPO_ROOT / "evals" / "grounded" / "answerable_v1.jsonl"
UNANSWERABLE_PATH = REPO_ROOT / "evals" / "grounded" / "unanswerable_v1.jsonl"

TARGET_ANSWERABLE_CITED = 0.95
TARGET_UNANSWERABLE_ABSTAINED = 0.90


class _NullDispatcher:
    """Ingestion and chat background dispatch are inline/skipped here."""

    def dispatch(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        return None

    def dispatch_embed(self, workspace_id: UUID, job_id: UUID, trace_id: str | None = None) -> None:
        return None

    def dispatch_summarize(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None:
        return None

    def dispatch_title(
        self, workspace_id: UUID, conversation_id: UUID, trace_id: str | None = None
    ) -> None:
        return None


class _FakeChatProvider:
    """Smoke mode: always answers with one citation marker."""

    @property
    def name(self) -> str:
        return "fake"

    @property
    def is_local(self) -> bool:
        return True

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=ProviderChatMessage(role="assistant", content="According to the docs [1]."),
            usage=Usage(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
            model=model,
            provider="fake",
        )

    def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        raise NotImplementedError("the eval drives complete()")


@dataclass(frozen=True)
class Question:
    id: str
    question: str


def _load(path: Path) -> list[Question]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [Question(id=row["id"], question=row["question"]) for row in rows]


async def _ingest_corpus(
    factory: async_sessionmaker[AsyncSession], workspace_id: UUID, provider: EmbeddingProvider
) -> None:
    registry = default_registry()

    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(factory)

    detect = DetectChanges(
        uow_factory=uow,
        file_store=LocalFileStore(),
        dispatcher=_NullDispatcher(),
        supports=registry.supports,
    )
    ingest = IngestDocument(
        uow_factory=uow,
        file_store=LocalFileStore(),
        parser_for=registry.parser_for,
        dispatcher=_NullDispatcher(),
    )
    embed = EmbedDocument(uow_factory=uow, provider=provider)
    source = Source(workspace_id=workspace_id, kind="folder", name="Golden", uri=str(CORPUS_DIR))
    async with uow() as unit:
        await unit.sources.add(source)
        await unit.commit()
    report = await detect.execute(workspace_id, source.id, batch_id="grounded-eval")
    for job_id in report.enqueued_job_ids:
        outcome = await ingest.execute(workspace_id, job_id)
        if outcome.result == "chunked":
            outcome = await embed.execute(workspace_id, job_id)
        if outcome.result != "succeeded":
            raise RuntimeError(f"corpus ingestion failed: {outcome}")


async def evaluate(
    database_url: str, chat_provider: LLMProvider, embeddings: EmbeddingProvider
) -> dict[str, object]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        workspace_id = await ensure_default_workspace(factory, name="Eval")
        await _ingest_corpus(factory, workspace_id, embeddings)

        def uow() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(factory)

        providers: dict[str, LLMProvider] = {
            "anthropic": chat_provider,
            "ollama": chat_provider,
            "fake": chat_provider,
        }
        runtime = ChatRuntime(
            router=ModelRouter(),
            executor=ResilientExecutor(
                providers, BreakerBoard(time.monotonic), sleep=asyncio.sleep
            ),
            assembler=ContextAssembler(),
            cost_meter=CostMeter(),
            prompts=SqlPromptRegistry(factory),
            profile="hybrid" if chat_provider.name == "anthropic" else "local_only",
        )
        send = SendMessage(
            uow_factory=uow,
            runtime=runtime,
            dispatcher=_NullDispatcher(),
            retriever=HybridSearch(SqlCandidateSearcher(factory), embeddings),
        )
        create = CreateConversation(uow)

        async def ask(question: Question) -> dict[str, object]:
            conversation = await create.execute(workspace_id, title=question.id)
            result = await send.execute(workspace_id, conversation.id, question.question)
            content = result.assistant_message.content
            emitted = extract_markers(content)
            persisted = [c.marker for c in result.citations]
            return {
                "id": question.id,
                "abstained": result.abstained,
                "emitted_markers": emitted,
                "persisted_markers": persisted,
                "fabricated_markers": [m for m in emitted if m not in persisted],
            }

        answerable = [await ask(q) for q in _load(ANSWERABLE_PATH)]
        unanswerable = [await ask(q) for q in _load(UNANSWERABLE_PATH)]

        answered = [row for row in answerable if not row["abstained"]]
        cited = [row for row in answered if row["persisted_markers"]]
        abstained_ok = [row for row in unanswerable if row["abstained"]]
        fabricating = [
            row["id"] for row in unanswerable if not row["abstained"] and row["persisted_markers"]
        ]
        return {
            "answerable": {
                "questions": len(answerable),
                "cited_rate": round(len(cited) / len(answerable), 4),
                "false_abstention_rate": round(
                    (len(answerable) - len(answered)) / len(answerable), 4
                ),
                "target_cited_rate": TARGET_ANSWERABLE_CITED,
                "uncited_ids": [row["id"] for row in answered if not row["persisted_markers"]],
            },
            "unanswerable": {
                "questions": len(unanswerable),
                "abstention_rate": round(len(abstained_ok) / len(unanswerable), 4),
                "target_abstention_rate": TARGET_UNANSWERABLE_ABSTAINED,
                "fabricating_ids": fabricating,
            },
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", choices=("fake", "anthropic", "ollama"), default="fake")
    parser.add_argument("--embeddings", choices=("ollama",), default="ollama")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    chat: LLMProvider
    if args.chat == "anthropic":
        chat = AnthropicChatProvider(os.environ.get("ANTHROPIC_API_KEY"))
    elif args.chat == "ollama":
        chat = OllamaChatProvider(args.ollama_url)
    else:
        chat = _FakeChatProvider()
    embeddings = OllamaEmbeddingProvider(args.ollama_url)

    def run(url: str) -> dict[str, object]:
        run_migrations_sync(url)
        return asyncio.run(evaluate(url, chat, embeddings))

    if args.database_url:
        result = run(args.database_url)
    else:
        with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as container:
            result = run(container.get_connection_url())

    result["chat_provider"] = args.chat
    payload = json.dumps(result, indent=2)
    print(payload)
    if args.output:
        args.output.write_text(payload + "\n")
    return 0  # numbers are recorded, not gated — M11 pins the baseline


if __name__ == "__main__":
    sys.exit(main())
