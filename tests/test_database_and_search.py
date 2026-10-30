"""Automated verification tests for Task 5: Database and Search."""
import uuid
import pytest
from app.core.database import get_conn, release_conn
from app.models.document_repo import (
    save_document,
    get_document,
    list_documents,
    add_history,
    get_history,
    delete_document,
)
from app.models.result_repo import save_extraction_result, get_extraction_results
from app.models.embedding_repo import save_document_embedding, search_document_embeddings
from app.services.rag_service import generate_embedding


def test_database_stores_all_required_entities():
    """Verifies database stores metadata, extracted text, structured results, validation errors, and history."""
    doc_id = str(uuid.uuid4())
    
    # 1. Store document metadata & extracted text
    saved_doc = save_document({
        "id": doc_id,
        "filename": "INV-2026-CLOUD-001.pdf",
        "file_type": "application/pdf",
        "size_bytes": 104857,
        "status": "needs_review",
        "stage": "Information Extraction",
        "progress": 0.6,
        "page_count": 2,
        "pages_done": 2,
        "extracted_text": "INVOICE #INV-2026-CLOUD-001\nVendor: Apex Cloud Infrastructure\nTotal: $5,200.00",
    })
    assert saved_doc["id"] == doc_id
    assert saved_doc["extracted_text"] is not None

    # 2. Store structured results & validation errors
    validation_issues = [
        {
            "code": "TOTAL_MISMATCH",
            "severity": "error",
            "field": "total",
            "message": "Subtotal $5,000 + Tax $500 != Total $5,200",
        }
    ]
    saved_res = save_extraction_result({
        "document_id": doc_id,
        "invoice_number": "INV-2026-CLOUD-001",
        "invoice_date": "2026-10-30",
        "supplier_name": "Apex Cloud Infrastructure",
        "customer_name": "Global Tech Corp",
        "subtotal": 5000.00,
        "tax": 500.00,
        "total": 5200.00,
        "currency": "USD",
        "data": {
            "line_items": [{"description": "Dedicated Kubernetes Nodes", "quantity": 5, "amount": 5000.00}],
            "issues": validation_issues,
        },
        "validation_errors": validation_issues,
        "needs_review": True,
        "overall_confidence": 0.85,
    })
    assert saved_res["invoice_number"] == "INV-2026-CLOUD-001"
    assert saved_res["needs_review"] is True

    # 3. Store processing history in database
    add_history(doc_id, "OCR Parsing", "Extracted 2 pages of digital text", stage="OCR & Parsing")
    add_history(doc_id, "Validation Flagged", "Discrepancy detected between subtotal and total", stage="Needs Review")

    history = get_history(doc_id)
    assert len(history) >= 2
    actions = [h["action"] for h in history]
    assert "OCR Parsing" in actions
    assert "Validation Flagged" in actions

    # Verify extraction results retrieval includes validation errors
    res_list = get_extraction_results(doc_id)
    assert len(res_list) >= 1
    assert res_list[0]["validation_errors"] is not None
    assert len(res_list[0]["validation_errors"]) == 1
    assert res_list[0]["validation_errors"][0]["code"] == "TOTAL_MISMATCH"

    # Cleanup
    delete_document(doc_id)


def test_keyword_based_filtering():
    """Verifies keyword filtering matches filename, invoice number, supplier name, customer name, and extracted text."""
    doc_id = str(uuid.uuid4())
    save_document({
        "id": doc_id,
        "filename": "Freight_Consignment_489.pdf",
        "file_type": "application/pdf",
        "size_bytes": 45000,
        "status": "completed",
        "stage": "Completed",
        "progress": 1.0,
        "extracted_text": "Special handling for Cryogenic Refrigeration Sensors delivery",
    })
    save_extraction_result({
        "document_id": doc_id,
        "invoice_number": "WAYBILL-998811",
        "supplier_name": "Nordic Arctic Logistics",
        "customer_name": "Stockholm Biomedica AB",
        "subtotal": 3400.00,
        "tax": 600.00,
        "total": 4000.00,
        "currency": "EUR",
        "needs_review": False,
    })

    try:
        # 1. Filter by Supplier Name
        hits_supplier = list_documents(q="Nordic Arctic")
        assert any(d["id"] == doc_id for d in hits_supplier), "Should find document by supplier name"

        # 2. Filter by Invoice / Waybill Number
        hits_inv = list_documents(q="WAYBILL-998811")
        assert any(d["id"] == doc_id for d in hits_inv), "Should find document by invoice number"

        # 3. Filter by Customer Name
        hits_customer = list_documents(q="Stockholm Biomedica")
        assert any(d["id"] == doc_id for d in hits_customer), "Should find document by customer name"

        # 4. Filter by Extracted Text Content
        hits_text = list_documents(q="Cryogenic Refrigeration")
        assert any(d["id"] == doc_id for d in hits_text), "Should find document by extracted text keyword"

        # 5. Filter by Status
        hits_status = list_documents(q="Nordic", status="completed")
        assert any(d["id"] == doc_id for d in hits_status), "Should filter by completed status"

        hits_mismatch_status = list_documents(q="Nordic", status="failed")
        assert not any(d["id"] == doc_id for d in hits_mismatch_status), "Should not match wrong status"
    finally:
        delete_document(doc_id)


def test_semantic_vector_search():
    """Verifies dense semantic vector search retrieves relevant documents using cosine similarity."""
    doc_id = str(uuid.uuid4())
    chunk = "Automated high-frequency trading server rack installation and 10Gbps optical fiber leasing agreement."

    save_document({
        "id": doc_id,
        "filename": "Colocation_Datacenter_Lease.pdf",
        "status": "completed",
        "stage": "Completed & Vectorized",
        "extracted_text": chunk,
    })
    save_extraction_result({
        "document_id": doc_id,
        "invoice_number": "DATACENTER-2026-X",
        "supplier_name": "Equinix High-Speed Interconnects",
        "total": 18500.00,
        "currency": "USD",
    })

    # Generate embedding and save chunk into pgvector
    vector = generate_embedding(chunk)
    save_document_embedding(doc_id, chunk, vector)

    try:
        # Search query semantically related but using different vocabulary: "optical fiber network datacenter rental"
        query_vector = generate_embedding("optical fiber network datacenter rental server")
        results = search_document_embeddings(query_vector, limit=5)

        assert len(results) > 0
        matching = [r for r in results if r["document_id"] == doc_id]
        assert len(matching) == 1, "Semantic search should retrieve the matching document"
        assert matching[0]["similarity"] > 0.45, f"Similarity should be strong (got {matching[0]['similarity']})"
        assert matching[0]["supplier_name"] == "Equinix High-Speed Interconnects"
    finally:
        delete_document(doc_id)
