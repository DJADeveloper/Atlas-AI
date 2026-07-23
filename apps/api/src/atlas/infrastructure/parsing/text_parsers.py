"""Markdown and plain-text parsers.

Since M05 both emit the structural block IR (docs/21 §4) alongside the
whole-document text: Markdown yields heading/paragraph/code blocks from
its explicit structure; plain text yields blank-line paragraphs — the
weakest structure, closest to fixed-window chunking.
"""

import re
from pathlib import PurePath

from atlas.domain.knowledge.parsing import Block, ParsedDocument, ParseFailed

MAX_PARSE_BYTES = 50 * 1024 * 1024  # docs/21: files beyond 50 MB are refused

_MD_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^(```|~~~)")


def ensure_parseable_size(raw: bytes) -> None:
    if len(raw) > MAX_PARSE_BYTES:
        raise ParseFailed("too_large", f"file exceeds {MAX_PARSE_BYTES} bytes")


def _decode(raw: bytes) -> str:
    # Real-world text files are messy; replacement beats rejection for
    # the odd stray byte, while fully undecodable content still ends up
    # empty and is rejected as such.
    return raw.decode("utf-8", errors="replace")


def paragraph_blocks(text: str) -> tuple[Block, ...]:
    """Blank-line-separated paragraphs, whitespace-normalized per line."""
    blocks: list[Block] = []
    for raw_paragraph in re.split(r"\n\s*\n", text):
        paragraph = "\n".join(line.strip() for line in raw_paragraph.splitlines() if line.strip())
        if paragraph:
            blocks.append(Block(kind="paragraph", text=paragraph))
    return tuple(blocks)


def _markdown_blocks(text: str) -> tuple[Block, ...]:
    """Headings, fenced code (kept whole, docs/21 §4), and paragraphs."""
    blocks: list[Block] = []
    paragraph_lines: list[str] = []
    code_lines: list[str] | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            blocks.append(Block(kind="paragraph", text="\n".join(paragraph_lines)))
            paragraph_lines = []

    for line in text.splitlines():
        if code_lines is not None:
            code_lines.append(line)
            if _MD_FENCE.match(line.strip()):
                blocks.append(Block(kind="code", text="\n".join(code_lines)))
                code_lines = None
            continue
        if _MD_FENCE.match(line.strip()):
            flush_paragraph()
            code_lines = [line]
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush_paragraph()
            blocks.append(
                Block(
                    kind="heading",
                    text=heading.group("title"),
                    level=len(heading.group("hashes")),
                )
            )
            continue
        if line.strip():
            paragraph_lines.append(line.strip())
        else:
            flush_paragraph()
    if code_lines:  # unterminated fence: keep the content as code
        blocks.append(Block(kind="code", text="\n".join(code_lines)))
    flush_paragraph()
    return tuple(blocks)


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
        blocks = _markdown_blocks(text)
        title = next(
            (b.text for b in blocks if b.kind == "heading" and b.level == 1),
            None,
        )
        return ParsedDocument(
            text=text,
            title=title,
            meta={"line_count": text.count("\n") + 1},
            blocks=blocks,
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
        return ParsedDocument(
            text=text,
            title=None,
            meta={"line_count": text.count("\n") + 1},
            blocks=paragraph_blocks(text),
        )
