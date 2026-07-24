"""Context-window assembler (docs/20 §9): segment caps, whole-unit
drops, the 70% summarization trigger, and the budget invariant proved
over arbitrary inputs (M07 acceptance: property test)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from atlas.ai.context import (
    CHUNK_BUDGET_FRACTION,
    MEMORY_BUDGET_FRACTION,
    SUMMARIZE_AT_FRACTION,
    AssembledContext,
    ContextAssembler,
    ContextSources,
    Turn,
    context_window_for,
)
from atlas.domain.ai.errors import ContextWindowExceeded
from atlas.shared.tokens import count_tokens

MODEL = "test-model"
SYSTEM = "You are Atlas."


def _assembler(window: int) -> ContextAssembler:
    return ContextAssembler({MODEL: window})


def _words(n: int, word: str = "context") -> str:
    return " ".join(word for _ in range(n))


def _prompt_tokens(assembled: AssembledContext) -> int:
    return sum(count_tokens(message.content) for message in assembled.messages)


class TestBudgetMath:
    def test_unknown_models_get_the_conservative_floor(self) -> None:
        assert context_window_for("mystery-model") == 8_192

    def test_no_budget_left_raises(self) -> None:
        with pytest.raises(ContextWindowExceeded):
            _assembler(1000).assemble(
                model=MODEL,
                max_tokens=1000,
                system_prompt=SYSTEM,
                turns=[Turn(role="user", content="hi")],
            )

    def test_system_prompt_overflow_is_a_build_bug(self) -> None:
        with pytest.raises(ContextWindowExceeded, match="build bug"):
            _assembler(1000).assemble(
                model=MODEL,
                max_tokens=100,
                system_prompt=_words(900),
                turns=[Turn(role="user", content="hi")],
            )

    def test_oversized_latest_turn_raises(self) -> None:
        with pytest.raises(ContextWindowExceeded, match="latest turn"):
            _assembler(1000).assemble(
                model=MODEL,
                max_tokens=100,
                system_prompt=SYSTEM,
                turns=[Turn(role="user", content=_words(2000))],
            )


class TestSegmentCaps:
    def test_memories_drop_whole_from_lowest_priority(self) -> None:
        # window 2000 → budget 2000-200-200 = 1600; memory cap = 160.
        # Each rendered memory "- <60 words>" is 61 tokens; the header
        # is 4 → two fit (126), the third (187) would overflow the cap.
        assembled = _assembler(2000).assemble(
            model=MODEL,
            max_tokens=200,
            system_prompt=SYSTEM,
            turns=[Turn(role="user", content="hi")],
            sources=ContextSources(
                memories=[_words(60, "pref"), _words(60, "fact"), _words(60, "note")]
            ),
        )
        assert assembled.included_memories == 2
        assert assembled.dropped_memories == 1
        system = assembled.messages[0].content
        assert "pref" in system and "fact" in system
        assert "note" not in system  # dropped whole, not truncated

    def test_chunks_drop_whole_from_rank_eight_downward(self) -> None:
        # window 2000 → budget 1600; chunk cap = 640. Chunks render as
        # "[n] <text>" (153 tokens each with the marker) → four fit.
        chunks = [_words(150, f"chunk{i}") for i in range(1, 7)]
        assembled = _assembler(2000).assemble(
            model=MODEL,
            max_tokens=200,
            system_prompt=SYSTEM,
            turns=[Turn(role="user", content="hi")],
            sources=ContextSources(chunks=chunks),
        )
        assert assembled.included_chunks == 4
        assert assembled.dropped_chunks == 2
        system = assembled.messages[0].content
        assert "chunk1" in system and "chunk4" in system
        assert "chunk5" not in system and "chunk6" not in system

    def test_memory_cap_fraction_is_ten_percent(self) -> None:
        assert MEMORY_BUDGET_FRACTION == 0.10
        assert CHUNK_BUDGET_FRACTION == 0.40


class TestConversationSegment:
    def test_oldest_turns_drop_first_newest_always_kept(self) -> None:
        turns = [
            Turn(role="user", content=_words(300, "oldest")),
            Turn(role="assistant", content=_words(300, "middle")),
            Turn(role="user", content=_words(300, "newest")),
        ]
        # window 1600 → budget 1600-160-160=1280; system 4 → conv 1276.
        # Newest+middle fit (600); adding oldest would exceed... no -
        # 900 fits. Use window 1100 → budget 1100-110-160=830; conv 826
        # → two turns (600) fit, three (900) do not.
        assembled = _assembler(1100).assemble(
            model=MODEL, max_tokens=160, system_prompt=SYSTEM, turns=turns
        )
        assert assembled.included_turns == 2
        assert assembled.dropped_turns == 1
        replayed = [message.content for message in assembled.messages[1:]]
        assert "newest" in replayed[-1]
        assert all("oldest" not in content for content in replayed)

    def test_summary_rides_in_the_system_message(self) -> None:
        assembled = _assembler(2000).assemble(
            model=MODEL,
            max_tokens=200,
            system_prompt=SYSTEM,
            turns=[Turn(role="user", content="hi")],
            sources=ContextSources(summary="Earlier we chose Postgres over SQLite."),
        )
        assert assembled.summary_included
        assert "Earlier we chose Postgres" in assembled.messages[0].content

    def test_summarization_triggers_over_seventy_percent(self) -> None:
        assembler = _assembler(2000)  # budget 1600; system 4 → conv 1596
        threshold = int(1596 * SUMMARIZE_AT_FRACTION)  # 1117
        below = assembler.assemble(
            model=MODEL,
            max_tokens=200,
            system_prompt=SYSTEM,
            turns=[Turn(role="user", content=_words(threshold - 50))],
        )
        over = assembler.assemble(
            model=MODEL,
            max_tokens=200,
            system_prompt=SYSTEM,
            turns=[Turn(role="user", content=_words(threshold + 50))],
        )
        assert below.needs_summarization is False
        assert over.needs_summarization is True

    def test_dropped_turns_always_trigger_summarization(self) -> None:
        assembled = _assembler(1100).assemble(
            model=MODEL,
            max_tokens=160,
            system_prompt=SYSTEM,
            turns=[Turn(role="user", content=_words(300)) for _ in range(4)],
        )
        assert assembled.dropped_turns > 0
        assert assembled.needs_summarization is True


class TestBudgetInvariant:
    """M07 acceptance: assembled context never exceeds the window,
    proved over arbitrary inputs. `used_tokens` counts every segment in
    rendered form and the counter is additive across whitespace joins,
    so the real prompt can never exceed what was budgeted."""

    @given(
        window=st.integers(min_value=600, max_value=6000),
        max_tokens=st.integers(min_value=16, max_value=512),
        turns=st.lists(
            st.tuples(st.sampled_from(["user", "assistant"]), st.text(max_size=400)),
            max_size=30,
        ),
        memories=st.lists(st.text(max_size=200), max_size=15),
        chunks=st.lists(st.text(max_size=300), max_size=12),
        summary=st.none() | st.text(max_size=400),
    )
    def test_used_tokens_never_exceed_budget(
        self,
        window: int,
        max_tokens: int,
        turns: list[tuple[str, str]],
        memories: list[str],
        chunks: list[str],
        summary: str | None,
    ) -> None:
        assembler = _assembler(window)
        try:
            assembled = assembler.assemble(
                model=MODEL,
                max_tokens=max_tokens,
                system_prompt=SYSTEM,
                turns=[
                    Turn(role="user" if role == "user" else "assistant", content=content)
                    for role, content in turns
                ],
                sources=ContextSources(memories=memories, chunks=chunks, summary=summary),
            )
        except ContextWindowExceeded:
            return  # refusing to overflow satisfies the invariant too
        assert assembled.used_tokens <= assembled.budget
        assert _prompt_tokens(assembled) <= assembled.used_tokens
        assert _prompt_tokens(assembled) + max_tokens <= window
