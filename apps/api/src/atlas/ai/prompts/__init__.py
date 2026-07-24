"""Prompt registry v1 (M08; spine §5/§7, docs/11 §5)."""

from atlas.ai.prompts.definitions import PROMPT_SPECS, PromptSpec, spec_for
from atlas.ai.prompts.registry import (
    PromptProvider,
    RegisteredPrompt,
    StaticPromptRegistry,
)

__all__ = [
    "PROMPT_SPECS",
    "PromptProvider",
    "PromptSpec",
    "RegisteredPrompt",
    "StaticPromptRegistry",
    "spec_for",
]
