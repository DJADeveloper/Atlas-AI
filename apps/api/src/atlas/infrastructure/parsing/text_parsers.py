"""Markdown and plain-text parsers."""

import re
from pathlib import PurePath

from atlas.domain.knowledge.parsing import ParsedDocument, ParseFailed

MAX_PARSE_BYTES = 50 * 1024 * 1024  # docs/21: files beyond 50 MB are refused

_MD_HEADING = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)


def ensure_parseable_size(raw: bytes) -> None:
    if len(raw) > MAX_PARSE_BYTES:
        raise ParseFailed("too_large", f"file exceeds {MAX_PARSE_BYTES} bytes")


def _decode(raw: bytes) -> str:
    # Real-world text files are messy; replacement beats rejection for
    # the odd stray byte, while fully undecodable content still ends up
    # empty and is rejected as such.
    return raw.decode("utf-8", errors="replace")


class MarkdownParser:
    name = "markdown"
    _extensions = frozenset({".md", ".markdown"})

    def supports(self, path: str) -> bool:
        return PurePath(path).suffix.lower() in self._extensions

    def parse(self, raw: bytes, path: str) -> ParsedDocument:
        ensure_parseable_size(raw)
        text = _decode(raw)
        if not text.strip():
            raise ParseFailed("empty_document", f"no textual content in {path}")
        heading = _MD_HEADING.search(text)
        return ParsedDocument(
            text=text,
            title=heading.group("title") if heading else None,
            meta={"line_count": text.count("\n") + 1},
        )


class PlainTextParser:
    name = "plaintext"
    _extensions = frozenset({".txt", ".text"})

    def supports(self, path: str) -> bool:
        return PurePath(path).suffix.lower() in self._extensions

    def parse(self, raw: bytes, path: str) -> ParsedDocument:
        ensure_parseable_size(raw)
        text = _decode(raw)
        if not text.strip():
            raise ParseFailed("empty_document", f"no textual content in {path}")
        return ParsedDocument(text=text, title=None, meta={"line_count": text.count("\n") + 1})
