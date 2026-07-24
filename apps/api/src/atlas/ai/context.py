"""Context-window management (docs/20 §9).

For each call the assembler works against ``budget = context_window -
max_tokens - safety margin (10% of the window)`` and packs four
segments in priority order:

| Segment                    | Cap            | Overflow behavior          |
|----------------------------|----------------|----------------------------|
| system prompt              | measured       | never truncated: build bug |
| memory context             | ≤ 10% of budget| drop whole, lowest first   |
| retrieved chunks           | ≤ 40% of budget| drop whole, rank 8 down    |
| summary + verbatim turns   | remainder      | oldest fold into summary   |

The design principle: degrade the newest, least-load-bearing context
first, and degrade in **whole semantic units** — a half chunk breaks
citation grounding, a half memory is a corrupted fact.

Every segment is counted in its RENDERED form (headers, bullets, rank
markers included), and the shared counter is additive across
whitespace joins — so ``used_tokens ≤ budget`` is an exact invariant
of the assembled text, provable by property test, not an estimate.
The 10% safety margin then absorbs what genuinely cannot be counted
client-side: vendor tokenizer drift and per-message wire overhead.

Callers pass memories highest-priority-first (standing preferences
before recalled facts) and chunks in fusion-rank order; the assembler
keeps the longest prefix of each that fits its cap.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from atlas.domain.ai.errors import ContextWindowExceeded
from atlas.domain.ai.provider import ChatRole, ProviderChatMessage
from atlas.shared.tokens import count_tokens

# Context limits live beside the rate table (docs/20 §9) until M10
# moves both into a reviewed config file. MUST be verified against the
# provider's current model documentation on every model change. Unknown
# models fall back to the conservative floor — packing tighter than
# reality is safe; looser overflows.
DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-haiku-4-5-20251001": 200_000,
    # llama3.1 advertises 128k, but Ollama serves num_ctx=8192 unless
    # raised in the Modelfile — budget for what is actually served.
    "llama3.1:8b": 8_192,
}
FALLBACK_CONTEXT_WINDOW = 8_192

SAFETY_MARGIN_FRACTION = 0.10
MEMORY_BUDGET_FRACTION = 0.10
CHUNK_BUDGET_FRACTION = 0.40

# Summarization trigger (docs/60 M07): fold when the verbatim tail
# exceeds 70% of the conversation segment's budget — early enough that
# the background job finishes before the window actually overflows.
SUMMARIZE_AT_FRACTION = 0.70

MEMORY_HEADER = "## Standing memory"
CHUNK_HEADER = "## Retrieved context"
SUMMARY_HEADER = "## Earlier conversation (summarized)"


@dataclass(frozen=True, slots=True)
class Turn:
    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class ContextSources:
    """Everything beyond the dialogue itself: standing memories
    (highest priority first), retrieved chunks (fusion-rank order),
    and the rolling summary."""

    memories: Sequence[str] = ()
    chunks: Sequence[str] = ()
    summary: str | None = None


EMPTY_SOURCES = ContextSources()


@dataclass(frozen=True, slots=True)
class AssembledContext:
    """The packed prompt plus the packing evidence — every drop is
    visible data, never a silent truncation (spine §2.7)."""

    messages: list[ProviderChatMessage]
    budget: int
    conversation_budget: int
    used_tokens: int
    included_memories: int
    dropped_memories: int
    included_chunks: int
    dropped_chunks: int
    included_turns: int
    dropped_turns: int
    summary_included: bool
    needs_summarization: bool


def context_window_for(model: str, windows: dict[str, int] | None = None) -> int:
    table = windows if windows is not None else DEFAULT_CONTEXT_WINDOWS
    return table.get(model, FALLBACK_CONTEXT_WINDOW)


class ContextAssembler:
    def __init__(self, windows: dict[str, int] | None = None) -> None:
        self._windows = windows if windows is not None else DEFAULT_CONTEXT_WINDOWS

    def assemble(
        self,
        *,
        model: str,
        max_tokens: int,
        system_prompt: str,
        turns: Sequence[Turn],
        sources: ContextSources = EMPTY_SOURCES,
    ) -> AssembledContext:
        memories, chunks, summary = sources.memories, sources.chunks, sources.summary
        window = context_window_for(model, self._windows)
        budget = window - max_tokens - math.ceil(window * SAFETY_MARGIN_FRACTION)
        if budget <= 0:
            raise ContextWindowExceeded(
                f"max_tokens {max_tokens} leaves no prompt budget in a {window}-token window"
            )

        system_tokens = count_tokens(system_prompt)
        if system_tokens > budget:
            raise ContextWindowExceeded(
                "system prompt alone exceeds the context budget - that is a build bug, "
                f"not an input problem ({system_tokens} > {budget})"
            )

        memory_section, kept_memories = _packed_section(
            MEMORY_HEADER,
            [f"- {text}" for text in memories],
            int(budget * MEMORY_BUDGET_FRACTION),
        )
        chunk_section, kept_chunks = _packed_section(
            CHUNK_HEADER,
            [f"[{rank}] {text}" for rank, text in enumerate(chunks, start=1)],
            int(budget * CHUNK_BUDGET_FRACTION),
        )
        memory_tokens = count_tokens(memory_section) if memory_section else 0
        chunk_tokens = count_tokens(chunk_section) if chunk_section else 0

        conversation_budget = budget - system_tokens - memory_tokens - chunk_tokens

        summary_section = f"{SUMMARY_HEADER}\n{summary}" if summary else None
        summary_tokens = count_tokens(summary_section) if summary_section else 0
        include_summary = summary_section is not None and summary_tokens <= conversation_budget
        turn_budget = conversation_budget - (summary_tokens if include_summary else 0)

        kept_turns = _newest_suffix(turns, turn_budget)
        if turns and not kept_turns:
            raise ContextWindowExceeded(
                "the latest turn alone exceeds the conversation budget "
                f"({count_tokens(turns[-1].content)} > {turn_budget})"
            )

        turn_tokens_total = sum(count_tokens(turn.content) for turn in turns)
        kept_turn_tokens = sum(count_tokens(turn.content) for turn in kept_turns)

        system_parts = [system_prompt]
        if memory_section:
            system_parts.append(memory_section)
        if chunk_section:
            system_parts.append(chunk_section)
        if include_summary and summary_section:
            system_parts.append(summary_section)
        messages = [ProviderChatMessage(role="system", content="\n\n".join(system_parts))]
        messages.extend(ProviderChatMessage(role=t.role, content=t.content) for t in kept_turns)

        return AssembledContext(
            messages=messages,
            budget=budget,
            conversation_budget=conversation_budget,
            used_tokens=system_tokens
            + memory_tokens
            + chunk_tokens
            + (summary_tokens if include_summary else 0)
            + kept_turn_tokens,
            included_memories=len(kept_memories),
            dropped_memories=len(memories) - len(kept_memories),
            included_chunks=len(kept_chunks),
            dropped_chunks=len(chunks) - len(kept_chunks),
            included_turns=len(kept_turns),
            dropped_turns=len(turns) - len(kept_turns),
            summary_included=include_summary,
            needs_summarization=turn_tokens_total > conversation_budget * SUMMARIZE_AT_FRACTION,
        )


def _packed_section(header: str, rendered: Sequence[str], cap: int) -> tuple[str | None, list[str]]:
    """Longest prefix of RENDERED units (bullets and markers counted)
    fitting the cap alongside the header; first overflow ends the
    segment — dropping from the tail IS dropping lowest priority."""
    if not rendered:
        return None, []
    used = count_tokens(header)
    if used > cap:
        return None, []
    kept: list[str] = []
    for item in rendered:
        tokens = count_tokens(item)
        if used + tokens > cap:
            break
        kept.append(item)
        used += tokens
    if not kept:
        return None, []
    return header + "\n" + "\n".join(kept), kept


def _newest_suffix(turns: Sequence[Turn], cap: int) -> list[Turn]:
    """Newest turns are load-bearing (the question being answered);
    oldest fold into the summary first (docs/20 §9)."""
    kept: list[Turn] = []
    used = 0
    for turn in reversed(turns):
        tokens = count_tokens(turn.content)
        if used + tokens > cap:
            break
        kept.append(turn)
        used += tokens
    kept.reverse()
    return kept
