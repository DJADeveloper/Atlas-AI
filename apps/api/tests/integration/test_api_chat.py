"""M07 chat API over the real ASGI app, Postgres, and Redis: the SSE
lifecycle (docs/12 §4.3), Redis Stream detachment + resume (§4.3.5),
idempotent re-attach, the one-stream guard, and memories CRUD. Chat
providers are scripted fakes swapped into the container — the wire
protocol, buffers, and persistence underneath are real."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, replace
from uuid import UUID

import httpx
import pytest

from atlas.ai import BreakerBoard, CostMeter, ModelRouter, ResilientExecutor
from atlas.ai.context import ContextAssembler
from atlas.application.chat import ChatRuntime
from atlas.config.settings import Settings
from atlas.domain.ai import ChatEvent, LLMProvider, ProviderError, ProviderUnavailable, Usage
from atlas.domain.knowledge.entities import Chunk, Document, DocumentVersion, Source
from atlas.domain.knowledge.values import ContentHash
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.prompts import SqlPromptRegistry
from atlas.infrastructure.persistence.tables import EMBEDDING_DIM
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork
from atlas.infrastructure.streams import RETENTION_SECONDS
from atlas.presentation.app import create_app
from atlas.presentation.composition import Container
from tests.conftest import app_client
from tests.fakes import FakeEmbeddingProvider, ScriptedChatProvider

pytestmark = pytest.mark.integration

SONNET = "claude-sonnet-5"


@dataclass
class SseEvent:
    id: int
    event: str
    data: dict[str, object]


def _parse_sse(raw: str) -> list[SseEvent]:
    events: list[SseEvent] = []
    for block in raw.split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith(":")]
        fields = dict(line.split(": ", 1) for line in lines if ": " in line)
        if "event" in fields and "data" in fields:
            events.append(
                SseEvent(
                    id=int(fields["id"]), event=fields["event"], data=json.loads(fields["data"])
                )
            )
    return events


Script = list[ChatEvent | ProviderError | asyncio.Event]


def _happy_script(text_parts: list[str]) -> Script:
    usage = Usage(input_tokens=3812, output_tokens=402)
    deltas: Script = [ChatEvent(type="text_delta", text=part) for part in text_parts]
    return [*deltas, ChatEvent(type="usage", usage=usage), ChatEvent(type="done", usage=usage)]


class ChatApi:
    def __init__(
        self, http: httpx.AsyncClient, container: Container, anthropic: ScriptedChatProvider
    ) -> None:
        self.http = http
        self.container = container
        self.anthropic = anthropic

    async def create_conversation(self, title: str | None = "Chat") -> str:
        response = await self.http.post("/api/v1/conversations", json={"title": title})
        assert response.status_code == 201, response.text
        conversation_id: str = response.json()["id"]
        return conversation_id

    async def stream_message(
        self, conversation_id: str, content: str, *, key: str
    ) -> tuple[int, list[SseEvent]]:
        raw = ""
        async with self.http.stream(
            "POST",
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": content},
            headers={"Idempotency-Key": key},
        ) as response:
            status = response.status_code
            if status != 200:
                await response.aread()
                return status, []
            assert response.headers["content-type"].startswith("text/event-stream")
            async for chunk in response.aiter_text():
                raw += chunk
        return status, _parse_sse(raw)


@pytest.fixture
async def api(migrated_database_url: str, redis_url: str) -> AsyncIterator[ChatApi]:
    settings = Settings(
        database_url=migrated_database_url, redis_url=redis_url, readiness_timeout_seconds=5.0
    )
    app = create_app(settings)
    async for http in app_client(app):
        container = app.state.container
        anthropic = ScriptedChatProvider("anthropic", is_local=False)
        providers: dict[str, LLMProvider] = {
            "anthropic": anthropic,
            "ollama": ScriptedChatProvider("ollama", is_local=True),
        }
        runtime = ChatRuntime(
            router=ModelRouter(),
            executor=ResilientExecutor(
                providers, BreakerBoard(time.monotonic), sleep=asyncio.sleep
            ),
            assembler=ContextAssembler(),
            cost_meter=CostMeter(),
            prompts=SqlPromptRegistry(container.session_factory),
            profile="hybrid",
        )
        app.state.container = replace(
            container,
            chat_runtime=runtime,
            embedding_provider=FakeEmbeddingProvider(dimensions=EMBEDDING_DIM),
        )
        yield ChatApi(http, app.state.container, anthropic)


class TestConversationEndpoints:
    async def test_create_list_get_roundtrip(self, api: ChatApi) -> None:
        conversation_id = await api.create_conversation("Pricing")
        listed = await api.http.get("/api/v1/conversations")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [conversation_id]
        detail = await api.http.get(f"/api/v1/conversations/{conversation_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["title"] == "Pricing"
        assert body["message_count"] == 0
        assert body["last_message_at"] is None

    async def test_missing_conversation_is_problem_json(self, api: ChatApi) -> None:
        response = await api.http.get("/api/v1/conversations/019f8a3c-6b21-7d4e-8a2f-3c9d1e5b7a01")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


class TestSseLifecycle:
    async def test_full_stream_then_persistence(self, api: ChatApi) -> None:
        await _seed_corpus(api)  # retrieval must ground, not abstain
        conversation_id = await api.create_conversation()
        api.anthropic.streams = [_happy_script(["You anchored Pro ", "at $12/mo."])]

        status, events = await api.stream_message(conversation_id, "Pricing?", key="k-1")

        assert status == 200
        assert [e.event for e in events] == [
            "message_start",
            "content_delta",
            "content_delta",
            "usage",
            "message_end",
        ]
        assert [e.id for e in events] == [0, 1, 2, 3, 4]
        assert events[0].data["model"] == SONNET
        deltas = [e.data["delta"] for e in events if e.event == "content_delta"]
        assert "".join(str(d) for d in deltas) == "You anchored Pro at $12/mo."
        assert events[3].data["input_tokens"] == 3812

        messages = await api.http.get(f"/api/v1/conversations/{conversation_id}/messages")
        items = messages.json()["items"]
        assert [item["role"] for item in items] == ["user", "assistant"]
        assert items[1]["content"] == "You anchored Pro at $12/mo."
        assert items[1]["usage"]["input_tokens"] == 3812
        assert items[1]["usage"]["cost_usd"] is not None

        detail = await api.http.get(f"/api/v1/conversations/{conversation_id}")
        assert detail.json()["message_count"] == 2
        assert detail.json()["last_message_at"] is not None

    async def test_resume_replays_from_last_event_id(self, api: ChatApi) -> None:
        await _seed_corpus(api)
        conversation_id = await api.create_conversation()
        api.anthropic.streams = [_happy_script(["Hel", "lo"])]
        _, events = await api.stream_message(conversation_id, "hi", key="k-resume")
        message_id = events[0].data["message_id"]

        raw = ""
        async with api.http.stream(
            "GET", f"/api/v1/messages/{message_id}/stream", headers={"Last-Event-ID": "1"}
        ) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_text():
                raw += chunk
        replayed = _parse_sse(raw)
        assert [e.id for e in replayed] == [2, 3, 4]  # only events after the cursor
        assert replayed[-1].event == "message_end"

        # The buffer carries the 15-minute retention TTL (§4.3.5).
        ttl = await api.container.redis.ttl(f"atlas:chat:stream:{message_id}")
        assert 0 < ttl <= RETENTION_SECONDS

    async def test_unknown_stream_is_not_found(self, api: ChatApi) -> None:
        response = await api.http.get(
            "/api/v1/messages/019f8a3c-72b8-7f60-8c4b-5e1f3a7d9c23/stream"
        )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_replayed_idempotency_key_reattaches(self, api: ChatApi) -> None:
        await _seed_corpus(api)
        conversation_id = await api.create_conversation()
        api.anthropic.streams = [_happy_script(["one answer"])]
        _, first = await api.stream_message(conversation_id, "hi", key="same-key")
        dialed = len(api.anthropic.requests)

        _, second = await api.stream_message(conversation_id, "hi", key="same-key")
        assert len(api.anthropic.requests) == dialed  # no second generation
        assert [e.event for e in second] == [e.event for e in first]
        assert second[0].data["message_id"] == first[0].data["message_id"]

    async def test_missing_idempotency_key_is_validation_error(self, api: ChatApi) -> None:
        conversation_id = await api.create_conversation()
        response = await api.http.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"content": "hi"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    async def test_second_stream_while_active_conflicts(self, api: ChatApi) -> None:
        conversation_id = await api.create_conversation()
        claimed = await api.container.stream_buffer.try_claim_conversation(UUID(conversation_id))
        assert claimed
        try:
            status, _ = await api.stream_message(conversation_id, "hi", key="k-blocked")
            assert status == 409
        finally:
            await api.container.stream_buffer.release_conversation(UUID(conversation_id))

    async def test_pre_stream_failure_is_problem_json(self, api: ChatApi) -> None:
        response = await api.http.post(
            "/api/v1/conversations/019f8a3c-6b21-7d4e-8a2f-3c9d1e5b7a01/messages",
            json={"content": "hi"},
            headers={"Idempotency-Key": "k-404"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_mid_stream_fault_yields_error_event_and_no_answer_row(
        self, api: ChatApi
    ) -> None:
        """M07 acceptance: kill the provider mid-stream → problem-coded
        SSE error event, no half answer persisted, never a hung stream."""
        await _seed_corpus(api)
        conversation_id = await api.create_conversation()
        api.anthropic.streams = [
            [ChatEvent(type="text_delta", text="You anch"), ProviderUnavailable("killed")]
        ]
        status, events = await api.stream_message(conversation_id, "hi", key="k-fault")
        assert status == 200  # failure happened after headers, inside the stream
        assert [e.event for e in events] == ["message_start", "content_delta", "error"]
        problem = events[-1].data
        assert problem["code"] == "provider_unavailable"
        assert problem["status"] == 502

        messages = await api.http.get(f"/api/v1/conversations/{conversation_id}/messages")
        assert [item["role"] for item in messages.json()["items"]] == ["user"]

    async def test_disconnect_detaches_generation_and_persists(self, api: ChatApi) -> None:
        """M07 acceptance + §4.3.5: the client walks away mid-stream;
        the tail dies, generation finishes in the background within the
        2 s budget, the full answer is persisted, and the buffered
        stream remains resumable.

        httpx's ASGITransport buffers streaming bodies until the app
        completes, so the walk-away is expressed by cancelling the
        request task — the same CancelledError a dropped socket
        delivers to the response generator."""
        await _seed_corpus(api)
        conversation_id = await api.create_conversation()
        gate = asyncio.Event()
        usage = Usage(input_tokens=10, output_tokens=5)
        api.anthropic.streams = [
            [
                ChatEvent(type="text_delta", text="Hel"),
                gate,
                ChatEvent(type="text_delta", text="lo"),
                ChatEvent(type="done", usage=usage),
            ]
        ]

        request = asyncio.create_task(
            api.http.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "hi"},
                headers={"Idempotency-Key": "k-detach"},
            )
        )
        # Wait until generation demonstrably started (message_start +
        # first delta in the Redis buffer), then hang up.
        message_id = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            message_id = await api.container.stream_buffer.recall_message_for(
                f"{conversation_id}:k-detach"
            )
            if (
                message_id is not None
                and await api.container.redis.xlen(f"atlas:chat:stream:{message_id}") >= 2
            ):
                break
            await asyncio.sleep(0.02)
        assert message_id is not None, "stream never started"
        request.cancel()
        with suppress(asyncio.CancelledError):
            await request

        gate.set()  # the provider produces the rest after the client left
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            messages = await api.http.get(f"/api/v1/conversations/{conversation_id}/messages")
            items = messages.json()["items"]
            if [item["role"] for item in items] == ["user", "assistant"]:
                assert items[1]["content"] == "Hello"
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("assistant answer was not persisted within 2s of disconnect")

        # The abandoned stream is still resumable from the buffer.
        raw = ""
        async with api.http.stream("GET", f"/api/v1/messages/{message_id}/stream") as response:
            assert response.status_code == 200
            async for chunk in response.aiter_text():
                raw += chunk
        replayed = _parse_sse(raw)
        assert [e.event for e in replayed][-1] == "message_end"
        deltas = [e.data["delta"] for e in replayed if e.event == "content_delta"]
        assert "".join(str(d) for d in deltas) == "Hello"


class TestMemoriesCrud:
    async def test_full_crud_roundtrip(self, api: ChatApi) -> None:
        created = await api.http.post(
            "/api/v1/memories",
            json={"kind": "decision", "content": "We chose Postgres over SQLite."},
        )
        assert created.status_code == 201, created.text
        memory_id = created.json()["id"]

        listed = await api.http.get("/api/v1/memories")
        assert [item["id"] for item in listed.json()["items"]] == [memory_id]

        revised = await api.http.patch(
            f"/api/v1/memories/{memory_id}", json={"content": "Postgres, reaffirmed."}
        )
        assert revised.status_code == 200
        assert revised.json()["content"] == "Postgres, reaffirmed."

        deleted = await api.http.delete(f"/api/v1/memories/{memory_id}")
        assert deleted.status_code == 204
        assert (await api.http.get("/api/v1/memories")).json()["items"] == []

    async def test_project_facts_are_rejected_until_projects_exist(self, api: ChatApi) -> None:
        response = await api.http.post(
            "/api/v1/memories", json={"kind": "project_fact", "content": "fee is 50k"}
        )
        assert response.status_code == 422


CITED_TEXT = "The Meridian contract notice period is 30 days."


async def _seed_corpus(api: ChatApi) -> None:
    """One embedded chunk in the DEFAULT workspace so retrieval has
    evidence; vectors come from the same fake the app now embeds
    queries with (the test_api_search pattern)."""
    container = api.container
    workspace_id = await ensure_default_workspace(container.session_factory)
    source = Source(workspace_id=workspace_id, kind="folder", name="Docs", uri="/seeded-chat")
    document = Document(source_id=source.id, path="meridian.md", mime_type="text/markdown")
    document.title = "meridian.md"
    version = DocumentVersion(
        document_id=document.id, content_hash=ContentHash("b" * 64), size_bytes=1, parser="md"
    )
    vectors = await container.embedding_provider.embed_documents([CITED_TEXT])
    chunk = Chunk(
        document_version_id=version.id,
        ordinal=0,
        text=CITED_TEXT,
        token_count=8,
        content_hash=ContentHash("e" * 64),
        embedding=vectors[0],
        embedding_model=container.embedding_provider.model,
    )
    async with SqlAlchemyUnitOfWork(container.session_factory) as uow:
        await uow.sources.add(source)
        await uow.documents.add(document)
        await uow.document_versions.add(version)
        await uow.chunks.add_all([chunk])
        document.set_current_version(version.id)  # flip after the version exists
        await uow.documents.save(document)
        await uow.commit()


class TestGroundedAnswers:
    async def test_cited_answer_end_to_end(self, api: ChatApi) -> None:
        """M08 acceptance path: question → grounded prompt → [n] in the
        stream → citation SSE event → persisted citations rows pointing
        at real chunks → resolved citations on the message list → a
        non-null prompt_version_id in the database."""
        await _seed_corpus(api)
        conversation_id = await api.create_conversation()
        api.anthropic.streams = [_happy_script(["The notice period is 30 days [1", "]."])]

        status, events = await api.stream_message(
            conversation_id, "What is the Meridian notice period?", key="k-cite"
        )

        assert status == 200
        names = [e.event for e in events]
        assert names == [
            "message_start",
            "content_delta",
            "content_delta",
            "citation",
            "usage",
            "message_end",
        ]
        citation = next(e for e in events if e.event == "citation")
        assert citation.data["marker"] == 1
        assert citation.data["document_title"] == "meridian.md"
        end = events[-1]
        assert end.data["citation_count"] == 1
        assert end.data["abstained"] is False

        # Persisted rows resolve to the real chunk, and the view carries them.
        messages = await api.http.get(f"/api/v1/conversations/{conversation_id}/messages")
        items = messages.json()["items"]
        assert items[1]["citations"][0]["marker"] == 1
        assert items[1]["citations"][0]["document_title"] == "meridian.md"
        assert CITED_TEXT.startswith(items[1]["citations"][0]["snippet"][:20])

        # M08 acceptance: the chat path records a non-null prompt_version_id.
        workspace_id = await ensure_default_workspace(api.container.session_factory)
        async with SqlAlchemyUnitOfWork(api.container.session_factory) as uow:
            stored = await uow.messages.get(workspace_id, UUID(items[1]["id"]))
        assert stored is not None
        assert stored.prompt_version_id is not None

    async def test_unanswerable_question_abstains_without_a_model(self, api: ChatApi) -> None:
        """Empty index → threshold abstention: flagged message_end,
        zero citations, zero provider dials, honest persisted row."""
        conversation_id = await api.create_conversation()
        dialed_before = len(api.anthropic.requests)

        status, events = await api.stream_message(
            conversation_id, "What is the quarterly synergy cadence?", key="k-abstain"
        )

        assert status == 200
        assert len(api.anthropic.requests) == dialed_before  # no model ran
        start = events[0]
        assert start.data["model"] == "abstention"
        end = events[-1]
        assert end.event == "message_end"
        assert end.data["abstained"] is True
        assert end.data["citation_count"] == 0

        messages = await api.http.get(f"/api/v1/conversations/{conversation_id}/messages")
        items = messages.json()["items"]
        assert items[1]["abstained"] is True
        assert items[1]["citations"] == []
