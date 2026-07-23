"""Parsing port and value objects (doc 10 §knowledge ports).

Parsers turn raw file bytes into a normalized :class:`ParsedDocument`:
an ordered list of typed :class:`Block`s (docs/21 §4). The block IR is
the one place format weirdness dies — chunking (M05, `atlas.rag`)
consumes only blocks, so chunking logic exists once, not once per
format. Parser failures are DATA, not bugs (M04 risk note): they
surface as :class:`ParseFailed` with a stable reason code and land jobs
in the dead-letter detail, never a crash loop.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from atlas.shared.errors import AtlasError, ValidationFailed

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


# Structural block kinds present at M05 (MD/TXT/PDF). The docs/21 §4
# vocabulary (slide, sheet_region, caption) grows with formats at M12.
BlockKind = Literal["heading", "paragraph", "code", "table"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Block:
    """One structural unit of a parsed document.

    ``level`` is the heading depth (1-based) for heading blocks;
    ``page`` is the 1-based page number for paginated formats (PDF) —
    both feed citation locators and chunk boundaries.
    """

    kind: BlockKind
    text: str
    level: int | None = None
    page: int | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValidationFailed("block text must not be empty")
        if self.kind == "heading" and self.level is None:
            raise ValidationFailed("heading blocks carry a level")
        if self.level is not None and self.level < 1:
            raise ValidationFailed("heading level is 1-based")
        if self.page is not None and self.page < 1:
            raise ValidationFailed("page numbers are 1-based")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Normalized parse output.

    ``blocks`` is the ordered structural IR consumed by chunking;
    ``text`` is the extracted plain text (whole-document view);
    ``title`` is the best-effort document title; ``meta`` carries
    parser-specific facts (page count, headings) persisted onto the
    document version.
    """

    text: str
    title: str | None = None
    meta: dict[str, object] = field(default_factory=dict)
    blocks: tuple[Block, ...] = ()


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
