"""Deleting a document while a worker is still processing it must not leave a ghost "document.pdf" behind."""
import uuid

from app.models.document_repo import delete_document, get_document, save_document
from app.services.pipeline import run_document_pipeline


def _new(name="in_flight.pdf"):
    doc_id = str(uuid.uuid4())
    save_document({"id": doc_id, "filename": name, "file_type": "application/pdf", "size_bytes": 99, "status": "processing"})
    return doc_id


def test_a_progress_update_for_a_deleted_document_does_not_bring_it_back():
    doc_id = _new()
    delete_document(doc_id)
    save_document({"id": doc_id, "status": "processing", "stage": "OCR & Parsing", "progress": 0.4})   # what a still-running worker sends
    assert get_document(doc_id) is None


def test_the_pipeline_finishing_after_a_delete_leaves_nothing_behind():
    """The real pipeline, on a file that cannot be read (so no AI call is made), for a document deleted before the worker finishes."""
    doc_id = _new("deleted_midway.pdf")
    delete_document(doc_id)
    run_document_pipeline(doc_id, "deleted_midway.pdf", b"not a real pdf", "application/pdf")   # must not raise
    assert get_document(doc_id) is None


def test_creating_a_document_still_works_and_updates_still_apply():
    doc_id = _new("normal.pdf")
    try:
        assert get_document(doc_id)["filename"] == "normal.pdf"
        save_document({"id": doc_id, "status": "completed", "stage": "Done", "progress": 1.0})
        d = get_document(doc_id)
        assert d["status"] == "completed" and d["filename"] == "normal.pdf" and d["size_bytes"] == 99
    finally:
        delete_document(doc_id)
