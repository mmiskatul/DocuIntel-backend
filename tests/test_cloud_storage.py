"""Originals live in Cloudinary while a document awaits review and are deleted once it is approved.

Cloudinary itself is replaced by an in-memory stand-in; the database is the real one.
"""
import io

import pymupdf
import pytest
from fastapi.testclient import TestClient

import app.api.v1.documents as docs
from app.core.database import get_conn, release_conn
from app.main import app
from app.models.document_repo import delete_document, get_document, release_original, save_document
from app.services import cloud_storage as cs

client = TestClient(app)


def _pdf() -> bytes:
    d = pymupdf.open()
    d.new_page(width=300, height=400).insert_text((40, 80), "Invoice 1")
    out = d.tobytes()
    d.close()
    return out


PDF = _pdf()  # built once: every call to _pdf() embeds a fresh document id


class FakeCloud:
    def __init__(self):
        self.files = {}
        self.fail_upload = False
        self.fail_delete = False

    def upload(self, doc_id, filename, data):
        if self.fail_upload:
            raise RuntimeError("cloudinary unreachable")
        pid = f"docuintel/{doc_id}/{filename}"
        self.files[pid] = data
        return pid

    def download(self, public_id, timeout=60):
        return self.files[public_id]

    def delete(self, public_id):
        if self.fail_delete:
            return False
        self.files.pop(public_id, None)
        return True


@pytest.fixture
def cloud(monkeypatch):
    fake = FakeCloud()
    monkeypatch.setattr(cs, "enabled", lambda: True)
    monkeypatch.setattr(cs, "upload", fake.upload)
    monkeypatch.setattr(cs, "download", fake.download)
    monkeypatch.setattr(cs, "delete", fake.delete)
    monkeypatch.setattr(docs, "enqueue", lambda *a, **k: None)  # no OCR/LLM in these tests
    import app.services.rag_service as rag
    monkeypatch.setattr(rag, "index_document_chunks", lambda *a, **k: None)
    return fake


def _upload(name="inv.pdf", data=None):
    r = client.post("/api/v1/documents", files=[("files", (name, io.BytesIO(data or PDF), "application/pdf"))])
    assert r.status_code == 202, r.text
    return r.json()[0]["id"]


def _columns(doc_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT file_base64 IS NOT NULL, cloud_public_id, file_removed_at IS NOT NULL FROM documents WHERE id = %s;", (doc_id,))
            return cur.fetchone()
    finally:
        release_conn(conn)


def test_upload_goes_to_cloudinary_and_preview_reads_it_back(cloud):
    doc_id = _upload()
    try:
        has_b64, pid, removed = _columns(doc_id)
        assert not has_b64 and pid == f"docuintel/{doc_id}/inv.pdf" and not removed   # nothing large in the database
        assert pid in cloud.files
        d = get_document(doc_id)
        assert d["original_available"] is True and d["original_removed"] is False

        assert client.get(f"/api/v1/documents/{doc_id}/file").content == PDF           # served from Cloudinary
        page = client.get(f"/api/v1/documents/{doc_id}/page/1/image")
        assert page.status_code == 200 and page.content[:8] == b"\x89PNG\r\n\x1a\n"       # preview drawn from it
    finally:
        delete_document(doc_id)


def test_approval_deletes_the_original_from_cloudinary(cloud):
    doc_id = _upload()
    try:
        pid = _columns(doc_id)[1]
        r = client.post(f"/api/v1/documents/{doc_id}/approve", json={"reviewer": "t"})
        assert r.status_code == 200 and r.json()["original_removed"] is True
        assert pid not in cloud.files                                                      # gone from Cloudinary

        assert _columns(doc_id) == (False, None, True)                                    # pointer cleared, removal recorded
        d = get_document(doc_id)
        assert d["status"] == "completed" and d["original_available"] is False and d["original_removed"] is True

        gone = client.get(f"/api/v1/documents/{doc_id}/file")
        assert gone.status_code == 410 and "deleted after" in gone.json()["detail"]
        assert client.get(f"/api/v1/documents/{doc_id}/page/1/image").status_code == 410
        assert client.post(f"/api/v1/documents/{doc_id}/reprocess").status_code == 409     # cannot re-read a deleted original
    finally:
        delete_document(doc_id)


def test_reconcile_and_batch_approve_also_release_the_original(cloud):
    a, b = _upload("a.pdf"), _upload("b.pdf")
    try:
        client.post(f"/api/v1/documents/{a}/reconcile", json={"total": 10.0, "action": "manual", "reviewer": "t"})
        client.post("/api/v1/documents/batch-approve", json={"document_ids": [b], "reviewer": "t"})
        assert cloud.files == {}
        assert _columns(a)[2] and _columns(b)[2]
    finally:
        delete_document(a)
        delete_document(b)


def test_deleting_a_document_removes_its_cloudinary_file(cloud):
    doc_id = _upload()
    assert len(cloud.files) == 1
    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 200
    assert cloud.files == {} and get_document(doc_id) is None


def test_upload_falls_back_to_the_database_when_cloudinary_fails(cloud):
    cloud.fail_upload = True
    doc_id = _upload()
    try:
        has_b64, pid, _ = _columns(doc_id)
        assert has_b64 and pid is None                                                    # kept in the database instead
        assert client.get(f"/api/v1/documents/{doc_id}/file").content == PDF
        client.post(f"/api/v1/documents/{doc_id}/approve", json={"reviewer": "t"})
        assert _columns(doc_id) == (False, None, True)                                    # discarded on approval as well
    finally:
        delete_document(doc_id)


def test_a_failed_cloudinary_delete_keeps_the_pointer_so_it_can_be_retried(cloud):
    doc_id = _upload()
    try:
        cloud.fail_delete = True
        r = client.post(f"/api/v1/documents/{doc_id}/approve", json={"reviewer": "t"})
        assert r.status_code == 200 and r.json()["original_removed"] is False             # approval itself still succeeds
        assert _columns(doc_id)[1] is not None and len(cloud.files) == 1
        cloud.fail_delete = False
        assert release_original(doc_id) is True                                           # retry works
        assert cloud.files == {} and _columns(doc_id) == (False, None, True)
    finally:
        delete_document(doc_id)


def test_download_url_is_signed_and_needs_no_network(monkeypatch):
    monkeypatch.setattr(cs, "CLOUDINARY_URL", "")
    monkeypatch.setattr(cs, "CLOUDINARY_CLOUD_NAME", "democloud")
    monkeypatch.setattr(cs, "CLOUDINARY_API_KEY", "key")
    monkeypatch.setattr(cs, "CLOUDINARY_API_SECRET", "secret")
    monkeypatch.setattr(cs, "_configured", False)
    assert cs.enabled()
    url = cs.download_url("docuintel/abc/inv.pdf")
    assert "/v1_1/democloud/raw/download" in url and "signature=" in url and "secret" not in url   # signed, secret not exposed
    assert cs._safe_name("../weird name?.pdf") == ".._weird_name_.pdf"
