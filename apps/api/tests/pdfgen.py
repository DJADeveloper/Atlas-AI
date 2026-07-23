"""PDF fixture generation — the one shared helper allowed pymupdf calls."""

import pymupdf


def pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return bytes(data)
