"""Model routing (docs/20 §4; the spine §9 table, verbatim, keyed by role).

Call sites ask for a ROLE; the router resolves (role, profile) into an
ordered model chain. Profiles are enforced structurally: in local-only
profile the chain is built exclusively from local models — cloud rungs
are not deprioritized, they are absent. Escalation in local-only
degrades to the chat role explicitly (the spine marks it unavailable).
"""

from dataclasses import dataclass
from typing import Literal

Role = Literal["chat", "escalation", "classification"]
Profile = Literal["hybrid", "local_only"]
TimeoutClass = Literal["interactive_stream", "standard", "escalation", "fast"]


def as_routing_profile(config_profile: str) -> Profile:
    """Map the user-facing profile name (docs/40 spells it
    ``local-only``) onto the routing literal used throughout atlas.ai.
    Anything unrecognized fails CLOSED to local-only: a typo in
    configuration must never quietly enable cloud dispatch."""
    return "hybrid" if config_profile == "hybrid" else "local_only"


@dataclass(frozen=True, slots=True)
class ModelRef:
    provider: str  # "anthropic" | "ollama"
    model: str
    is_local: bool


@dataclass(frozen=True, slots=True)
class RoutePlan:
    role: Role
    chain: tuple[ModelRef, ...]  # primary first; later rungs are degradation
    timeout_class: TimeoutClass
    max_tokens: int
    degraded_role: bool = False  # escalation served as chat in local-only


SONNET = ModelRef("anthropic", "claude-sonnet-5", is_local=False)
OPUS = ModelRef("anthropic", "claude-opus-4-8", is_local=False)
HAIKU = ModelRef("anthropic", "claude-haiku-4-5-20251001", is_local=False)
LLAMA = ModelRef("ollama", "llama3.1:8b", is_local=True)

_HYBRID_CHAINS: dict[Role, tuple[ModelRef, ...]] = {
    "chat": (SONNET, HAIKU, LLAMA),  # docs/20 §5.4 fallback chains
    "escalation": (OPUS, SONNET),
    "classification": (HAIKU, LLAMA),
}
_LOCAL_CHAINS: dict[Role, tuple[ModelRef, ...]] = {
    "chat": (LLAMA,),
    "classification": (LLAMA,),
}
_TIMEOUTS: dict[Role, TimeoutClass] = {
    "chat": "interactive_stream",
    "escalation": "escalation",
    "classification": "fast",
}
_MAX_TOKENS: dict[Role, int] = {
    "chat": 4096,
    "escalation": 8192,
    "classification": 512,
}


class ModelRouter:
    """Pure resolution — no I/O, no state; the executor owns resilience."""

    def plan(self, role: Role, profile: Profile) -> RoutePlan:
        if profile == "local_only":
            resolved: Role = "chat" if role == "escalation" else role
            return RoutePlan(
                role=resolved,
                chain=_LOCAL_CHAINS[resolved],
                timeout_class=_TIMEOUTS[resolved],
                max_tokens=_MAX_TOKENS[resolved],
                degraded_role=role == "escalation",
            )
        return RoutePlan(
            role=role,
            chain=_HYBRID_CHAINS[role],
            timeout_class=_TIMEOUTS[role],
            max_tokens=_MAX_TOKENS[role],
        )
