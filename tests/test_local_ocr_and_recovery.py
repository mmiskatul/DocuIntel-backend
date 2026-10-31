"""Local OCR (no LLM), OCR-confidence routing, derived confidence scores and multi-instance-safe job recovery."""
import base64
import io
import threading
import uuid

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.core.database import get_conn, release_conn
from app.models.document_repo import delete_document, get_document, save_document
from app.services import job_queue, vision_ocr
from app.services.pipeline import compute_confidence


def _font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _clean_receipt():
    im = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(["RECEIPT", "Receipt No: RCP-Z77", "Merchant: Cafe Aroma", "Subtotal: 12.00", "Tax: 1.20", "Total: 13.20 USD"]):
        d.text((50, 40 + i * 80), line, fill="black", font=_font(36))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _unreadable_receipt():
    im = Image.new("RGB", (700, 420), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(["RECEIPT", "Receipt No: RCP-9", "Coffee x2   8.00", "Total: 13.20 USD"]):
        d.text((40, 30 + i * 36), line, fill=(60, 60, 60), font=ImageFont.load_default())
    buf = io.BytesIO()
    im.filter(ImageFilter.GaussianBlur(1.6)).save(buf, "PNG")
    return buf.getvalue()


def _backdate(ids, seconds=3600):
    """Simulates documents whose worker died `seconds` ago (nothing has touched them since)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE documents SET updated_at = NOW() - make_interval(secs => %s) WHERE id::text = ANY(%s);", (seconds, ids))
            conn.commit()
    finally:
        release_conn(conn)


def _no_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the vision LLM must not be called")
    monkeypatch.setattr(vision_ocr, "ocr_image_vision", boom)


def test_clean_scan_is_read_locally_without_any_llm_call(monkeypatch):
    _no_llm(monkeypatch)
    vision_ocr.reset_ocr_meta()
    text = vision_ocr.ocr_image(_clean_receipt())
    meta = vision_ocr.get_ocr_meta()
    assert "RCP-Z77" in text and "13.20" in text and "Cafe Aroma" in text
    assert meta["engine"] == "local" and meta["confidence"] >= 0.8 and not meta["low_confidence"]


def test_unreliable_scan_falls_back_to_vision_and_is_flagged(monkeypatch):
    monkeypatch.setattr(vision_ocr, "ocr_image_vision", lambda b: "RECEIPT\nTotal: 13.20")
    vision_ocr.reset_ocr_meta()
    text = vision_ocr.ocr_image(_unreadable_receipt())
    meta = vision_ocr.get_ocr_meta()
    assert text == "RECEIPT\nTotal: 13.20"
    assert meta["engine"] == "vision" and meta["low_confidence"] is True and meta["confidence"] < 0.8


def test_local_only_mode_never_calls_llm_even_when_unsure(monkeypatch):
    _no_llm(monkeypatch)
    monkeypatch.setattr(vision_ocr, "OCR_ENGINE", "local")
    vision_ocr.reset_ocr_meta()
    vision_ocr.ocr_image(_unreadable_receipt())
    assert vision_ocr.get_ocr_meta()["engine"] == "local"


def test_empty_image_is_rejected():
    with pytest.raises(ValueError):
        vision_ocr.ocr_image(b"")


def test_confidence_is_derived_from_signals_not_constant():
    full = {"invoice_number": "A", "invoice_date": "2026-01-01", "supplier_name": "S", "total": 5}
    assert compute_confidence(full, False) == 1.0
    assert compute_confidence(full, True) < compute_confidence(full, False)
    assert compute_confidence(full, False, ocr_conf=0.6) == 0.6
    assert compute_confidence({"total": 5}, False) < compute_confidence(full, False)


def test_concurrent_recovery_never_claims_a_job_twice(monkeypatch):
    claimed = []
    lock = threading.Lock()

    def fake_enqueue(doc_id, *a):
        with lock:
            claimed.append(doc_id)

    monkeypatch.setattr(job_queue, "enqueue", fake_enqueue)
    ids = [str(uuid.uuid4()) for _ in range(6)]
    payload = base64.b64encode(b"a,b\n1,2").decode()
    try:
        for i in ids:
            save_document({"id": i, "filename": "stuck.csv", "file_type": "text/csv", "status": "processing", "file_base64": payload})
        _backdate(ids)
        barrier = threading.Barrier(3)

        def sweep():
            barrier.wait()
            job_queue.recover_stuck_jobs(stale_seconds=600)

        threads = [threading.Thread(target=sweep) for _ in range(3)]  # three "instances" sweeping at once
        [t.start() for t in threads]
        [t.join() for t in threads]
        mine = [c for c in claimed if c in ids]
        assert sorted(mine) == sorted(ids), "every stuck job is recovered exactly once"
    finally:
        for i in ids:
            delete_document(i)


def test_recent_or_owned_jobs_are_not_stolen(monkeypatch):
    claimed = []
    monkeypatch.setattr(job_queue, "enqueue", lambda doc_id, *a: claimed.append(doc_id))
    fresh, owned, dead = (str(uuid.uuid4()) for _ in range(3))
    payload = base64.b64encode(b"x").decode()
    try:
        for i in (fresh, owned, dead):
            save_document({"id": i, "filename": "f.csv", "status": "processing", "file_base64": payload})
        _backdate([owned, dead])        # both look abandoned...
        job_queue._inflight.add(owned)  # ...but this instance is still running `owned`
        job_queue.recover_stuck_jobs(stale_seconds=600)
        assert dead in claimed                      # abandoned by a dead instance -> recovered
        assert fresh not in claimed                 # touched seconds ago -> its owner is alive
        assert owned not in claimed                 # owned by this instance -> never stolen
        assert get_document(fresh)["status"] == "processing" and get_document(owned)["status"] == "processing"
    finally:
        job_queue._inflight.discard(owned)
        for i in (fresh, owned, dead):
            delete_document(i)


def test_glued_capitalised_words_are_split_but_ordinary_tokens_untouched():
    from app.services.local_ocr import fix_glued_words
    assert fix_glued_words("MetroIndustrial Hardware") == "Metro Industrial Hardware"
    for untouched in ("McAfee Inc", "PayPal", "INV-2026-9041", "Total: 13.20 USD", "Cafe Aroma"):
        assert fix_glued_words(untouched) == untouched
