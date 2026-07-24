"""M07 persistence: conversations, messages, memories over real
Postgres — round trips, chronological order, accounting precision, and
workspace scoping."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from atlas.domain.conversation import Citation, Conversation, Message
from atlas.domain.knowledge.entities import Chunk, Document, DocumentVersion, Source
from atlas.domain.knowledge.values import ContentHash
from atlas.domain.memory import Memory
from atlas.infrastructure.persistence.bootstrap import ensure_default_workspace
from atlas.infrastructure.persistence.prompts import SqlPromptRegistry
from atlas.infrastructure.persistence.tables import ChunkRow
from atlas.infrastructure.persistence.uow import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


class Rig:
    def __init__(self, factory: async_sessionmaker[AsyncSession], workspace_id: UUID) -> None:
        self.factory = factory
        self.workspace_id = workspace_id

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.factory)


@pytest.fixture
async def rig(migrated_database_url: str) -> AsyncIterator[Rig]:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id = await ensure_default_workspace(factory, name="Chat")
    yield Rig(factory, workspace_id)
    await engine.dispose()


class TestConversations:
    async def test_round_trip_and_recency_ordering(self, rig: Rig) -> None:
        first = Conversation(workspace_id=rig.workspace_id, title="First")
        second = Conversation(workspace_id=rig.workspace_id, title="Second")
        async with rig.uow() as uow:
            await uow.conversations.add(first)
            await uow.conversations.add(second)
            await uow.commit()

        first.touch()  # most recent activity wins the list
        async with rig.uow() as uow:
            await uow.conversations.save(first)
            await uow.commit()
        async with rig.uow() as uow:
            recent = await uow.conversations.list_recent(rig.workspace_id)
        assert [c.title for c in recent[:2]] == ["First", "Second"]

    async def test_soft_deleted_conversations_are_invisible(self, rig: Rig) -> None:
        conversation = Conversation(workspace_id=rig.workspace_id, title="Gone")
        async with rig.uow() as uow:
            await uow.conversations.add(conversation)
            await uow.commit()
        conversation.soft_delete()
        async with rig.uow() as uow:
            await uow.conversations.save(conversation)
            await uow.commit()
        async with rig.uow() as uow:
            assert await uow.conversations.get(rig.workspace_id, conversation.id) is None

    async def test_workspace_scoping(self, rig: Rig) -> None:
        conversation = Conversation(workspace_id=rig.workspace_id, title="Private")
        async with rig.uow() as uow:
            await uow.conversations.add(conversation)
            await uow.commit()
        other = await ensure_default_workspace(rig.factory, name="ChatOther")
        async with rig.uow() as uow:
            assert await uow.conversations.get(other, conversation.id) is None
            assert await uow.conversations.list_recent(other) == []


class TestMessages:
    async def test_chronological_order_and_usage_precision(self, rig: Rig) -> None:
        prompt_version_id = (await SqlPromptRegistry(rig.factory).get("chat.system")).version_id
        conversation = Conversation(workspace_id=rig.workspace_id)
        turns = [
            Message(conversation_id=conversation.id, role="user", content="What is our PTO?"),
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="25 days per year.",
                model="claude-sonnet-5",
                provider="anthropic",
                prompt_version_id=prompt_version_id,
                input_tokens=3812,
                output_tokens=402,
                cost_usd=0.017412,
                latency_ms=2916,
                trace_id="t-chat-1",
            ),
        ]
        async with rig.uow() as uow:
            await uow.conversations.add(conversation)
            for message in turns:
                await uow.messages.add(message)
            await uow.commit()

        async with rig.uow() as uow:
            listed = await uow.messages.list_for_conversation(rig.workspace_id, conversation.id)
            count = await uow.messages.count_for_conversation(rig.workspace_id, conversation.id)
        assert [m.role for m in listed] == ["user", "assistant"]
        assert count == 2
        answer = listed[1]
        assert answer.cost_usd == pytest.approx(0.017412)  # numeric(12,6) exact
        assert (answer.provider, answer.model) == ("anthropic", "claude-sonnet-5")
        assert answer.prompt_version_id == prompt_version_id

    async def test_messages_scoped_via_conversation_workspace(self, rig: Rig) -> None:
        conversation = Conversation(workspace_id=rig.workspace_id)
        message = Message(conversation_id=conversation.id, role="user", content="secret")
        async with rig.uow() as uow:
            await uow.conversations.add(conversation)
            await uow.messages.add(message)
            await uow.commit()
        other = await ensure_default_workspace(rig.factory, name="MsgOther")
        async with rig.uow() as uow:
            assert await uow.messages.get(other, message.id) is None
            assert await uow.messages.list_for_conversation(other, conversation.id) == []


class TestMemories:
    async def test_round_trip_with_provenance(self, rig: Rig) -> None:
        conversation = Conversation(workspace_id=rig.workspace_id)
        source_message = Message(
            conversation_id=conversation.id, role="user", content="I prefer bullets"
        )
        memory = Memory(
            workspace_id=rig.workspace_id,
            kind="preference",
            content="Prefers answers as bullet points.",
            source_message_id=source_message.id,
            confidence=0.8,
        )
        async with rig.uow() as uow:
            await uow.conversations.add(conversation)
            await uow.messages.add(source_message)
            await uow.memories.add(memory)
            await uow.commit()
        async with rig.uow() as uow:
            loaded = await uow.memories.get(rig.workspace_id, memory.id)
        assert loaded is not None
        assert loaded.source_message_id == source_message.id
        assert loaded.confidence == pytest.approx(0.8)

    async def test_expired_and_deleted_memories_are_not_listed(self, rig: Rig) -> None:
        live = Memory(workspace_id=rig.workspace_id, kind="decision", content="Keep Postgres.")
        expired = Memory(
            workspace_id=rig.workspace_id,
            kind="episodic",
            content="Debugging session last winter.",
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        deleted = Memory(workspace_id=rig.workspace_id, kind="entity", content="Old vendor.")
        deleted.soft_delete()
        async with rig.uow() as uow:
            for memory in (live, expired, deleted):
                await uow.memories.add(memory)
            await uow.commit()
        async with rig.uow() as uow:
            active = await uow.memories.list_active(rig.workspace_id)
            by_kind = await uow.memories.list_active(rig.workspace_id, kind="decision")
        assert [m.id for m in active] == [live.id]
        assert [m.id for m in by_kind] == [live.id]


class TestCitations:
    async def test_round_trip_marker_order_and_retention_interlock(self, rig: Rig) -> None:
        """Citations come back in marker order, scoped by workspace;
        deleting a cited chunk violates the restrictive FK — the
        docs/11 §4 retention interlock, proved against real Postgres."""
        source = Source(workspace_id=rig.workspace_id, kind="folder", name="N", uri="/cite")
        document = Document(source_id=source.id, path="doc.md", mime_type="text/markdown")
        version = DocumentVersion(
            document_id=document.id, content_hash=ContentHash("c" * 64), size_bytes=1, parser="md"
        )
        chunk = Chunk(
            document_version_id=version.id,
            ordinal=0,
            text="The notice period is 30 days.",
            token_count=6,
            content_hash=ContentHash("d" * 64),
        )
        conversation = Conversation(workspace_id=rig.workspace_id)
        message = Message(conversation_id=conversation.id, role="assistant", content="30 days [1]")
        async with rig.uow() as uow:
            await uow.sources.add(source)
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await uow.chunks.add_all([chunk])
            await uow.conversations.add(conversation)
            await uow.messages.add(message)
            await uow.citations.add_all(
                [
                    Citation(message_id=message.id, chunk_id=chunk.id, marker=2, score=0.01),
                    Citation(message_id=message.id, chunk_id=chunk.id, marker=1, score=0.03),
                ]
            )
            await uow.commit()

        async with rig.uow() as uow:
            listed = await uow.citations.list_for_message(rig.workspace_id, message.id)
        assert [c.marker for c in listed] == [1, 2]
        assert all(c.chunk_id == chunk.id for c in listed)

        other = await ensure_default_workspace(rig.factory, name="CiteOther")
        async with rig.uow() as uow:
            assert await uow.citations.list_for_message(other, message.id) == []

        async with rig.factory() as session:
            with pytest.raises(IntegrityError):
                await session.execute(delete(ChunkRow).where(ChunkRow.id == chunk.id))
                await session.commit()
