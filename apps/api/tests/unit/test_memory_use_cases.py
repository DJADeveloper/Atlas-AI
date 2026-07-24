"""Memory use cases v1 (M07): explicit remember/recall/revise/forget,
and the recall ranking contract — preferences are standing orders that
always ride first; other kinds rank by lexical overlap then recency
(docs/22 §5-6)."""

from datetime import timedelta
from uuid import UUID

import pytest

from atlas.application.memory import (
    ForgetMemory,
    ListMemories,
    RecallMemories,
    RememberFact,
    ReviseMemory,
    rank_memories,
)
from atlas.domain.memory.entities import Memory
from atlas.shared.clock import utc_now
from atlas.shared.errors import NotFound, ValidationFailed
from atlas.shared.ids import uuid7
from tests.fakes import FakeState, FakeUnitOfWork


class Rig:
    def __init__(self) -> None:
        self.state = FakeState()
        self.workspace_id: UUID = uuid7()

    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.state)


class TestRememberFact:
    async def test_remembers_and_lists(self) -> None:
        rig = Rig()
        memory = await RememberFact(rig.uow).execute(
            rig.workspace_id, "decision", "We chose Postgres over SQLite.", confidence=0.9
        )
        listed = await ListMemories(rig.uow).execute(rig.workspace_id)
        assert [m.id for m in listed] == [memory.id]
        assert listed[0].kind == "decision"

    async def test_project_facts_wait_for_projects(self) -> None:
        rig = Rig()
        with pytest.raises(ValidationFailed):
            await RememberFact(rig.uow).execute(rig.workspace_id, "project_fact", "fee is 50k")

    async def test_provenance_rides_along(self) -> None:
        rig = Rig()
        source = uuid7()
        memory = await RememberFact(rig.uow).execute(
            rig.workspace_id, "entity", "Dana is my accountant", source_message_id=source
        )
        assert memory.source_message_id == source


class TestRecallMemories:
    async def test_preferences_always_come_first(self) -> None:
        rig = Rig()
        await RememberFact(rig.uow).execute(
            rig.workspace_id, "decision", "notice period is 30 days"
        )
        await RememberFact(rig.uow).execute(rig.workspace_id, "preference", "answer in Spanish")
        recalled = await RecallMemories(rig.uow).execute(
            rig.workspace_id, "what is the notice period?"
        )
        # Zero lexical overlap, yet the preference leads: standing order.
        assert recalled[0].kind == "preference"
        assert recalled[1].kind == "decision"

    async def test_lexical_overlap_ranks_facts(self) -> None:
        rig = Rig()
        await RememberFact(rig.uow).execute(rig.workspace_id, "episodic", "debugged NAS backup")
        await RememberFact(rig.uow).execute(
            rig.workspace_id, "decision", "the notice period is 30 days"
        )
        recalled = await RecallMemories(rig.uow).execute(rig.workspace_id, "notice period?")
        assert [m.kind for m in recalled] == ["decision", "episodic"]

    async def test_limit_caps_the_recall(self) -> None:
        rig = Rig()
        for i in range(6):
            await RememberFact(rig.uow).execute(rig.workspace_id, "episodic", f"event {i}")
        recalled = await RecallMemories(rig.uow).execute(rig.workspace_id, "events", limit=3)
        assert len(recalled) == 3

    async def test_expired_memories_never_surface(self) -> None:
        rig = Rig()
        await RememberFact(rig.uow).execute(
            rig.workspace_id,
            "episodic",
            "temporary note",
            expires_at=utc_now() - timedelta(minutes=1),
        )
        recalled = await RecallMemories(rig.uow).execute(rig.workspace_id, "temporary note")
        assert recalled == []

    def test_rank_is_pure_and_recency_breaks_ties(self) -> None:
        workspace_id = uuid7()
        older = Memory(workspace_id=workspace_id, kind="decision", content="alpha topic")
        newer = Memory(workspace_id=workspace_id, kind="decision", content="alpha topic twice")
        newer.updated_at = older.updated_at + timedelta(minutes=5)
        ranked = rank_memories([older, newer], "unrelated query")
        assert ranked[0].id == newer.id


class TestReviseAndForget:
    async def test_revise_updates_content(self) -> None:
        rig = Rig()
        memory = await RememberFact(rig.uow).execute(rig.workspace_id, "entity", "Dana: accountant")
        revised = await ReviseMemory(rig.uow).execute(
            rig.workspace_id, memory.id, "Dana is my former accountant"
        )
        assert revised.content == "Dana is my former accountant"
        listed = await ListMemories(rig.uow).execute(rig.workspace_id)
        assert listed[0].content == "Dana is my former accountant"

    async def test_forget_soft_deletes(self) -> None:
        rig = Rig()
        memory = await RememberFact(rig.uow).execute(rig.workspace_id, "episodic", "old note")
        await ForgetMemory(rig.uow).execute(rig.workspace_id, memory.id)
        assert await ListMemories(rig.uow).execute(rig.workspace_id) == []
        with pytest.raises(NotFound):
            await ForgetMemory(rig.uow).execute(rig.workspace_id, memory.id)

    async def test_cross_workspace_access_is_not_found(self) -> None:
        rig = Rig()
        memory = await RememberFact(rig.uow).execute(rig.workspace_id, "episodic", "mine")
        with pytest.raises(NotFound):
            await ReviseMemory(rig.uow).execute(uuid7(), memory.id, "stolen")
