"""Fast PDF text and page extraction service using PyMuPDF."""
import logging
from typing import Tuple
import pymupdf

logger = logging.getLogger("docuintel.services.pdf_parser")


def parse_pdf(file_bytes: bytes) -> Tuple[str, int]:
    """Parses digital PDF text and counts pages in <20ms."""
    if not file_bytes or len(file_bytes) == 0:
        raise ValueError("PDF file is empty (0 bytes).")

    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        page_count = len(doc)
        if page_count == 0:
            raise ValueError("PDF contains 0 pages.")

        pages_text = []
        for i, page in enumerate(doc):
            p_txt = page.get_text().strip()
            if p_txt:
                pages_text.append(f"--- Page {i + 1} ---\n{p_txt}")

        text = "\n\n".join(pages_text).strip()
        logger.info(f"PyMuPDF extracted {len(text)} characters across {page_count} page(s).")
        return text, page_count
    except pymupdf.FileDataError as exc:
        logger.error(f"PyMuPDF FileDataError (corrupted PDF): {exc}")
        raise ValueError(f"Corrupted or unreadable PDF: {exc}")
    except Exception as exc:
        logger.error(f"PyMuPDF parsing error: {exc}")
        raise ValueError(f"Failed to read PDF document: {exc}")
