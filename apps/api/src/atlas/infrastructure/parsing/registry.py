"""Parser registry: path → parser resolution.

Code-registered like tools (docs/24 rationale): the supported-format
set is a reviewable property of the codebase, not runtime state.
"""

from collections.abc import Sequence

from atlas.domain.knowledge.parsing import DocumentParser, ParseFailed
from atlas.infrastructure.parsing.pdf_parser import PdfParser
from atlas.infrastructure.parsing.text_parsers import MarkdownParser, PlainTextParser


class ParserRegistry:
    def __init__(self, parsers: Sequence[DocumentParser]) -> None:
        self._parsers = list(parsers)

    def supports(self, path: str) -> bool:
        return any(parser.supports(path) for parser in self._parsers)

    def parser_for(self, path: str) -> DocumentParser:
        for parser in self._parsers:
            if parser.supports(path):
                return parser
        raise ParseFailed("unsupported_type", f"no parser for {path}")


def default_registry() -> ParserRegistry:
    return ParserRegistry([MarkdownParser(), PlainTextParser(), PdfParser()])
