"""Multi-invoice PDF splitting, per-invoice results and per-invoice manual corrections."""
import uuid

import pymupdf
from fastapi.testclient import TestClient

from app.main import app
from app.models.document_repo import delete_document, get_document, save_document
from app.models.result_repo import get_extraction_results
from app.services.pipeline import run_document_pipeline
from app.services.segmenter import split_invoice_segments

client = TestClient(app)


def _marked(*pages):
    return "\n\n".join(f"--- Page {i + 1} ---\n{p}" for i, p in enumerate(pages))


def test_segmenter_groups_pages_by_invoice_number():
    text = _marked("INVOICE\nInvoice Number: A-100\nTotal 5", "line items continued", "Invoice No: B-200\nTotal 7", "Invoice Number: B-200\nTerms")
    segs = split_invoice_segments(text)
    assert [(s["invoice_key"], s["start_page"], s["end_page"]) for s in segs] == [("A-100", 1, 2), ("B-200", 3, 4)]


def test_single_invoice_spanning_pages_is_not_split():
    text = _marked("Invoice Number: A-100\nitems", "Invoice Number: A-100\nmore items", "totals, no number here")
    assert len(split_invoice_segments(text)) == 1


def test_document_without_page_markers_is_not_split():
    assert len(split_invoice_segments("Invoice Number: A-1\nTotal 5")) == 1


def test_bundle_pdf_end_to_end_and_per_invoice_correction():
    tag = uuid.uuid4().hex[:6].upper()
    a, b, c = f"BND-{tag}-A", f"BND-{tag}-B", f"BND-{tag}-C"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((50, 60), f"INVOICE\nInvoice Number: {a}\nInvoice Date: 2026-06-01\nSupplier: Bundle Co\nCustomer: Orion Retail\nWidgets 2 x 50.00 = 100.00\nSubtotal: 100.00\nTax: 10.00\nTotal: 110.00\nCurrency: USD")
    pdf.new_page().insert_text((50, 60), f"INVOICE\nInvoice Number: {b}\nInvoice Date: 2026-06-02\nSupplier: Bundle Co\nCustomer: Orion Retail\nGadgets 4 x 25.00 = 100.00")
    pdf.new_page().insert_text((50, 60), "Subtotal: 100.00\nTax: 20.00\nTotal: 120.00\nCurrency: USD")  # continuation page of invoice B
    pdf.new_page().insert_text((50, 60), f"INVOICE\nInvoice Number: {c}\nInvoice Date: 2026-06-03\nSupplier: Bundle Co\nCustomer: Orion Retail\nCables 1 x 40.00 = 40.00\nSubtotal: 40.00\nTax: 4.00\nTotal: 900.00\nCurrency: USD")
    data = pdf.tobytes()

    doc_id = str(uuid.uuid4())
    save_document({"id": doc_id, "filename": "bundle.pdf", "file_type": "application/pdf", "status": "queued"})
    try:
        run_document_pipeline(doc_id, "bundle.pdf", data, "application/pdf")

        results = get_extraction_results(doc_id)
        by_no = {r["invoice_number"]: r for r in results}
        assert set(by_no) == {a, b, c}, [r["invoice_number"] for r in results]
        assert float(by_no[a]["total"]) == 110.0
        assert float(by_no[b]["total"]) == 120.0      # total came from the continuation page
        assert by_no[b]["data"]["page_range"] == [2, 3]
        assert by_no[c]["needs_review"] and not by_no[a]["needs_review"]
        assert get_document(doc_id)["status"] == "needs_review"

        # correct only invoice C via the nested UI payload + result_id
        r = client.post(f"/api/v1/documents/{doc_id}/correct", json={"corrections": {"total": 44.0}, "result_id": by_no[c]["id"], "reviewer": "qa"})
        assert r.status_code == 200 and r.json()["status"] == "completed"
        after = {x["invoice_number"]: x for x in get_extraction_results(doc_id)}
        assert float(after[c]["total"]) == 44.0 and after[c]["reviewed"]
        assert float(after[a]["total"]) == 110.0       # untouched
        assert get_document(doc_id)["status"] == "completed"
    finally:
        delete_document(doc_id)
