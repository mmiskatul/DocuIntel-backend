"""Page preview: the original is drawn as a PNG so no browser PDF viewer is needed."""
import base64
import io
import uuid

import pymupdf as fitz
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.models.document_repo import delete_document, save_document

client = TestClient(app)


def _pdf(pages=2) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page(width=300, height=400).insert_text((40, 80), f"Invoice page {i + 1}")
    out = doc.tobytes()
    doc.close()
    return out


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _store(name, mime, data):
    doc_id = str(uuid.uuid4())
    save_document({"id": doc_id, "filename": name, "file_type": mime, "status": "completed",
                   "file_base64": base64.b64encode(data).decode()})
    return doc_id


def _is_png(b):
    return b[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_renders_pdf_pages_images_and_rejects_the_rest():
    ids = [
        _store("two.pdf", "application/pdf", _pdf(2)),
        _store("red.png", "image/png", _png()),
        _store("t.csv", "text/csv", b"a,b\n1,2\n"),
        _store("bad.pdf", "application/pdf", b"%PDF-1.4 not really a pdf"),
    ]
    pdf_id, img_id, csv_id, bad_id = ids
    try:
        p1 = client.get(f"/api/v1/documents/{pdf_id}/page/1/image")
        p2 = client.get(f"/api/v1/documents/{pdf_id}/page/2/image")
        assert p1.status_code == 200 and p1.headers["content-type"] == "image/png" and _is_png(p1.content)
        assert p2.status_code == 200 and _is_png(p2.content) and p1.content != p2.content  # different pages differ

        big = client.get(f"/api/v1/documents/{pdf_id}/page/1/image", params={"scale": 3}).content
        assert len(big) > len(p1.content)

        assert client.get(f"/api/v1/documents/{pdf_id}/page/3/image").status_code == 404   # past the last page
        assert client.get(f"/api/v1/documents/{pdf_id}/page/0/image").status_code == 404

        im = client.get(f"/api/v1/documents/{img_id}/page/1/image")
        assert im.status_code == 200 and _is_png(im.content)
        assert client.get(f"/api/v1/documents/{img_id}/page/2/image").status_code == 404

        assert client.get(f"/api/v1/documents/{csv_id}/page/1/image").status_code == 415   # spreadsheets/CSV: no image
        assert client.get(f"/api/v1/documents/{bad_id}/page/1/image").status_code == 422   # corrupt file, no crash
        assert client.get(f"/api/v1/documents/{uuid.uuid4()}/page/1/image").status_code == 404
    finally:
        for i in ids:
            delete_document(i)
