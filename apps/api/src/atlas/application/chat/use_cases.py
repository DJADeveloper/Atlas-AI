"""Chat use cases (M07; docs/12 §4.3, docs/20 §9, docs/22 §2).

Transaction shape for an exchange is two Units of Work, deliberately:
the user's message commits BEFORE the model is dialed (their words are
never hostage to a provider outage), and the assistant's message
commits after the answer completes, with usage accounting taken
verbatim from the provider (spine §12: what an answer cost is product
data). Between the two sits the AI runtime — assembler, router,
executor — which owns every resilience decision.

Context is packed for the PRIMARY rung's window. A fallback rung with
a smaller window (llama after sonnet) can therefore still refuse with
`ContextWindowExceeded`; that surfaces honestly rather than silently
re-packing a different, smaller prompt mid-conversation — docs/20 §9's
one-notch-tighter repack joins with the M08 grounded-prompt work.

Background work (summarization at the 70% trigger, title generation
after the first exchange) dispatches AFTER commit, fire-and-forget: a
lost task costs a nicety, never data.
"""

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from atlas.ai.context import AssembledContext, ContextAssembler, ContextSources, Turn
from atlas.ai.cost import CostMeter
from atlas.ai.executor import ResilientExecutor
from atlas.ai.prompts import PromptProvider, RegisteredPrompt
from atlas.ai.routing import ModelRouter, Profile, RoutePlan
from atlas.application.memory.use_cases import rank_memories
from atlas.application.ports import ChatDispatcher, SearchFilters, UnitOfWork
from atlas.application.retrieval import SearchResult
from atlas.domain.ai.errors import ProviderError
from atlas.domain.ai.provider import ChatRequest, ProviderChatMessage, StopReason, Usage
from atlas.domain.conversation.entities import Citation, Conversation, Message
from atlas.rag import (
    FUSED_RESULT_LIMIT,
    GroundedContext,
    GroundingChunk,
    MarkerAccumulator,
    extract_markers,
    select_grounding,
)
from atlas.shared.errors import NotFound
from atlas.shared.ids import uuid7

# Verbatim turns kept out of every fold: the model always sees the
# recent exchange as actually written, never only summarized.
KEEP_RECENT_TURNS = 6

# Ungrounded fallback prompt (no retriever wired, e.g. worker-side
# flows); the API path grounds every exchange with chat.grounded.
CHAT_PROMPT_NAME = "chat.system"
GROUNDED_PROMPT_NAME = "chat.grounded"

# The abstention answer is deterministic and free: no model runs when
# retrieval scored below threshold ("grounded or silent", spine SS2.4).
ABSTENTION_MODEL = "abstention"
ABSTENTION_PROVIDER = "atlas"
ABSTENTION_TEXT = (
    "I don't have anything in your indexed documents that answers this. "
    "Try rephrasing, or add the relevant files to a source and let "
    "indexing finish."
)


class Retriever(Protocol):
    """Structural seam for HybridSearch (M06) - chat depends on the
    shape, tests inject canned results."""

    async def execute(
        self,
        workspace_id: UUID,
        query: str,
        *,
        filters: SearchFilters | None = None,
        limit: int = FUSED_RESULT_LIMIT,
    ) -> list[SearchResult]: ...


MAX_TITLE_WORDS = 6


def _monotonic_ms() -> float:
    return time.monotonic() * 1000


@dataclass(frozen=True)
class CreateConversation:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(self, workspace_id: UUID, *, title: str | None = None) -> Conversation:
        conversation = Conversation(workspace_id=workspace_id, title=title)
        async with self.uow_factory() as uow:
            await uow.conversations.add(conversation)
            await uow.commit()
        return conversation


@dataclass(frozen=True)
class ListConversations:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(self, workspace_id: UUID, *, limit: int = 50) -> list[Conversation]:
        async with self.uow_factory() as uow:
            return await uow.conversations.list_recent(workspace_id, limit=limit)


@dataclass(frozen=True)
class ConversationDetail:
    conversation: Conversation
    messages: list[Message]


