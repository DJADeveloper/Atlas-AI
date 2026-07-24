"""Prompt resolution at call time (M08).

`PromptProvider` is the seam: chat use cases resolve a prompt NAME to
the registered version (template + the row id stamped on messages).
The SQL implementation (infrastructure) get-or-creates rows on first
use and refuses a content-hash mismatch; `StaticPromptRegistry` backs
unit tests with minted ids and zero I/O.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from atlas.ai.prompts.definitions import PROMPT_SPECS, PromptSpec
from atlas.shared.ids import uuid7


@dataclass(frozen=True, slots=True)
class RegisteredPrompt:
    spec: PromptSpec
    version_id: UUID  # prompt_versions.id — the FK messages record

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def template(self) -> str:
        return self.spec.template


class PromptProvider(Protocol):
    async def get(self, name: str) -> RegisteredPrompt: ...


class StaticPromptRegistry:
    """In-memory provider over the code-defined specs (unit tests and
    tooling); ids are minted per instance."""

    def __init__(self, specs: tuple[PromptSpec, ...] = PROMPT_SPECS) -> None:
        self._entries = {
            spec.name: RegisteredPrompt(spec=spec, version_id=uuid7()) for spec in specs
        }

    async def get(self, name: str) -> RegisteredPrompt:
        entry = self._entries.get(name)
        if entry is None:
            raise LookupError(f"no registered prompt named {name!r}")
        return entry
