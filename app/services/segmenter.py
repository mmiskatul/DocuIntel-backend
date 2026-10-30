"""Splits a multi-page document into per-invoice segments.

A PDF may bundle many invoices (e.g. a month-end batch). Pages are grouped by the invoice number printed on
them: a page carrying a different invoice number than the current segment starts a new segment; pages with no
invoice number (continuation pages, terms, line-item overflow) stay with the current segment.
"""
import re
from typing import Dict, List, Optional

PAGE_MARKER = re.compile(r"^--- Page (\d+) ---\s*$", re.MULTILINE)
INVOICE_NO = re.compile(
    r"(?i)\b(?:tax\s+)?(?:invoice|inv|receipt|bill|waybill)\s*(?:no\.?|number|num|#)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\-/_.]{2,})"
)


def split_pages(text: str) -> List[Dict]:
    """Returns [{page, text}] using the '--- Page N ---' markers inserted by the PDF/OCR extractors."""
    marks = list(PAGE_MARKER.finditer(text))
    pages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        pages.append({"page": int(m.group(1)), "text": text[m.end():end].strip()})
    return pages


def page_invoice_number(page_text: str) -> Optional[str]:
    m = INVOICE_NO.search(page_text)
    return m.group(1).strip(".-").upper() if m else None


def split_invoice_segments(text: str, max_segments: int = 500) -> List[Dict]:
    """Returns segments [{invoice_key, start_page, end_page, text}]. A single segment means 'not a bundle'."""
    pages = split_pages(text)
    if len(pages) < 2:
        return [{"invoice_key": None, "start_page": 1, "end_page": max(1, len(pages)), "text": text}]

    segments: List[Dict] = []
    for p in pages:
        key = page_invoice_number(p["text"])
        cur = segments[-1] if segments else None
        if cur is None or (key and cur["invoice_key"] and key != cur["invoice_key"]):
            segments.append({"invoice_key": key, "start_page": p["page"], "end_page": p["page"], "parts": [p["text"]]})
        else:
            if key and not cur["invoice_key"]:
                cur["invoice_key"] = key
            cur["end_page"] = p["page"]
            cur["parts"].append(p["text"])

    if len(segments) > max_segments:
        segments = segments[:max_segments]
    out = [{
        "invoice_key": s["invoice_key"], "start_page": s["start_page"], "end_page": s["end_page"],
        "text": "\n\n".join(f"--- Page {s['start_page']}-{s['end_page']} ---\n" + t for t in [ "\n\n".join(s["parts"]) ]),
    } for s in segments]
    return out if len({s["invoice_key"] for s in out if s["invoice_key"]}) >= 2 else [{
        "invoice_key": None, "start_page": 1, "end_page": pages[-1]["page"], "text": text,
    }]
