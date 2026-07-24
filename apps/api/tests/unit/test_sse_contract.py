"""SSE wire contract (M07): event names and payload shapes pinned
against docs/12 §4.3.3, the pump's terminal-event guarantee, and the
tail encoding (§4.3.2). The M09 client is generated against these
exact shapes — drift here is a broken UI."""

import asyncio
import json
import random
from typing import cast
from uuid import UUID

import pytest

from atlas.ai import BreakerBoard, CostMeter, ModelRates, ModelRouter, ResilientExecutor
from atlas.ai.context import ContextAssembler
from atlas.ai.prompts import StaticPromptRegistry
from atlas.application.chat import ChatRuntime, ChatStreamEvent, StreamAnswer, StreamStarted
from atlas.domain.ai import ChatEvent, LLMProvider, ProviderError, ProviderUnavailable, Usage
from atlas.domain.conversation.entities import Conversation
from atlas.infrastructure.streams import BufferedEvent
from atlas.presentation.sse import encode_sse, pump_stream, sse_body, wire_event
from atlas.shared.ids import uuid7
from tests.fakes import (
    FakeDispatcher,
    FakeState,
    FakeUnitOfWork,
    MemoryStreamBuffer,
    ScriptedChatProvider,
)

SONNET = "claude-sonnet-5"

Script = list[ChatEvent | ProviderError | asyncio.Event]


async def _no_sleep(_seconds: float) -> None:
    return None


class Rig:
    def __init__(self) -> None:
        self.state = FakeState()
        self.workspace_id = uuid7()
        self.anthropic = ScriptedChatProvider("anthropic", is_local=False)
        self.buffer = MemoryStreamBuffer()
        providers: dict[str, LLMProvider] = {
            "anthropic": self.anthropic,
            "ollama": ScriptedChatProvider("ollama", is_local=True),
        }
        runtime = ChatRuntime(
            router=ModelRouter(),
            executor=ResilientExecutor(
                providers, BreakerBoard(lambda: 0.0), sleep=_no_sleep, rng=random.Random(7)
            ),
            assembler=ContextAssembler(),
            cost_meter=CostMeter({SONNET: ModelRates(3.00, 15.00)}),
            prompts=StaticPromptRegistry(),
            profile="hybrid",
        )
        self.stream = StreamAnswer(
            uow_factory=self.uow, runtime=runtime, dispatcher=FakeDispatcher()
        )

    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.state)

    async def seed_conversation(self) -> Conversation:
        conversation = Conversation(workspace_id=self.workspace_id, title="Chat")
        async with self.uow() as uow:
            await uow.conversations.add(conversation)
            await uow.commit()
        return conversation


async def _run_pumped_exchange(rig: Rig, script: Script) -> tuple[UUID, list[BufferedEvent]]:
    conversation = await rig.seed_conversation()
    rig.anthropic.streams = [script]
    events = rig.stream.execute(rig.workspace_id, conversation.id, "Pricing?", trace_id="t-1")
    started = await anext(events)
    assert isinstance(started, StreamStarted)
    await pump_stream(events, started, rig.buffer)
    return started.message_id, rig.buffer.events[started.message_id]


def _happy_script() -> Script:
    usage = Usage(input_tokens=3812, output_tokens=402)
    return [
        ChatEvent(type="text_delta", text="You anchored Pro "),
        ChatEvent(type="text_delta", text="at $12/mo."),
        ChatEvent(type="usage", usage=usage),
        ChatEvent(type="done", usage=usage),
    ]


