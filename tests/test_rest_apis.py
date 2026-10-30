"""Automated test suite verifying the REST APIs:
1. Document uploads (with BackgroundTasks for OCR/AI)
2. Processing status endpoint
3. Extracted results endpoint
4. Document search endpoint (keyword, semantic, RAG)
5. Manual corrections endpoint
6. Failed-job reprocessing endpoint
"""
import io
import os
import sys
import uuid
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.models.document_repo import save_document, delete_document
from app.models.result_repo import save_extraction_result

client = TestClient(app)


def test_rest_api_upload_and_background_processing():
    """1. Test REST API for document uploads with BackgroundTasks."""
    file_content = b"%PDF-1.4 1 0 obj << /Title (API Test) >> endobj trailer << /Root 1 0 R >> %%EOF"
    response = client.post(
        "/api/v1/documents/upload",
        files=[("files", ("test_invoice.pdf", io.BytesIO(file_content), "application/pdf"))],
    )
    assert response.status_code == 202
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    doc_id = data[0]["id"]
    assert data[0]["status"] == "queued"

    # Cleanup
    delete_document(doc_id)


def test_rest_api_processing_status():
    """2. Test REST API for processing status polling."""
    doc_id = str(uuid.uuid4())
    save_document({
        "id": doc_id,
        "filename": "status_check.pdf",
        "file_type": "application/pdf",
        "status": "processing",
        "stage": "Information Extraction",
        "progress": 0.65,
    })

    try:
        response = client.get(f"/api/v1/documents/{doc_id}/status")
        assert response.status_code == 200
        st = response.json()
        assert st["id"] == doc_id
        assert st["status"] == "processing"
        assert st["stage"] == "Information Extraction"
        assert st["progress"] == 0.65
    finally:
        delete_document(doc_id)


def test_rest_api_extracted_results():
    """3. Test REST API for retrieving structured extracted results."""
    doc_id = str(uuid.uuid4())
    save_document({
        "id": doc_id,
        "filename": "results_check.pdf",
        "file_type": "application/pdf",
        "status": "completed",
    })
    save_extraction_result({
        "document_id": doc_id,
        "invoice_number": "INV-API-440",
        "supplier_name": "API Logistics Corp",
        "customer_name": "Tech Corp",
        "subtotal": 1200.00,
        "tax": 100.00,
        "total": 1300.00,
        "currency": "USD",
        "data": {
            "invoice_number": "INV-API-440",
            "line_items": [{"description": "Cloud hosting", "amount": 1200.00}],
        },
    })

    try:
        response = client.get(f"/api/v1/documents/{doc_id}/results")
        assert response.status_code == 200
        res = response.json()
        assert isinstance(res, list)
        assert len(res) >= 1
        assert res[0]["invoice_number"] == "INV-API-440"
        assert res[0]["supplier_name"] == "API Logistics Corp"
        assert res[0]["total"] == 1300.00
    finally:
        delete_document(doc_id)


def test_rest_api_manual_corrections():
    """4. Test REST API for manual corrections (field edits and reconciliation)."""
    doc_id = str(uuid.uuid4())
    save_document({
        "id": doc_id,
        "filename": "correction_test.pdf",
        "file_type": "application/pdf",
        "status": "needs_review",
        "stage": "Needs Review (Arithmetic Variance)",
    })
    save_extraction_result({
        "document_id": doc_id,
        "invoice_number": "INV-OLD-01",
        "supplier_name": "Old Vendor",
        "total": 999.00,
        "data": {"invoice_number": "INV-OLD-01", "total": 999.00},
    })

    try:
        # Submit manual correction
        correction_payload = {
            "invoice_number": "INV-CORRECTED-99",
            "supplier_name": "Corrected Vendor LLC",
            "total": 1500.00,
            "reviewer": "chief_auditor@company.com",
            "notes": "Fixed supplier name and total after receipt verification.",
        }
        response = client.post(f"/api/v1/documents/{doc_id}/correct", json=correction_payload)
        assert response.status_code == 200
        resp_data = response.json()
        assert resp_data["success"] is True
        assert resp_data["status"] == "completed"

        # Verify updated results
        res_response = client.get(f"/api/v1/documents/{doc_id}/results")
        res_list = res_response.json()
        assert res_list[0]["invoice_number"] == "INV-CORRECTED-99"
        assert res_list[0]["supplier_name"] == "Corrected Vendor LLC"
        assert res_list[0]["total"] == 1500.00
    finally:
        delete_document(doc_id)


def test_rest_api_failed_job_reprocessing():
    """5. Test REST API for failed-job reprocessing."""
    doc_id = str(uuid.uuid4())
    file_bytes = b"%PDF-1.4 mock content"
    import base64
    save_document({
        "id": doc_id,
        "filename": "failed_job.pdf",
        "file_type": "application/pdf",
        "status": "failed",
        "stage": "Failed",
        "error": "Temporary network timeout",
        "file_base64": base64.b64encode(file_bytes).decode("utf-8"),
    })

    try:
        # Trigger single job reprocessing
        response = client.post(f"/api/v1/documents/{doc_id}/reprocess")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["status"] == "queued"
        assert "Reprocessing" in body["stage"]

        # Trigger batch failed-jobs reprocessing
        batch_response = client.post("/api/v1/documents/reprocess-failed")
        assert batch_response.status_code == 200
        batch_body = batch_response.json()
        assert batch_body["success"] is True
    finally:
        delete_document(doc_id)


def test_rest_api_search():
    """6. Test REST API for document search (keyword and semantic options)."""
    response = client.get("/api/v1/search?q=invoice&mode=keyword")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

