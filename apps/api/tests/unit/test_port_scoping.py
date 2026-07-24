"""M03 acceptance: every repository query method requires workspace_id.

This test introspects every repository port in every bounded context;
any public method whose name starts with a query prefix must take
``workspace_id`` as its first parameter. Adding an unscoped query is a
CI failure, not a review comment (M03 risk mitigation; the sweep grew
with the conversation and memory contexts at M07).
"""

import inspect

from atlas.domain.conversation import ports as conversation_ports
from atlas.domain.knowledge import ports as knowledge_ports
from atlas.domain.memory import ports as memory_ports

QUERY_PREFIXES = ("get", "list", "find", "count")
_PORT_MODULES = (knowledge_ports, conversation_ports, memory_ports)


def _repository_ports() -> list[type]:
    return [
        obj
        for module in _PORT_MODULES
        for name, obj in vars(module).items()
        if inspect.isclass(obj) and name.endswith("Repository")
    ]


def test_contexts_expose_the_expected_ports() -> None:
    names = sorted(cls.__name__ for cls in _repository_ports())
    assert names == [
        "ChunkRepository",
        "CitationRepository",
        "ConversationRepository",
        "DocumentRepository",
        "DocumentVersionRepository",
        "FeedbackRepository",
        "IngestionJobRepository",
        "MemoryRepository",
        "MessageRepository",
        "SourceRepository",
    ]


def test_every_query_method_requires_workspace_id_first() -> None:
    violations: list[str] = []
    for port in _repository_ports():
        for name, member in inspect.getmembers(port, predicate=inspect.isfunction):
            if name.startswith("_") or not name.startswith(QUERY_PREFIXES):
                continue
            params = [
                p.name for p in inspect.signature(member).parameters.values() if p.name != "self"
            ]
            if not params or params[0] != "workspace_id":
                violations.append(f"{port.__name__}.{name}({', '.join(params)})")
    assert not violations, f"unscoped query methods: {violations}"


def test_ports_have_query_methods_to_enforce() -> None:
    """Guard the guard: the sweep must actually be exercising methods."""
    total = sum(
        1
        for port in _repository_ports()
        for name, _ in inspect.getmembers(port, predicate=inspect.isfunction)
        if name.startswith(QUERY_PREFIXES)
    )
    assert total >= 10