@dataclass(frozen=True)
class GetConversation:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(self, workspace_id: UUID, conversation_id: UUID) -> ConversationDetail:
        async with self.uow_factory() as uow:
            conversation = await uow.conversations.get(workspace_id, conversation_id)
            if conversation is None or conversation.is_deleted:
                raise NotFound(f"conversation {conversation_id} not found")
            messages = await uow.messages.list_for_conversation(workspace_id, conversation_id)
        return ConversationDetail(conversation=conversation, messages=messages)


@dataclass(frozen=True)
class ChatRuntime:
    """The AI-runtime bundle every chat use case shares (docs/20):
    routing, resilient execution, window packing, cost accounting, and
    the active profile. Composed once at the process root."""

    router: ModelRouter
    executor: ResilientExecutor
    assembler: ContextAssembler
    cost_meter: CostMeter
    prompts: PromptProvider
    profile: Profile
    abstain_below: float = 0.016  # see atlas.rag.grounding for the arithmetic


@dataclass(frozen=True)
class _PreparedExchange:
    conversation: Conversation
    user_message: Message
    assembled: AssembledContext
    plan: RoutePlan
    prompt: RegisteredPrompt
    grounded: GroundedContext
    citable: tuple[GroundingChunk, ...]  # entries that survived packing


@dataclass(frozen=True, slots=True)
class _Served:
    """Which rung answered, at what usage and wall-clock cost."""

    model: str
    provider: str
    usage: Usage
    latency_ms: int


