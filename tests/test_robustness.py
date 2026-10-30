"""Automated tests verifying multi-page PDFs, multiple sheets, poor image handling, corrupted files, and format rejection."""
import io
import os
import sys
import uuid
import pytest
import pymupdf
import openpyxl
from PIL import Image

# Ensure backend root is in Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.extractor import extract_raw_text
from app.services.vision_ocr import preprocess_image
from app.services.pipeline import run_document_pipeline
from app.models.document_repo import save_document, get_document, delete_document


def test_multi_page_pdf_handling():
    """Verify multi-page PDF extraction extracts all pages and reports accurate page count."""
    doc = pymupdf.open()
    for i in range(1, 4):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Invoice Section Page {i}: Total Due $1,{i}00.00")

    pdf_bytes = doc.tobytes()
    doc.close()

    text, page_count = extract_raw_text(pdf_bytes, "multi_page_invoice.pdf")
    assert page_count == 3
    assert "Page 1" in text
    assert "Page 2" in text
    assert "Page 3" in text
    assert "1,300.00" in text


def test_multi_sheet_excel_handling():
    """Verify multi-sheet Excel extraction extracts every sheet and sets page count."""
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Summary"
    ws1.append(["Vendor", "Acme Cloud Corp"])
    ws1.append(["Total", "15420.50"])

    ws2 = wb.create_sheet(title="Line Items")
    ws2.append(["Item", "Qty", "Price"])
    ws2.append(["Server Host", "2", "5000.00"])
    ws2.append(["Database Replica", "1", "5420.50"])

    buf = io.BytesIO()
    wb.save(buf)
    excel_bytes = buf.getvalue()

    text, page_count = extract_raw_text(excel_bytes, "quarterly_statement.xlsx")
    assert page_count == 2
    assert "=== Sheet: Summary ===" in text
    assert "Acme Cloud Corp" in text
    assert "=== Sheet: Line Items ===" in text
    assert "Database Replica" in text


def test_poor_quality_image_preprocessing():
    """Verify degraded, high-resolution, unoriented images are normalized cleanly."""
    img = Image.new("RGB", (2500, 2000), color=(180, 180, 180))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    raw_bytes = buf.getvalue()

    enhanced_bytes = preprocess_image(raw_bytes)
    assert len(enhanced_bytes) > 0

    res_img = Image.open(io.BytesIO(enhanced_bytes))
    assert max(res_img.size) <= 2400
    assert res_img.format == "JPEG"


def test_corrupted_file_handling():
    """Verify corrupted files and zero-byte files are caught and marked as failed safely without crashing."""
    doc_id = str(uuid.uuid4())
    save_document({
        "id": doc_id,
        "filename": "corrupted_document.pdf",
        "file_type": "application/pdf",
        "size_bytes": 10,
        "status": "queued",
        "stage": "Uploaded",
    })

    garbage_bytes = b"NOT_A_VALID_PDF_HEADER_JUST_GARBAGE"
    run_document_pipeline(doc_id, "corrupted_document.pdf", garbage_bytes, "application/pdf")

    doc = get_document(doc_id)
    assert doc is not None
    assert doc["status"] == "failed"
    assert doc["stage"] == "Corrupted File Error"
    assert doc.get("error") is not None
    assert "PDF" in doc["error"] or "unreadable" in doc["error"].lower()

    delete_document(doc_id)


def test_zero_byte_file_handling():
    """Verify zero-byte empty file raises ValueError and fails gracefully."""
    with pytest.raises(ValueError, match="empty"):
        extract_raw_text(b"", "empty_file.pdf")


def test_assignment_document_format_rejection():
    """Verify assignment task / project specification PDFs are automatically rejected by format validator."""
    doc_id = str(uuid.uuid4())
    # Generate clean PDF with assignment task content
    doc_pdf = pymupdf.open()
    page = doc_pdf.new_page(width=595, height=842)
    page.insert_text((72, 72), "Senior AI Engineer Assignment Task Description\nDeliverables:\nTask 1: Pipeline\nSubmission Deadline: 5 Days")
    pdf_bytes = doc_pdf.tobytes()
    doc_pdf.close()

    save_document({
        "id": doc_id,
        "filename": "senior_ai_assignment_task.pdf",
        "file_type": "application/pdf",
        "size_bytes": len(pdf_bytes),
        "status": "queued",
        "stage": "Uploaded",
    })

    run_document_pipeline(doc_id, "senior_ai_assignment_task.pdf", pdf_bytes, "application/pdf")

    doc = get_document(doc_id)
    assert doc is not None
    assert doc["status"] == "rejected"
    assert "Format" in doc["stage"] or "Rejected" in doc["stage"]
    assert doc.get("error") is not None
    assert "rejected" in doc["error"].lower()  # wording of the reason is LLM-generated

    delete_document(doc_id)


def test_missing_and_uncertain_information_handling():
    """Verify missing fields (no customer name, no tax stated) are extracted as None without inventing values."""
    from app.services.extractor import extract_structured_fields

    minimal_invoice_text = (
        "TAX INVOICE\n"
        "Invoice Number: INV-8821\n"
        "Date: 2026-03-20\n"
        "Vendor: Beta Corp Logistics\n"
        "Item: Storage Rental - $500.00\n"
        "Total: $500.00\n"
    )
    # Notice: customer_name, subtotal, and tax are NOT mentioned at all in the document!
    res = extract_structured_fields(minimal_invoice_text, "beta_invoice.pdf")
    assert res["is_supported_format"] is True
    assert res["invoice_number"] == "INV-8821"
    assert "Beta Corp" in (res["supplier_name"] or "")
    assert res["total"] == 500.0
    # Crucial anti-hallucination check: customer_name must NOT be invented
    assert res.get("customer_name") is None or res.get("customer_name") == ""
    # Tax was not stated: must NOT be invented
    assert res.get("tax") is None or res.get("tax") == 0.0
    # Line items: only the real stated item
    assert len(res.get("line_items", [])) >= 1

