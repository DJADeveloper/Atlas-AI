"""PDF parser backed by PyMuPDF.

Failure modes are reason codes, not exceptions escaping to the worker
(M04 risk note): encrypted and malformed files dead-letter with their
reason; scanned/image-only PDFs surface as `empty_document` until OCR
arrives at M12.
"""

from pathlib import PurePath

import pymupdf

from atlas.domain.knowledge.parsing import ParsedDocument, ParseFailed
from atlas.infrastructure.parsing.text_parsers import ensure_parseable_size


class PdfParser:
    name = "pymupdf"

    def supports(self, path: str) -> bool:
        return PurePath(path).suffix.lower() == ".pdf"

    def parse(self, raw: bytes, path: str) -> ParsedDocument:
        ensure_parseable_size(raw)
        try:
            document = pymupdf.open(stream=raw, filetype="pdf")
        except Exception as error:
            raise ParseFailed("malformed", f"unreadable PDF {path}: {error}") from error
        try:
            if document.needs_pass:
                raise ParseFailed("encrypted", f"password-protected PDF: {path}")
            pages = [page.get_text() for page in document]
            text = "\n".join(pages)
            if not text.strip():
                raise ParseFailed(
                    "empty_document",
                    f"no extractable text in {path} (scanned PDFs need OCR, M12)",
                )
            raw_title = document.metadata.get("title") if document.metadata else None
            title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else None
            return ParsedDocument(
                text=text,
                title=title,
                meta={"page_count": document.page_count},
            )
        finally:
            document.close()
