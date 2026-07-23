"""Parser adapters: happy paths and every failure reason code."""

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


class TestPlainTextParser:
    def test_parses_and_counts_lines(self) -> None:
        parsed = PlainTextParser().parse(b"line one\nline two\n", "notes.txt")
        assert parsed.title is None
        assert parsed.meta["line_count"] == 3

    def test_undecodable_bytes_degrade_to_replacement(self) -> None:
        parsed = PlainTextParser().parse(b"ok \xff\xfe bytes", "weird.txt")
        assert "ok" in parsed.text


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