class _ExchangeBase:
    """Shared first half of SendMessage/StreamAnswer: commit the user
    turn, recall memories, pack the window."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        runtime: ChatRuntime,
        dispatcher: ChatDispatcher,
        retriever: Retriever | None = None,
        clock_ms: Callable[[], float] = _monotonic_ms,
    ) -> None:
        self._uow_factory = uow_factory
        self._runtime = runtime
        self._dispatcher = dispatcher
        self._retriever = retriever
        self._clock_ms = clock_ms

    @property
    def _executor(self) -> ResilientExecutor:
        return self._runtime.executor

    @property
    def _profile(self) -> Profile:
        return self._runtime.profile

    async def _prepare(
        self, workspace_id: UUID, conversation_id: UUID, content: str, trace_id: str | None
    ) -> _PreparedExchange:
        plan = self._runtime.router.plan("chat", self._profile)
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(workspace_id, conversation_id)
            if conversation is None or conversation.is_deleted:
                raise NotFound(f"conversation {conversation_id} not found")
            history = await uow.messages.list_for_conversation(workspace_id, conversation_id)
            memories = await uow.memories.list_active(workspace_id)
            user_message = Message(
                conversation_id=conversation_id, role="user", content=content, trace_id=trace_id
            )
            await uow.messages.add(user_message)
            conversation.touch()
            await uow.conversations.save(conversation)
            await uow.commit()

        grounded = GroundedContext(entries=(), abstain=False)
        if self._retriever is not None:
            results = await self._retriever.execute(workspace_id, content)
            grounded = select_grounding(
                [
                    GroundingChunk(
                        chunk_id=result.chunk_id,
                        document_id=result.document_id,
                        text=result.text,
                        score=result.score,
                    )
                    for result in results
                ],
                abstain_below=self._runtime.abstain_below,
            )

        grounded_answering = self._retriever is not None and bool(grounded.entries)
        prompt = await self._runtime.prompts.get(
            GROUNDED_PROMPT_NAME if grounded_answering else CHAT_PROMPT_NAME
        )
        turns = _verbatim_turns(conversation, [*history, user_message])
        ranked = rank_memories(memories, content)
        assembled = self._runtime.assembler.assemble(
            model=plan.chain[0].model,
            max_tokens=plan.max_tokens,
            system_prompt=prompt.template,
            turns=turns,
            sources=ContextSources(
                memories=[f"[{memory.kind}] {memory.content}" for memory in ranked],
                chunks=[entry.text for entry in grounded.entries],
                summary=conversation.summary,
            ),
        )
        return _PreparedExchange(
            conversation=conversation,
            user_message=user_message,
            assembled=assembled,
            plan=plan,
            prompt=prompt,
            grounded=grounded,
            # The assembler keeps a prefix under budget pressure, so
            # markers never renumber - only the citable tail shrinks.
            citable=grounded.entries[: assembled.included_chunks],
        )

    async def _persist_answer(
        self,
        workspace_id: UUID,
        prepared: _PreparedExchange,
        answer: Message,
        citations: list[Citation] | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(workspace_id, answer.conversation_id)
            if conversation is None:  # deleted mid-stream: the answer has no home
                return
            await uow.messages.add(answer)
            if citations:
                await uow.citations.add_all(citations)
            conversation.touch()
            await uow.conversations.save(conversation)
            await uow.commit()
        if prepared.assembled.needs_summarization:
            self._dispatcher.dispatch_summarize(
                workspace_id, answer.conversation_id, answer.trace_id
            )
        if conversation.title is None:
            self._dispatcher.dispatch_title(workspace_id, answer.conversation_id, answer.trace_id)

    def _citations_for(
        self, prepared: _PreparedExchange, message_id: UUID, markers: list[int]
    ) -> list[Citation]:
        """Emitted markers -> rows. Only markers inside the offered
        evidence range become citations; a hallucinated [9] stays in
        the text for the eval to see but never fabricates a row."""
        return [
            Citation(
                message_id=message_id,
                chunk_id=prepared.citable[marker - 1].chunk_id,
                marker=marker,
                score=prepared.citable[marker - 1].score,
            )
            for marker in markers
            if 1 <= marker <= len(prepared.citable)
        ]

    async def _resolve_citation(
        self, workspace_id: UUID, prepared: _PreparedExchange, marker: int
    ) -> "StreamCitation":
        entry = prepared.citable[marker - 1]
        async with self._uow_factory() as uow:
            document = await uow.documents.get(workspace_id, entry.document_id)
        return StreamCitation(
            marker=marker,
            chunk_id=entry.chunk_id,
            document_id=entry.document_id,
            document_title=document.title if document is not None else None,
            source_id=document.source_id if document is not None else None,
            snippet=entry.text[:200],
            score=entry.score,
        )

    def _abstention_message(
        self, prepared: _PreparedExchange, message_id: UUID, trace_id: str | None
    ) -> Message:
        return Message(
            id=message_id,
            conversation_id=prepared.conversation.id,
            role="assistant",
            content=ABSTENTION_TEXT,
            abstained=True,
            model=ABSTENTION_MODEL,
            provider=ABSTENTION_PROVIDER,
            prompt_version_id=prepared.prompt.version_id,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=0,
            trace_id=trace_id,
        )

    def _answer_message(
        self,
        prepared: _PreparedExchange,
        served: _Served,
        *,
        message_id: UUID,
        content: str,
        trace_id: str | None,
    ) -> Message:
        return Message(
            id=message_id,
            conversation_id=prepared.conversation.id,
            role="assistant",
            content=content,
            model=served.model,
            provider=served.provider,
            prompt_version_id=prepared.prompt.version_id,
            input_tokens=served.usage.input_tokens,
            output_tokens=served.usage.output_tokens,
            cost_usd=self._runtime.cost_meter.cost_usd(served.model, served.usage),
            latency_ms=served.latency_ms,
            trace_id=trace_id,
        )


@dataclass(frozen=True)
class SendMessageResult:
    user_message: Message
    assistant_message: Message
    degraded: bool
    stop_reason: StopReason
    citations: list[Citation]
    abstained: bool


class SendMessage(_ExchangeBase):
    """The non-streaming exchange (background jobs, tests, CLI)."""

    async def execute(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        content: str,
        *,
        trace_id: str | None = None,
    ) -> SendMessageResult:
        prepared = await self._prepare(workspace_id, conversation_id, content, trace_id)
        if prepared.grounded.abstain:
            answer = self._abstention_message(prepared, uuid7(), trace_id)
            await self._persist_answer(workspace_id, prepared, answer)
            return SendMessageResult(
                user_message=prepared.user_message,
                assistant_message=answer,
                degraded=False,
                stop_reason="end_turn",
                citations=[],
                abstained=True,
            )
        request = ChatRequest(
            messages=prepared.assembled.messages, max_tokens=prepared.plan.max_tokens
        )
        started = self._clock_ms()
        result = await self._executor.complete(prepared.plan, request, profile=self._profile)
        latency_ms = int(self._clock_ms() - started)
        message_id = uuid7()
        content_text = result.response.message.content
        citations = self._citations_for(prepared, message_id, extract_markers(content_text))
        answer = self._answer_message(
            prepared,
            _Served(
                model=result.served_by.model,
                provider=result.served_by.provider,
                usage=result.response.usage,
                latency_ms=latency_ms,
            ),
            message_id=message_id,
            content=content_text,
            trace_id=trace_id,
        )
        await self._persist_answer(workspace_id, prepared, answer, citations)
        return SendMessageResult(
            user_message=prepared.user_message,
            assistant_message=answer,
            degraded=result.degraded,
            stop_reason=result.response.stop_reason,
            citations=citations,
            abstained=False,
        )


@dataclass(frozen=True, slots=True)
class StreamStarted:
    message_id: UUID
    conversation_id: UUID
    model: str
    provider: str
    prompt_version: str
    trace_id: str | None
    degraded: bool


@dataclass(frozen=True, slots=True)
class StreamDelta:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class StreamUsage:
    model: str
    provider: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    stop_reason: StopReason


@dataclass(frozen=True, slots=True)
class StreamCitation:
    """An [n] marker resolved against the offered evidence - emitted
    the moment the marker's closing bracket arrives in the stream."""

    marker: int
    chunk_id: UUID
    document_id: UUID
    document_title: str | None
    source_id: UUID | None
    snippet: str
    score: float


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    message: Message
    citation_count: int = 0


