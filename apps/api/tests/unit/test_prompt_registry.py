"""Prompt registry v1 (M08): immutability enforced in CI.

The frozen hashes below are the contract: editing a registered
template WITHOUT registering a new version fails this suite — exactly
the "CI forbids mutating registered versions" guarantee (docs/60 M08
risk note). Adding a new version means adding a new frozen hash here,
which is a visible, reviewable act.
"""

import pytest

from atlas.ai.prompts import PROMPT_SPECS, StaticPromptRegistry, spec_for

FROZEN_HASHES = {
    "chat.system.v1": "065237d56149242f64ae3f606feec7275159b7c822b9146f351a5970eee2d9fd",
    "chat.grounded.v1": "b5fc7340d894b7f176c370b21f67bcdba8f30827bcba9a6a216af1f3e4bbafac",
    "conversation.summarize.v1": "b2b809939cccc8bd713504bbfa85a00226b198cfe80dff29b65b5de2ba26c507",
    "conversation.title.v1": "e5b88bc6e7b1708c33f7c4e80f73213d144035c3528b6ea4d8da6a70e7a0aec1",
    "judge.groundedness.v1": "ce1d9e762e60fae27c5d242826c4368e4433615259e0f4a86ca2fe97cec991c4",
}


def test_registered_versions_are_immutable() -> None:
    """A changed hash here means a registered template was edited in
    place. Register a NEW version instead; never update a frozen hash
    without bumping the version number."""
    assert {spec.label: spec.content_hash for spec in PROMPT_SPECS} == FROZEN_HASHES


def test_names_and_versions_are_unique() -> None:
    labels = [spec.label for spec in PROMPT_SPECS]
    assert len(labels) == len(set(labels))


def test_spec_lookup_and_unknown_names_raise() -> None:
    assert spec_for("chat.grounded").version == 1
    with pytest.raises(LookupError):
        spec_for("no.such.prompt")


async def test_static_registry_resolves_with_stable_ids() -> None:
    registry = StaticPromptRegistry()
    first = await registry.get("chat.system")
    second = await registry.get("chat.system")
    assert first.version_id == second.version_id  # stable within an instance
    assert first.label == "chat.system.v1"
    assert first.template  # the template rides along for assembly
    with pytest.raises(LookupError):
        await registry.get("no.such.prompt")
