"""Parsing port and value objects (doc 10 §knowledge ports).

Parsers turn raw file bytes into a normalized :class:`ParsedDocument`.
At M04 the parsed text validates the file and yields metadata; chunking
consumes the same structure at M05. Parser failures are DATA, not bugs
(M04 risk note): they surface as :class:`ParseFailed` with a stable
reason code and land jobs in the dead-letter detail, never a crash loop.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from atlas.shared.errors import AtlasError

ParseReason = Literal[
    "unsupported_type",
    "empty_document",
    "encrypted",
    "malformed",
    "too_large",
]


class ParseFailed(AtlasError):
    """A document could not be parsed; carries a stable reason code."""

    code = "parse_failed"
    status = 422
    title = "Parse failed"

    def __init__(self, reason: ParseReason, detail: str) -> None:
        super().__init__(detail, context={"reason": reason})
        self.reason: ParseReason = reason


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Normalized parse output.

    ``text`` is the extracted plain text (consumed by M05 chunking);
    ``title`` is the best-effort document title; ``meta`` carries
    parser-specific facts (page count, headings) persisted onto the
    document version.
    """

    text: str
    title: str | None = None
    meta: dict[str, object] = field(default_factory=dict)


class DocumentParser(Protocol):
    """Port implemented by `atlas.infrastructure.parsing` adapters."""

    @property
    def name(self) -> str:
        """Stable parser identifier recorded on document_versions.parser."""
        ...

    def supports(self, path: str) -> bool:
        """Whether this parser handles the file (by extension at M04)."""
        ...

    def parse(self, raw: bytes, path: str) -> ParsedDocument:
        """Parse raw bytes; raises ParseFailed with a reason code."""
        ...
