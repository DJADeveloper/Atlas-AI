"""Parsing adapters implementing the domain DocumentParser port.

M04 scope: Markdown, plain text, PDF (PyMuPDF). Formats join at M12
via new adapters in this package — the registry is the only wiring
point (docs/21 §parsing table).
"""

from atlas.infrastructure.parsing.registry import ParserRegistry, default_registry

__all__ = ["ParserRegistry", "default_registry"]
