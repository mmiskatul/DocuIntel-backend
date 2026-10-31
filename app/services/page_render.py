"""Renders one page of a stored document as a PNG so the UI can preview it in any browser (no PDF viewer needed)."""
import io

import pymupdf  # PyMuPDF
from PIL import Image

MAX_SIDE_PX = 2600  # cap the output so a huge scan cannot produce a multi-megabyte preview


class PreviewUnavailable(Exception):
    """The file type cannot be drawn as an image (spreadsheets, CSV, text)."""


class PageOutOfRange(Exception):
    pass


def _png(img: Image.Image) -> bytes:
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE_PX:
        img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=False)
    return out.getvalue()


def render_page_png(data: bytes, mimetype: str, filename: str, page: int, scale: float = 1.6) -> bytes:
    """1-based page number. `scale` is relative to 72 dpi (1.6 is roughly screen resolution)."""
    scale = min(max(scale, 0.5), 3.0)
    name = (filename or "").lower()
    if mimetype == "application/pdf" or name.endswith(".pdf"):
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if page < 1 or page > pdf.page_count:
                raise PageOutOfRange(f"page {page} of {pdf.page_count}")
            pix = pdf[page - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return pix.tobytes("png")
    if (mimetype or "").startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")):
        if page != 1:
            raise PageOutOfRange(f"page {page} of 1")
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return _png(img)
    raise PreviewUnavailable(mimetype or name)
