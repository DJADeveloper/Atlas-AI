"""Runtime profiles: model routing configuration (spine §9).

These are pure configuration values — which provider and model fills each
role under each profile. Provider adapters and routing *behavior* arrive at
M07; nothing here performs I/O or imports an SDK.
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

Profile = Literal["hybrid", "local-only"]

ProviderName = Literal["anthropic", "openai", "ollama"]


class ModelRef(BaseModel):
    """A provider/model pair filling one routing role."""

    model_config = ConfigDict(frozen=True)

    provider: ProviderName
    model: str


class ProviderProfile(BaseModel):
    """Resolved model routing for one runtime profile.

    Embeddings are local (ollama) in BOTH profiles: document content never
    leaves the machine for indexing, even when cloud reasoning is enabled
    (spine §9 privacy rationale).
    """

    model_config = ConfigDict(frozen=True)

    name: Profile
    chat: ModelRef
    escalation: ModelRef | None
    classification: ModelRef
    embedding: ModelRef


_LOCAL_EMBEDDING = ModelRef(provider="ollama", model="nomic-embed-text")

HYBRID = ProviderProfile(
    name="hybrid",
    chat=ModelRef(provider="anthropic", model="claude-sonnet-5"),
    escalation=ModelRef(provider="anthropic", model="claude-opus-4-8"),
    classification=ModelRef(provider="anthropic", model="claude-haiku-4-5-20251001"),
    embedding=_LOCAL_EMBEDDING,
)

LOCAL_ONLY = ProviderProfile(
    name="local-only",
    chat=ModelRef(provider="ollama", model="llama3.1:8b"),
    escalation=None,
    classification=ModelRef(provider="ollama", model="llama3.1:8b"),
    embedding=_LOCAL_EMBEDDING,
)

PROFILES: Mapping[Profile, ProviderProfile] = {
    "hybrid": HYBRID,
    "local-only": LOCAL_ONLY,
}