@dataclass(frozen=True, slots=True)
class StreamFailed:
    code: str
    title: str
    status: int
    detail: str
    partial_text: str


ChatStreamEvent = (
    StreamStarted | StreamDelta | StreamCitation | StreamUsage | StreamCompleted | StreamFailed
)


class StreamAnswer(_ExchangeBase):
    """The flagship streaming exchange (docs/12 §4.3).

    Yields typed application events the SSE layer maps 1:1 onto the
    wire protocol. Failure semantics follow docs/20 §5.2: before the
    first delta the executor already walked the chain, so a failure
    here means every rung failed; after deltas flowed, the failure is
    terminal (`StreamFailed`) and no assistant row is persisted — the
    stream record (M07-6) carries the failed state for resume, and the
    user's message is already safely committed.
    """

    async def execute(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        content: str,
        *,
        trace_id: str | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        prepared = await self._prepare(workspace_id, conversation_id, content, trace_id)
        if prepared.grounded.abstain:
            async for abstain_event in self._abstain_stream(workspace_id, prepared, trace_id):
                yield abstain_event
            return
        request = ChatRequest(
            messages=prepared.assembled.messages, max_tokens=prepared.plan.max_tokens
        )
        started = self._clock_ms()
        rung, degraded, events = await self._executor.start_stream(
            prepared.plan, request, profile=self._profile
        )
        message_id = uuid7()
        yield StreamStarted(
            message_id=message_id,
            conversation_id=conversation_id,
            model=rung.model,
            provider=rung.provider,
            prompt_version=prepared.prompt.label,
            trace_id=trace_id,
            degraded=degraded,
        )
        accumulator = MarkerAccumulator()
        usage: Usage | None = None
        index = 0
        try:
            async for event in events:
                if event.type == "text_delta" and event.text:
                    yield StreamDelta(index=index, text=event.text)
                    index += 1
                    for marker in accumulator.feed(event.text):
                        if 1 <= marker <= len(prepared.citable):
                            yield await self._resolve_citation(workspace_id, prepared, marker)
                elif event.type in ("usage", "done") and event.usage is not None:
                    usage = event.usage
        except ProviderError as error:
            yield StreamFailed(
                code=error.code,
                title=error.title,
                status=error.status,
                detail=error.detail,
                partial_text=accumulator.text,
            )
            return
        latency_ms = int(self._clock_ms() - started)
        if usage is None:  # a stream that never reported usage is broken
            yield StreamFailed(
                code="malformed_provider_response",
                title="Malformed provider response",
                status=502,
                detail="stream ended without usage accounting",
                partial_text=accumulator.text,
            )
            return
        citations = self._citations_for(prepared, message_id, list(accumulator.markers))
        answer = self._answer_message(
            prepared,
            _Served(model=rung.model, provider=rung.provider, usage=usage, latency_ms=latency_ms),
            message_id=message_id,
            content=accumulator.text,
            trace_id=trace_id,
        )
        await self._persist_answer(workspace_id, prepared, answer, citations)
        yield StreamUsage(
            model=rung.model,
            provider=rung.provider,
            prompt_version=prepared.prompt.label,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=answer.cost_usd if answer.cost_usd is not None else 0.0,
            latency_ms=latency_ms,
            stop_reason="end_turn",  # tool stops join with the M12 tool runtime
        )
        yield StreamCompleted(message=answer, citation_count=len(citations))

    async def _abstain_stream(
        self, workspace_id: UUID, prepared: _PreparedExchange, trace_id: str | None
    ) -> AsyncIterator[ChatStreamEvent]:
        """The grounded-or-silent path: no model runs, the answer is
        deterministic, and the abstained flag rides message_end so the
        UI can render honesty distinctly (M08 acceptance)."""
        answer = self._abstention_message(prepared, uuid7(), trace_id)
        yield StreamStarted(
            message_id=answer.id,
            conversation_id=prepared.conversation.id,
            model=ABSTENTION_MODEL,
            provider=ABSTENTION_PROVIDER,
            prompt_version=prepared.prompt.label,
            trace_id=trace_id,
            degraded=False,
        )
        yield StreamDelta(index=0, text=ABSTENTION_TEXT)
        await self._persist_answer(workspace_id, prepared, answer)
        yield StreamUsage(
            model=ABSTENTION_MODEL,
            provider=ABSTENTION_PROVIDER,
            prompt_version=prepared.prompt.label,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=0,
            stop_reason="end_turn",
        )
        yield StreamCompleted(message=answer, citation_count=0)


@dataclass(frozen=True)
class SummarizeOutcome:
    folded_turns: int


@dataclass(frozen=True)
class SummarizeConversation:
    """Fold everything older than the recent tail into the rolling
    summary (docs/22 §2) using the classification role — cheap, and
    local-only capable. Folding is additive: the original rows remain,
    so a bad summary is tunable, never destructive."""

    uow_factory: Callable[[], UnitOfWork]
    runtime: ChatRuntime
    keep_recent_turns: int = KEEP_RECENT_TURNS

    async def execute(self, workspace_id: UUID, conversation_id: UUID) -> SummarizeOutcome:
        async with self.uow_factory() as uow:
            conversation = await uow.conversations.get(workspace_id, conversation_id)
            if conversation is None or conversation.is_deleted:
                return SummarizeOutcome(folded_turns=0)
            history = await uow.messages.list_for_conversation(workspace_id, conversation_id)

        tail = _verbatim_messages(conversation, history)
        if len(tail) <= self.keep_recent_turns:
            return SummarizeOutcome(folded_turns=0)
        to_fold = tail[: -self.keep_recent_turns]

        transcript = "\n".join(f"{message.role}: {message.content}" for message in to_fold)
        prior = (
            f"Existing summary:\n{conversation.summary}\n\nNew turns:\n"
            if conversation.summary
            else ""
        )
        prompt = await self.runtime.prompts.get("conversation.summarize")
        plan = self.runtime.router.plan("classification", self.runtime.profile)
        request = ChatRequest(
            messages=[
                ProviderChatMessage(role="system", content=prompt.template),
                ProviderChatMessage(role="user", content=f"{prior}{transcript}"),
            ],
            max_tokens=plan.max_tokens,
        )
        result = await self.runtime.executor.complete(plan, request, profile=self.runtime.profile)
        summary = result.response.message.content.strip()
        if not summary:
            return SummarizeOutcome(folded_turns=0)

        async with self.uow_factory() as uow:
            conversation = await uow.conversations.get(workspace_id, conversation_id)
            if conversation is None or conversation.is_deleted:
                return SummarizeOutcome(folded_turns=0)
            conversation.fold_summary(summary, through_message_id=to_fold[-1].id)
            await uow.conversations.save(conversation)
            await uow.commit()
        return SummarizeOutcome(folded_turns=len(to_fold))


@dataclass(frozen=True)
class GenerateTitle:
    """Name a fresh conversation from its first exchange (docs/12
    §4.3) via the classification role. No-op once a title exists —
    user renames are never overwritten."""

    uow_factory: Callable[[], UnitOfWork]
    runtime: ChatRuntime

    async def execute(self, workspace_id: UUID, conversation_id: UUID) -> str | None:
        async with self.uow_factory() as uow:
            conversation = await uow.conversations.get(workspace_id, conversation_id)
            if conversation is None or conversation.is_deleted or conversation.title is not None:
                return None
            history = await uow.messages.list_for_conversation(
                workspace_id, conversation_id, limit=4
            )
        exchange = "\n".join(
            f"{message.role}: {message.content}"
            for message in history
            if message.role in ("user", "assistant")
        )
        if not exchange:
            return None
        prompt = await self.runtime.prompts.get("conversation.title")
        plan = self.runtime.router.plan("classification", self.runtime.profile)
        request = ChatRequest(
            messages=[
                ProviderChatMessage(role="system", content=prompt.template),
                ProviderChatMessage(role="user", content=exchange),
            ],
            max_tokens=plan.max_tokens,
        )
        result = await self.runtime.executor.complete(plan, request, profile=self.runtime.profile)
        title = _clean_title(result.response.message.content)
        if not title:
            return None
        async with self.uow_factory() as uow:
            conversation = await uow.conversations.get(workspace_id, conversation_id)
            if conversation is None or conversation.is_deleted or conversation.title is not None:
                return None
            conversation.rename(title)
            await uow.conversations.save(conversation)
            await uow.commit()
        return title


def _clean_title(raw: str) -> str:
    title = raw.strip().strip('"').strip("'").rstrip(".!")
    words = title.split()
    return " ".join(words[:MAX_TITLE_WORDS])


def _verbatim_messages(conversation: Conversation, history: list[Message]) -> list[Message]:
    """User/assistant turns after the summary watermark — UUIDv7 order
    makes "after" a plain id comparison."""
    watermark = conversation.summary_through_message_id
    return [
        message
        for message in history
        if message.role in ("user", "assistant")
        and (watermark is None or message.id.int > watermark.int)
    ]


def _verbatim_turns(conversation: Conversation, history: list[Message]) -> list[Turn]:
    return [
        Turn(role=message.role, content=message.content)
        for message in _verbatim_messages(conversation, history)
    ]


__all__ = [
    "ABSTENTION_TEXT",
    "KEEP_RECENT_TURNS",
    "ChatRuntime",
    "ChatStreamEvent",
    "ConversationDetail",
    "CreateConversation",
    "GenerateTitle",
    "GetConversation",
    "ListConversations",
    "SendMessage",
    "SendMessageResult",
    "StreamAnswer",
    "StreamCitation",
    "StreamCompleted",
    "StreamDelta",
    "StreamFailed",
    "StreamStarted",
    "StreamUsage",
    "SummarizeConversation",
    "SummarizeOutcome",
]
