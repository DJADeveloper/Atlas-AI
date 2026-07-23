"""Parser adapters: happy paths, every failure reason code, and the
structural block IR the M05 chunker consumes."""

import pymupdf
import pytest

from atlas.domain.knowledge.parsing import ParseFailed
from atlas.infrastructure.parsing import default_registry
from atlas.infrastructure.parsing.pdf_parser import PdfParser
from atlas.infrastructure.parsing.text_parsers import (
    MAX_PARSE_BYTES,
    MarkdownParser,
    PlainTextParser,
)
from atlas.rag import chunk_blocks


def _pdf_bytes(text: str = "Atlas ingestion test page.", *, encrypt: bool = False) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    if encrypt:
        return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    return document.tobytes()


class TestMarkdownParser:
    def test_extracts_title_and_text(self) -> None:
        parsed = MarkdownParser().parse(b"# Atlas Notes\n\nBody text.\n", "notes.md")
        assert parsed.title == "Atlas Notes"
        assert "Body text." in parsed.text
        assert parsed.meta["line_count"] == 4

    def test_no_heading_means_no_title(self) -> None:
        assert MarkdownParser().parse(b"just text", "a.md").title is None

    def test_empty_document_reason(self) -> None:
        with pytest.raises(ParseFailed) as failure:
            MarkdownParser().parse(b"   \n\n  ", "empty.md")
        assert failure.value.reason == "empty_document"

    def test_too_large_reason(self) -> None:
        with pytest.raises(ParseFailed) as failure:
            MarkdownParser().parse(b"x" * (MAX_PARSE_BYTES + 1), "big.md")
        assert failure.value.reason == "too_large"

    def test_supports_extensions_case_insensitively(self) -> None:
        parser = MarkdownParser()
        assert parser.supports("A.MD")
        assert parser.supports("b.markdown")
        assert not parser.supports("c.txt")

    def test_emits_heading_paragraph_and_code_blocks(self) -> None:
        source = (
            "# Title\n\nIntro paragraph\nspanning two lines.\n\n"
            "## Section\n\nBody.\n\n```python\nprint('kept whole')\n```\n"
        )
        parsed = MarkdownParser().parse(source.encode(), "doc.md")
        kinds = [block.kind for block in parsed.blocks]
        assert kinds == ["heading", "paragraph", "heading", "paragraph", "code"]
        assert parsed.blocks[0].level == 1
        assert parsed.blocks[2].level == 2
        assert parsed.blocks[1].text == "Intro paragraph\nspanning two lines."
        assert "print('kept whole')" in parsed.blocks[4].text

    def test_unterminated_fence_still_captured(self) -> None:
        parsed = MarkdownParser().parse(b"```\ncode without end\n", "odd.md")
        assert parsed.blocks[-1].kind == "code"
        assert "code without end" in parsed.blocks[-1].text

    def test_no_md_heading_is_split_by_chunking(self) -> None:
        """M05 acceptance: no heading is split mid-line for MD — through
        the real parser and the real chunker."""
        sections = "\n\n".join(
            f"## A very long section heading number {n} for the split check\n\n"
            + " ".join(f"word{n}x{w}" for w in range(180))
            + "."
            for n in range(12)
        )
        source = f"# Heading Integrity\n\n{sections}\n"
        parsed = MarkdownParser().parse(source.encode(), "headings.md")
        drafts = chunk_blocks(parsed.blocks, title=parsed.title)
        assert len(drafts) > 3
        for block in parsed.blocks:
            if block.kind == "heading":
                assert any(block.text in draft.text for draft in drafts)


class TestPlainTextParser:
    def test_parses_and_counts_lines(self) -> None:
        parsed = PlainTextParser().parse(b"line one\nline two\n", "notes.txt")
        assert parsed.title is None
        assert parsed.meta["line_count"] == 3

    def test_undecodable_bytes_degrade_to_replacement(self) -> None:
        parsed = PlainTextParser().parse(b"ok \xff\xfe bytes", "weird.txt")
        assert "ok" in parsed.text

    def test_blank_lines_separate_paragraph_blocks(self) -> None:
        parsed = PlainTextParser().parse(b"para one\nstill one\n\npara two\n", "n.txt")
        assert [block.kind for block in parsed.blocks] == ["paragraph", "paragraph"]
        assert parsed.blocks[0].text == "para one\nstill one"
        assert parsed.blocks[1].text == "para two"


class TestPdfParser:
    def test_extracts_text_and_page_count(self) -> None:
        parsed = PdfParser().parse(_pdf_bytes("Findable sentence."), "doc.pdf")
        assert "Findable sentence." in parsed.text
        assert parsed.meta["page_count"] == 1

    def test_encrypted_reason(self) -> None:
        with pytest.raises(ParseFailed) as failure:
            PdfParser().parse(_pdf_bytes(encrypt=True), "locked.pdf")
        assert failure.value.reason == "encrypted"

    def test_malformed_reason(self) -> None:
        with pytest.raises(ParseFailed) as failure:
            PdfParser().parse(b"definitely not a pdf", "broken.pdf")
        assert failure.value.reason == "malformed"

    def test_emits_page_tagged_blocks(self) -> None:
        parsed = PdfParser().parse(_pdf_bytes("Block on page one."), "doc.pdf")
        assert parsed.blocks
        assert all(block.kind == "paragraph" for block in parsed.blocks)
        assert parsed.blocks[0].page == 1
        assert "Block on page one." in parsed.blocks[0].text


class TestRegistry:
    def test_routes_by_extension(self) -> None:
        registry = default_registry()
        assert registry.parser_for("a.md").name == "markdown"
        assert registry.parser_for("b.txt").name == "plaintext"
        assert registry.parser_for("c.pdf").name == "pymupdf"

    def test_unsupported_type_reason(self) -> None:
        registry = default_registry()
        assert not registry.supports("slides.pptx")
        with pytest.raises(ParseFailed) as failure:
            registry.parser_for("slides.pptx")
        assert failure.value.reason == "unsupported_type"
