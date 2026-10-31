"""Tests for upload limits, API-key auth, durable job recovery, failure handling and large documents."""
import base64
import io
import time
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.document_repo import delete_document, get_document, save_document
from app.services import extractor, job_queue

client = TestClient(app)


# ---------------------------------------------------------------- upload limits
def test_upload_rejects_unsupported_extension():
    r = client.post("/api/v1/documents", files=[("files", ("malware.exe", io.BytesIO(b"MZ"), "application/octet-stream"))])
    assert r.status_code == 415


def test_upload_rejects_too_many_files(monkeypatch):
    import app.api.v1.documents as docs
    monkeypatch.setattr(docs, "MAX_FILES_PER_REQUEST", 1)
    files = [("files", (f"a{i}.csv", io.BytesIO(b"a,b"), "text/csv")) for i in range(2)]
    assert client.post("/api/v1/documents", files=files).status_code == 413


def test_upload_rejects_oversized_file(monkeypatch):
    import app.api.v1.documents as docs
    monkeypatch.setattr(docs, "MAX_UPLOAD_MB", 0)
    r = client.post("/api/v1/documents", files=[("files", ("big.csv", io.BytesIO(b"x" * 10), "text/csv"))])
    assert r.status_code == 413


# ---------------------------------------------------------------- authentication
def test_api_key_required_when_configured(monkeypatch):
    import app.core.security as sec
    monkeypatch.setattr(sec, "API_KEY", "s3cret")
    assert client.get("/api/v1/stats").status_code == 401
    assert client.get("/api/v1/stats", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/stats", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/api/v1/stats", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # health stays public


# ---------------------------------------------------------------- AI extraction
class _DownChain:
    def invoke(self, *a, **k):
        raise RuntimeError("llm down")


def test_llm_window_keeps_head_and_tail():
    long_text = "HEAD" + "x" * 50000 + "TAIL-TOTAL 999"
    w = extractor._llm_window(long_text)
    assert w.startswith("HEAD") and w.endswith("TAIL-TOTAL 999") and "omitted" in w
    assert len(w) < 11000
    assert extractor._llm_window("short") == "short"


def test_extraction_failure_never_invents_values(monkeypatch):
    monkeypatch.setattr(extractor, "extraction_chain", _DownChain())
    with pytest.raises(RuntimeError):
        extractor.extract_structured_fields("ACME Corp\nInvoice 1", "x.pdf")


def test_pipeline_marks_job_failed_when_llm_down(monkeypatch):
    from app.services.pipeline import run_document_pipeline
    monkeypatch.setattr(extractor, "extraction_chain", _DownChain())
    doc_id = str(uuid.uuid4())
    save_document({"id": doc_id, "filename": "i.csv", "status": "queued"})
    try:
        run_document_pipeline(doc_id, "i.csv", b"Invoice,INV-1\nTotal,10\n", "text/csv")
        doc = get_document(doc_id)
        assert doc["status"] == "failed"
    finally:
        delete_document(doc_id)


# ---------------------------------------------------------------- durable queue
def test_recover_stuck_jobs_requeues_interrupted_documents(monkeypatch):
    queued = []
    monkeypatch.setattr(job_queue, "enqueue", lambda *a: queued.append(a))
    doc_id = str(uuid.uuid4())
    save_document({
        "id": doc_id, "filename": "stuck.csv", "file_type": "text/csv", "status": "processing",
        "file_base64": base64.b64encode(b"a,b\n1,2").decode(),
    })
    try:
        assert job_queue.recover_stuck_jobs(stale_seconds=0) >= 1
        assert any(a[0] == doc_id and a[2] == b"a,b\n1,2" for a in queued)
        assert get_document(doc_id)["status"] == "queued"
    finally:
        delete_document(doc_id)


# ---------------------------------------------------------------- scale & formats
def test_1000_page_digital_pdf_is_parsed_quickly():
    doc = pymupdf.open()
    for i in range(1000):
        doc.new_page().insert_text((50, 60), f"INVOICE INV-{i:05d}\nTotal: {i}.00")
    data = doc.tobytes()
    doc.close()
    start = time.perf_counter()
    text, pages = extractor.extract_raw_text(data, "big.pdf")
    elapsed = time.perf_counter() - start
    assert pages == 1000
    assert "INV-00999" in text
    assert elapsed < 30, f"1000-page parse took {elapsed:.1f}s"


def test_xls_legacy_multi_sheet_parsing():
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook()
    for name in ("Summary", "Items"):
        ws = wb.add_sheet(name)
        ws.write(0, 0, "Invoice")
        ws.write(0, 1, name)
    buf = io.BytesIO()
    wb.save(buf)
    text, sheets = extractor.extract_raw_text(buf.getvalue(), "legacy.xls")
    assert "=== Sheet: Summary ===" in text and "=== Sheet: Items ===" in text and sheets == 2


def test_tif_images_are_routed_to_ocr(monkeypatch):
    monkeypatch.setattr(extractor, "ocr_image", lambda b: "INVOICE 1 Total 5")
    text, pages = extractor.extract_raw_text(b"fake-bytes", "scan.tif")
    assert "INVOICE" in text and pages == 1
