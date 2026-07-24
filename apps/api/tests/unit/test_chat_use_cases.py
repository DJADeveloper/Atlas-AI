"""Chat use cases over fakes and scripted providers (M07): exchange
persistence with provider-exact accounting, degraded visibility, the
70% summarization trigger, streaming lifecycle, and the summarize/title
background folds (docs/12 §4.3, docs/20 §9, docs/22 §2)."""

import random
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from uuid import UUID

import pytest

from atlas.ai import BreakerBoard, CostMeter, ModelRates, ModelRouter, ResilientExecutor
from atlas.ai.context import ContextAssembler
from atlas.application.chat import (
    ChatRuntime,
    CreateConversation,
    GenerateTitle,
    GetConversation,
    ListConversations,
    SendMessage,
    StreamAnswer,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    StreamUsage,
    SummarizeConversation,
)
from atlas.application.chat.prompts import SUMMARIZE_PROMPT
from atlas.application.memory import RememberFact
from atlas.domain.ai import (
    ChatEvent,
    ChatRequest,
    ChatResponse,
    LLMProvider,
    ProviderChatMessage,
    ProviderError,
    ProviderUnavailable,
    Usage,
)
from atlas.domain.conversation.entities import Conversation, Message
from atlas.shared.errors import NotFound
from atlas.shared.ids import uuid7
from tests.fakes import FakeDispatcher, FakeState, FakeUnitOfWork

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"


def _response(
    model: str,
    provider: str,
    content: str = "An answer.",
    *,
    input_tokens: int = 3812,
    output_tokens: int = 402,
) -> ChatResponse:
    return ChatResponse(
        message=ProviderChatMessage(role="assistant", content=content),
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason="end_turn",
        model=model,
        provider=provider,
    )


class ScriptedChatProvider:
    """Scripted completions and streams; records every wire request so
    tests can assert on the packed context."""

    def __init__(self, name: str, *, is_local: bool) -> None:
        self._name = name
        self._is_local = is_local
        self.requests: list[tuple[str, ChatRequest]] = []
        self.responses: list[ChatResponse | ProviderError] = []
        self.streams: list[list[ChatEvent | ProviderError] | ProviderError] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return self._is_local

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse:
        self.requests.append((model, request))
        if not self.responses:
            raise ProviderUnavailable(f"{self._name}: no scripted response")
        item = self.responses.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return item

    def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        self.requests.append((model, request))
        script = self.streams.pop(0) if self.streams else ProviderUnavailable("no script")
        return self._play(script)

    async def _play(
        self, script: list[ChatEvent | ProviderError] | ProviderError
    ) -> AsyncIterator[ChatEvent]:
        if isinstance(script, ProviderError):
            raise script
        for item in script:
            if isinstance(item, ProviderError):
                raise item
            yield item


def _clock(values: list[float]) -> Callable[[], float]:
    queue = list(values)

    def read() -> float:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return read


@dataclass
class Rig:
    state: FakeState
    workspace_id: UUID
    anthropic: ScriptedChatProvider
    ollama: ScriptedChatProvider
    dispatcher: FakeDispatcher
    send: SendMessage
    stream: StreamAnswer
    summarize: SummarizeConversation
    title: GenerateTitle

    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.state)

    async def seed_conversation(
        self, *, title: str | None = "Chat", messages: list[Message] | None = None
    ) -> Conversation:
        conversation = Conversation(workspace_id=self.workspace_id, title=title)
        async with self.uow() as uow:
            await uow.conversations.add(conversation)
            for message in messages or []:
                await uow.messages.add(message)
            await uow.commit()
        return conversation


async def _no_sleep(_seconds: float) -> None:
    return None