class TestEventContract:
    async def test_success_sequence_and_payload_keys_are_pinned(self) -> None:
        rig = Rig()
        message_id, buffered = await _run_pumped_exchange(rig, _happy_script())

        assert [e.event for e in buffered] == [
            "message_start",
            "content_delta",
            "content_delta",
            "usage",
            "message_end",
        ]
        assert [e.index for e in buffered] == [0, 1, 2, 3, 4]  # monotonic resume cursor
        assert [e.terminal for e in buffered] == [False, False, False, False, True]

        start = json.loads(buffered[0].data)
        assert set(start) == {
            "message_id",
            "conversation_id",
            "model",
            "prompt_version",
            "trace_id",
            "created_at",
        }
        assert start["message_id"] == str(message_id)
        assert start["model"] == SONNET
        assert start["prompt_version"] == "chat.system.v1"
        assert start["trace_id"] == "t-1"

        delta = json.loads(buffered[1].data)
        assert set(delta) == {"index", "delta"}
        assert delta == {"index": 0, "delta": "You anchored Pro "}

        usage = json.loads(buffered[3].data)
        assert set(usage) == {
            "model",
            "provider",
            "prompt_version",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "latency_ms",
            "stop_reason",
        }
        assert usage["input_tokens"] == 3812
        assert usage["stop_reason"] == "end_turn"

        end = json.loads(buffered[4].data)
        assert set(end) == {"message_id", "stop_reason", "citation_count", "abstained"}
        assert end["message_id"] == str(message_id)
        assert end["abstained"] is False

    async def test_mid_stream_failure_ends_with_problem_coded_error(self) -> None:
        """M07 acceptance (fault injection): a killed provider yields a
        problem+json-coded SSE error event — never a hung stream."""
        rig = Rig()
        script: Script = [
            ChatEvent(type="text_delta", text="You anch"),
            ProviderUnavailable("connection died"),
        ]
        message_id, buffered = await _run_pumped_exchange(rig, script)

        assert [e.event for e in buffered] == ["message_start", "content_delta", "error"]
        assert buffered[-1].terminal is True
        problem = json.loads(buffered[-1].data)
        assert set(problem) == {"type", "title", "status", "code", "detail"}
        assert problem["code"] == "provider_unavailable"
        assert problem["status"] == 502
        assert problem["type"].endswith("/provider_unavailable")
        # Terminal state is finished + conversation released for retry.
        assert message_id in rig.buffer.finished
        assert rig.buffer.active == set()

    async def test_pump_always_finishes_and_releases(self) -> None:
        rig = Rig()
        message_id, _ = await _run_pumped_exchange(rig, _happy_script())
        assert message_id in rig.buffer.finished
        assert rig.buffer.active == set()

    def test_unknown_event_type_is_a_loud_bug(self) -> None:
        bogus = cast("ChatStreamEvent", object())  # deliberately not a stream event
        with pytest.raises(TypeError):
            wire_event(bogus)


class TestSseEncoding:
    def test_event_frame_shape(self) -> None:
        frame = encode_sse(BufferedEvent(index=3, event="content_delta", data='{"a":1}'))
        assert frame == b'id: 3\nevent: content_delta\ndata: {"a":1}\n\n'

    async def test_body_replays_after_cursor_then_stops_at_terminal(self) -> None:
        buffer = MemoryStreamBuffer()
        message_id = uuid7()
        for index, name in enumerate(["message_start", "content_delta", "usage", "message_end"]):
            await buffer.append(
                message_id,
                BufferedEvent(index=index, event=name, data="{}", terminal=name == "message_end"),
            )
        await buffer.finish(message_id)

        chunks = [chunk async for chunk in sse_body(buffer, message_id, after=1)]
        assert chunks[0] == b"retry: 3000\n\n"  # §4.3.2 preamble
        assert b"id: 2\nevent: usage" in chunks[1]
        assert b"id: 3\nevent: message_end" in chunks[2]
        assert len(chunks) == 3  # nothing after the terminal event

    async def test_disconnected_tail_does_not_stop_the_pump(self) -> None:
        """Detachment (§4.3.5): cancelling the tail must leave the pump
        producing; a reconnect replays what accumulated meanwhile."""
        rig = Rig()
        conversation = await rig.seed_conversation()
        gate = asyncio.Event()
        usage = Usage(input_tokens=10, output_tokens=5)
        rig.anthropic.streams = [
            [
                ChatEvent(type="text_delta", text="Hel"),
                gate,
                ChatEvent(type="text_delta", text="lo"),
                ChatEvent(type="done", usage=usage),
            ]
        ]
        events = rig.stream.execute(rig.workspace_id, conversation.id, "hi")
        started = await anext(events)
        assert isinstance(started, StreamStarted)
        pump = asyncio.create_task(pump_stream(events, started, rig.buffer))

        tail_gen = sse_body(rig.buffer, started.message_id)
        async for chunk in tail_gen:
            if b"content_delta" in chunk:
                break
        await tail_gen.aclose()  # the client walks away mid-stream

        gate.set()
        await asyncio.wait_for(pump, timeout=2.0)  # acceptance: bounded, not orphaned

        replayed = rig.buffer.events[started.message_id]
        assert [e.event for e in replayed][-1] == "message_end"
        async with rig.uow() as uow:
            messages = await uow.messages.list_for_conversation(rig.workspace_id, conversation.id)
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[1].content == "Hello"
