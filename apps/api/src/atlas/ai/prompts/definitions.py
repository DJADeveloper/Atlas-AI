"""The registered prompt versions (M08; docs/11 §5).

Templates are immutable artifacts: editing one means REGISTERING A NEW
VERSION, never mutating an existing entry — the content hash is checked
against the database on load and a mismatch refuses to boot. Every LLM
call records which version ran (`messages.prompt_version_id`), so an
answer is always attributable to the exact words that asked for it.
"""

from dataclasses import dataclass, field
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class PromptSpec:
    name: str
    version: int
    template: str
    description: str
    variables: tuple[str, ...] = field(default=())

    @property
    def label(self) -> str:
        """The human-readable form the API surfaces ("chat.grounded.v1")."""
        return f"{self.name}.v{self.version}"

    @property
    def content_hash(self) -> str:
        return sha256(self.template.encode()).hexdigest()


CHAT_SYSTEM_V1 = PromptSpec(
    name="chat.system",
    version=1,
    description="Plain conversation without retrieval (pre-M08 path).",
    template=(
        "You are Atlas, a local-first assistant. Answer from the conversation "
        "and the provided context sections. Be direct and concise. If the "
        "context sections do not contain what you need, say so plainly instead "
        "of guessing."
    ),
)

CHAT_GROUNDED_V1 = PromptSpec(
    name="chat.grounded",
    version=1,
    description="Grounded RAG answering with inline [n] citations (spine §10).",
    template=(
        "You are Atlas, a local-first assistant. Answer the user's question "
        "using ONLY the numbered entries in the Retrieved context section and "
        "the conversation itself. Cite every factual claim drawn from the "
        "context with its marker, like [1] or [2][3], placed directly after "
        "the claim. Never invent markers that do not appear in the Retrieved "
        "context. If the retrieved context does not contain the answer, say "
        "plainly that the indexed documents do not cover it and stop - never "
        "guess and never cite what you did not use."
    ),
)

SUMMARIZE_V1 = PromptSpec(
    name="conversation.summarize",
    version=1,
    description="Fold old turns into the rolling summary (docs/22 §2).",
    template=(
        "Fold the conversation transcript below into one running summary. "
        "Preserve concrete facts, names, numbers, decisions, and open "
        "questions - they must survive verbatim. Drop pleasantries and "
        "repetition. Write plain prose, at most 300 words, no preamble. If an "
        "existing summary is provided, merge it with the new turns into a "
        "single coherent summary."
    ),
)

TITLE_V1 = PromptSpec(
    name="conversation.title",
    version=1,
    description="Name a fresh conversation from its first exchange.",
    template=(
        "Name this conversation. Reply with only the title: at most six "
        "words, no quotes, no trailing punctuation."
    ),
)

JUDGE_GROUNDEDNESS_V1 = PromptSpec(
    name="judge.groundedness",
    version=1,
    description="LLM-as-judge groundedness grader (drafted here, used by M11).",
    variables=("question", "context", "answer"),
    template=(
        "You are grading whether an answer is grounded in its retrieved "
        "context. You will receive a QUESTION, the numbered CONTEXT entries "
        "that were available, and the ANSWER given. A claim is grounded only "
        "if a context entry states it or it follows trivially. Respond with "
        'exactly one JSON object: {"grounded": true|false, '
        '"unsupported_claims": ["..."], "missing_citations": ["..."]}. '
        "Mark grounded=false if any factual claim lacks support in the "
        "context, if a citation marker points at an entry that does not "
        "support the claim, or if the answer should have abstained."
    ),
)

PROMPT_SPECS: tuple[PromptSpec, ...] = (
    CHAT_SYSTEM_V1,
    CHAT_GROUNDED_V1,
    SUMMARIZE_V1,
    TITLE_V1,
    JUDGE_GROUNDEDNESS_V1,
)


def spec_for(name: str) -> PromptSpec:
    for spec in PROMPT_SPECS:
        if spec.name == name:
            return spec
    raise LookupError(f"no registered prompt named {name!r}")