def _rig(windows: dict[str, int] | None = None, clock_values: list[float] | None = None) -> Rig:
    state = FakeState()
    anthropic = ScriptedChatProvider("anthropic", is_local=False)
    ollama = ScriptedChatProvider("ollama", is_local=True)
    providers: dict[str, LLMProvider] = {"anthropic": anthropic, "ollama": ollama}
    executor = ResilientExecutor(
        providers, BreakerBoard(lambda: 0.0), sleep=_no_sleep, rng=random.Random(7)
    )
    router = ModelRouter()
    assembler = ContextAssembler(windows)
    cost_meter = CostMeter({SONNET: ModelRates(3.00, 15.00), HAIKU: ModelRates(1.00, 5.00)})
    dispatcher = FakeDispatcher()
    workspace_id = uuid7()

    def uow_factory() -> FakeUnitOfWork:
        return FakeUnitOfWork(state)

    runtime = ChatRuntime(
        router=router,
        executor=executor,
        assembler=assembler,
        cost_meter=cost_meter,
        profile="hybrid",
    )
    ticks = clock_values if clock_values is not None else [1000.0, 3916.0]
    return Rig(
        state=state,
        workspace_id=workspace_id,
        anthropic=anthropic,
        ollama=ollama,
        dispatcher=dispatcher,
        send=SendMessage(
            uow_factory=uow_factory,
            runtime=runtime,
            dispatcher=dispatcher,
            clock_ms=_clock(list(ticks)),
        ),
        stream=StreamAnswer(
            uow_factory=uow_factory,
            runtime=runtime,
            dispatcher=dispatcher,
            clock_ms=_clock(list(ticks)),
        ),
        summarize=SummarizeConversation(uow_factory=uow_factory, runtime=runtime),
        title=GenerateTitle(uow_factory=uow_factory, runtime=runtime),
    )


class TestConversationCrud:
    async def test_create_list_get(self) -> None:
        rig = _rig()
        created = await CreateConversation(rig.uow).execute(rig.workspace_id, title="Pricing")
        listed = await ListConversations(rig.uow).execute(rig.workspace_id)
        assert [c.id for c in listed] == [created.id]
        detail = await GetConversation(rig.uow).execute(rig.workspace_id, created.id)
        assert detail.conversation.title == "Pricing"
        assert detail.messages == []

    async def test_get_missing_raises_not_found(self) -> None:
        rig = _rig()
        with pytest.raises(NotFound):
            await GetConversation(rig.uow).execute(rig.workspace_id, uuid7())


class TestSendMessage:
    async def test_persists_exchange_with_provider_exact_accounting(self) -> None:
        """M07 acceptance: the persisted totals match provider-reported
        usage exactly - tokens verbatim, cost from the rates fixture."""
        rig = _rig()
        conversation = await rig.seed_conversation()
        rig.anthropic.responses = [_response(SONNET, "anthropic")]

        result = await rig.send.execute(
            rig.workspace_id, conversation.id, "What did we decide?", trace_id="t-1"
        )

        async with rig.uow() as uow:
            messages = await uow.messages.list_for_conversation(rig.workspace_id, conversation.id)
        assert [m.role for m in messages] == ["user", "assistant"]
        answer = messages[1]
        assert answer.id == result.assistant_message.id
        assert answer.model == SONNET
        assert answer.provider == "anthropic"
        assert answer.prompt_version == "chat_system.v1"
        assert answer.input_tokens == 3812
        assert answer.output_tokens == 402
        assert answer.cost_usd == round((3812 * 3.00 + 402 * 15.00) / 1_000_000, 6)  # 0.017466
        assert answer.latency_ms == 2916
        assert answer.trace_id == "t-1"
        assert result.degraded is False

    async def test_user_message_survives_total_provider_failure(self) -> None:
        """The user's words commit before the model is dialed."""
        rig = _rig()
        conversation = await rig.seed_conversation()
        # No scripted responses anywhere: every rung fails.
        with pytest.raises(ProviderError):
            await rig.send.execute(rig.workspace_id, conversation.id, "hello?")
        async with rig.uow() as uow:
            messages = await uow.messages.list_for_conversation(rig.workspace_id, conversation.id)
        assert [m.role for m in messages] == ["user"]

    async def test_fallback_answer_is_marked_degraded(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation()
        rig.anthropic.responses = [
            ProviderUnavailable("down"),
            ProviderUnavailable("down"),
            ProviderUnavailable("down"),
            _response(HAIKU, "anthropic"),
        ]
        result = await rig.send.execute(rig.workspace_id, conversation.id, "hello")
        assert result.degraded is True
        assert result.assistant_message.model == HAIKU
        assert result.assistant_message.cost_usd == round((3812 * 1.00 + 402 * 5.00) / 1_000_000, 6)

    async def test_context_packs_memories_history_and_summary(self) -> None:
        rig = _rig()
        conversation = Conversation(workspace_id=rig.workspace_id, title="Chat")
        pre_watermark = Message(
            conversation_id=conversation.id, role="user", content="folded away turn"
        )
        recent = Message(conversation_id=conversation.id, role="assistant", content="Recent reply")
        conversation.fold_summary(
            "Earlier: the notice period is 30 days.", through_message_id=pre_watermark.id
        )
        async with rig.uow() as uow:
            await uow.conversations.add(conversation)
            await uow.messages.add(pre_watermark)
            await uow.messages.add(recent)
            await uow.commit()

        await RememberFact(rig.uow).execute(
            rig.workspace_id, "preference", "answer in short sentences"
        )
        rig.anthropic.responses = [_response(SONNET, "anthropic")]
        await rig.send.execute(rig.workspace_id, conversation.id, "and the fee?")

        model, request = rig.anthropic.requests[-1]
        assert model == SONNET
        system = request.messages[0].content
        assert "[preference] answer in short sentences" in system
        assert "Earlier: the notice period is 30 days." in system
        replayed = [m.content for m in request.messages[1:]]
        assert replayed == ["Recent reply", "and the fee?"]  # watermark excluded the folded turn

    async def test_summarization_dispatches_over_the_threshold(self) -> None:
        rig = _rig(windows={SONNET: 5300})  # budget 674 after max_tokens+margin
        conversation = await rig.seed_conversation()
        filler = " ".join("word" for _ in range(400))
        async with rig.uow() as uow:
            await uow.messages.add(
                Message(conversation_id=conversation.id, role="assistant", content=filler)
            )
            await uow.commit()
        rig.anthropic.responses = [_response(SONNET, "anthropic")]
        await rig.send.execute(rig.workspace_id, conversation.id, " ".join(["q"] * 100))
        assert [entry[1] for entry in rig.dispatcher.summarize_dispatched] == [conversation.id]

    async def test_below_threshold_does_not_dispatch_summarization(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation()
        rig.anthropic.responses = [_response(SONNET, "anthropic")]
        await rig.send.execute(rig.workspace_id, conversation.id, "short question")
        assert rig.dispatcher.summarize_dispatched == []

    async def test_untitled_conversation_dispatches_title_generation(self) -> None:
        rig = _rig()
        untitled = await rig.seed_conversation(title=None)
        rig.anthropic.responses = [_response(SONNET, "anthropic")]
        await rig.send.execute(rig.workspace_id, untitled.id, "hello")
        assert [entry[1] for entry in rig.dispatcher.title_dispatched] == [untitled.id]

        titled = await rig.seed_conversation(title="Named already")
        rig.anthropic.responses = [_response(SONNET, "anthropic")]
        await rig.send.execute(rig.workspace_id, titled.id, "hello")
        assert [entry[1] for entry in rig.dispatcher.title_dispatched] == [untitled.id]

    async def test_missing_conversation_raises_not_found(self) -> None:
        rig = _rig()
        with pytest.raises(NotFound):
            await rig.send.execute(rig.workspace_id, uuid7(), "hello")


class TestStreamAnswer:
    async def test_happy_path_streams_then_persists(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation()
        usage = Usage(input_tokens=3812, output_tokens=402)
        rig.anthropic.streams = [
            [
                ChatEvent(type="text_delta", text="You anchored Pro "),
                ChatEvent(type="text_delta", text="at $12/mo."),
                ChatEvent(type="usage", usage=usage),
                ChatEvent(type="done", usage=usage),
            ]
        ]

        events = [
            event
            async for event in rig.stream.execute(
                rig.workspace_id, conversation.id, "Pricing?", trace_id="t-9"
            )
        ]

        started = events[0]
        assert isinstance(started, StreamStarted)
        assert started.model == SONNET
        assert started.degraded is False
        deltas = [event for event in events if isinstance(event, StreamDelta)]
        assert [d.index for d in deltas] == [0, 1]
        usage_event = next(event for event in events if isinstance(event, StreamUsage))
        assert usage_event.input_tokens == 3812
        assert usage_event.cost_usd == round((3812 * 3.00 + 402 * 15.00) / 1_000_000, 6)
        assert usage_event.latency_ms == 2916
        completed = events[-1]
        assert isinstance(completed, StreamCompleted)
        assert completed.message.id == started.message_id
        assert completed.message.content == "You anchored Pro at $12/mo."

        async with rig.uow() as uow:
            messages = await uow.messages.list_for_conversation(rig.workspace_id, conversation.id)
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[1].content == "You anchored Pro at $12/mo."
        assert messages[1].cost_usd == round((3812 * 3.00 + 402 * 15.00) / 1_000_000, 6)

    async def test_mid_stream_failure_yields_typed_error_and_persists_no_answer(self) -> None:
        """M07 acceptance (fault injection): killing the provider mid-
        stream yields a problem-coded error event, never a hung stream."""
        rig = _rig()
        conversation = await rig.seed_conversation()
        rig.anthropic.streams = [
            [
                ChatEvent(type="text_delta", text="You anch"),
                ProviderUnavailable("connection died"),
            ]
        ]

        events = [
            event
            async for event in rig.stream.execute(rig.workspace_id, conversation.id, "Pricing?")
        ]

        failed = events[-1]
        assert isinstance(failed, StreamFailed)
        assert failed.code == "provider_unavailable"
        assert failed.status == 502
        assert failed.partial_text == "You anch"
        async with rig.uow() as uow:
            messages = await uow.messages.list_for_conversation(rig.workspace_id, conversation.id)
        assert [m.role for m in messages] == ["user"]  # no half answer persisted

    async def test_pre_first_byte_failure_falls_back_and_marks_degraded(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation()
        usage = Usage(input_tokens=10, output_tokens=5)
        rig.anthropic.streams = [
            ProviderUnavailable("sonnet down"),  # sonnet: dies before any byte
            [
                ChatEvent(type="text_delta", text="hi"),
                ChatEvent(type="done", usage=usage),
            ],
        ]
        events = [
            event async for event in rig.stream.execute(rig.workspace_id, conversation.id, "hello")
        ]
        started = events[0]
        assert isinstance(started, StreamStarted)
        assert started.model == HAIKU
        assert started.degraded is True
        assert isinstance(events[-1], StreamCompleted)


class TestSummarizeConversation:
    async def test_folds_old_turns_and_context_carries_the_early_fact(self) -> None:
        """M07 acceptance fixture: after summarization the packed
        context still carries a fact stated in the earliest turns."""
        rig = _rig()
        conversation = await rig.seed_conversation()
        turns = [
            Message(
                conversation_id=conversation.id,
                role="user",
                content="For Meridian: the notice period is 30 days.",
            )
        ]
        for i in range(9):
            turns.append(
                Message(
                    conversation_id=conversation.id,
                    role="assistant" if i % 2 == 0 else "user",
                    content=f"filler turn {i}",
                )
            )
        async with rig.uow() as uow:
            for message in turns:
                await uow.messages.add(message)
            await uow.commit()

        rig.anthropic.responses = [
            _response(HAIKU, "anthropic", "Summary: the Meridian notice period is 30 days.")
        ]
        outcome = await rig.summarize.execute(rig.workspace_id, conversation.id)
        assert outcome.folded_turns == 4  # 10 turns, keep 6 verbatim

        model, request = rig.anthropic.requests[-1]
        assert model == HAIKU
        assert request.messages[0].content == SUMMARIZE_PROMPT
        assert "notice period is 30 days" in request.messages[1].content

        async with rig.uow() as uow:
            stored = await uow.conversations.get(rig.workspace_id, conversation.id)
        assert stored is not None
        assert stored.summary == "Summary: the Meridian notice period is 30 days."
        assert stored.summary_through_message_id == turns[3].id

        # The next exchange packs the summary, not the folded turns.
        rig.anthropic.responses = [_response(SONNET, "anthropic")]
        await rig.send.execute(rig.workspace_id, conversation.id, "What was the notice period?")
        _, chat_request = rig.anthropic.requests[-1]
        system = chat_request.messages[0].content
        assert "the Meridian notice period is 30 days" in system
        replayed = [m.content for m in chat_request.messages[1:]]
        assert "For Meridian: the notice period is 30 days." not in replayed
        assert replayed[-1] == "What was the notice period?"

    async def test_short_conversations_are_left_alone(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation()
        async with rig.uow() as uow:
            for i in range(4):
                await uow.messages.add(
                    Message(conversation_id=conversation.id, role="user", content=f"t{i}")
                )
            await uow.commit()
        outcome = await rig.summarize.execute(rig.workspace_id, conversation.id)
        assert outcome.folded_turns == 0
        assert rig.anthropic.requests == []  # no model dialed

    async def test_refold_merges_the_existing_summary(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation()
        first_batch = [
            Message(conversation_id=conversation.id, role="user", content=f"early {i}")
            for i in range(10)
        ]
        async with rig.uow() as uow:
            for message in first_batch:
                await uow.messages.add(message)
            await uow.commit()
        rig.anthropic.responses = [_response(HAIKU, "anthropic", "First summary.")]
        await rig.summarize.execute(rig.workspace_id, conversation.id)

        async with rig.uow() as uow:
            for i in range(6):
                await uow.messages.add(
                    Message(conversation_id=conversation.id, role="user", content=f"later {i}")
                )
            await uow.commit()
        rig.anthropic.responses = [_response(HAIKU, "anthropic", "Merged summary.")]
        outcome = await rig.summarize.execute(rig.workspace_id, conversation.id)
        assert outcome.folded_turns == 6  # 12 unfolded turns, keep 6

        _, request = rig.anthropic.requests[-1]
        assert "Existing summary:\nFirst summary." in request.messages[1].content
        async with rig.uow() as uow:
            stored = await uow.conversations.get(rig.workspace_id, conversation.id)
        assert stored is not None
        assert stored.summary == "Merged summary."


class TestGenerateTitle:
    async def test_titles_a_fresh_conversation(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation(title=None)
        async with rig.uow() as uow:
            await uow.messages.add(
                Message(conversation_id=conversation.id, role="user", content="Pro pricing?")
            )
            await uow.commit()
        rig.anthropic.responses = [_response(HAIKU, "anthropic", '"Pro Pricing Decision."')]
        title = await rig.title.execute(rig.workspace_id, conversation.id)
        assert title == "Pro Pricing Decision"
        async with rig.uow() as uow:
            stored = await uow.conversations.get(rig.workspace_id, conversation.id)
        assert stored is not None
        assert stored.title == "Pro Pricing Decision"

    async def test_existing_titles_are_never_overwritten(self) -> None:
        rig = _rig()
        conversation = await rig.seed_conversation(title="My name")
        title = await rig.title.execute(rig.workspace_id, conversation.id)
        assert title is None
        assert rig.anthropic.requests == []
