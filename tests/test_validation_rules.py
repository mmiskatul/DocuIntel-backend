"""Automated tests for enterprise validation rules:
1. Missing fields
2. Incorrect numeric values
3. Duplicate invoices
4. Inconsistencies between subtotal, tax, line items, and total amount
5. Manual review flagging
"""
import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.validator import validate_document_data
from app.models.result_repo import save_extraction_result
from app.models.document_repo import save_document, delete_document


def test_missing_mandatory_fields():
    """Rule 1: Identify missing mandatory fields (invoice_number, total, supplier_name)."""
    extracted = {
        "is_supported_format": True,
        "invoice_number": None,  # MISSING!
        "invoice_date": "2026-03-15",
        "supplier_name": None,   # MISSING!
        "customer_name": "Acme Inc",
        "subtotal": 1000.0,
        "tax": 100.0,
        "total": None,           # MISSING!
        "line_items": [{"description": "Service", "amount": 1000.0}],
    }

    issues, needs_review, stage = validate_document_data(extracted, str(uuid.uuid4()), "doc.pdf", check_duplicates=False)

    codes = [i["code"] for i in issues]
    assert "MISSING_INVOICE_NUMBER" in codes
    assert "MISSING_SUPPLIER_NAME" in codes
    assert "MISSING_TOTAL" in codes
    assert needs_review is True
    assert "Missing Required Fields" in (stage or "")


def test_incorrect_numeric_values():
    """Rule 2: Identify incorrect numeric values (negative totals, zero totals with items)."""
    # Case A: Negative Total
    extracted_neg = {
        "is_supported_format": True,
        "invoice_number": "INV-101",
        "invoice_date": "2026-03-15",
        "supplier_name": "Supplier A",
        "subtotal": -500.0,
        "tax": -50.0,
        "total": -550.0,
        "line_items": [{"description": "Item", "amount": -500.0}],
    }
    issues_neg, needs_review_neg, stage_neg = validate_document_data(extracted_neg, str(uuid.uuid4()), "doc.pdf", check_duplicates=False)
    codes_neg = [i["code"] for i in issues_neg]
    assert "NEGATIVE_TOTAL" in codes_neg
    assert "NEGATIVE_SUBTOTAL" in codes_neg
    assert "NEGATIVE_TAX" in codes_neg
    assert needs_review_neg is True

    # Case B: Zero Total with billable line items
    extracted_zero = {
        "is_supported_format": True,
        "invoice_number": "INV-102",
        "invoice_date": "2026-03-15",
        "supplier_name": "Supplier B",
        "subtotal": 500.0,
        "tax": 0.0,
        "total": 0.0,
        "line_items": [{"description": "Consulting", "amount": 500.0, "quantity": 1.0, "unit_price": 500.0}],
    }
    issues_zero, needs_review_zero, _ = validate_document_data(extracted_zero, str(uuid.uuid4()), "doc.pdf", check_duplicates=False)
    codes_zero = [i["code"] for i in issues_zero]
    assert "ZERO_TOTAL_WITH_ITEMS" in codes_zero
    assert needs_review_zero is True


def test_inconsistencies_between_subtotal_tax_and_total():
    """Rule 4: Identify arithmetic inconsistencies (Subtotal + Tax != Total)."""
    extracted = {
        "is_supported_format": True,
        "invoice_number": "INV-202",
        "invoice_date": "2026-03-15",
        "supplier_name": "Logistics Pro",
        "subtotal": 2890.00,
        "tax": 450.00,
        "total": 3190.00,  # 2890 + 450 = 3340 != 3190! Discrepancy of -$150!
        "line_items": [
            {"description": "Haulage", "amount": 2400.00, "quantity": 4, "unit_price": 600.00},
            {"description": "Handling", "amount": 490.00, "quantity": 1, "unit_price": 490.00},
        ],
    }

    issues, needs_review, stage = validate_document_data(extracted, str(uuid.uuid4()), "doc.pdf", check_duplicates=False)
    codes = [i["code"] for i in issues]
    assert "TOTAL_MISMATCH" in codes
    assert needs_review is True
    assert "Arithmetic Inconsistency" in (stage or "")

    # Check details of the mismatch issue
    mismatch = next(i for i in issues if i["code"] == "TOTAL_MISMATCH")
    assert mismatch["details"]["diff"] == 150.0
    assert mismatch["details"]["expected"] == 3340.0


def test_inconsistency_between_line_items_and_subtotal():
    """Rule 4A: Sum of line items does not equal stated subtotal."""
    extracted = {
        "is_supported_format": True,
        "invoice_number": "INV-303",
        "invoice_date": "2026-03-15",
        "supplier_name": "Parts Corp",
        "subtotal": 1500.00,  # Stated subtotal 1500, but items sum to 1200!
        "tax": 100.00,
        "total": 1600.00,
        "line_items": [
            {"description": "Part A", "amount": 700.00},
            {"description": "Part B", "amount": 500.00},
        ],
    }

    issues, needs_review, stage = validate_document_data(extracted, str(uuid.uuid4()), "doc.pdf", check_duplicates=False)
    codes = [i["code"] for i in issues]
    assert "LINE_ITEMS_SUBTOTAL_MISMATCH" in codes
    assert needs_review is True


def test_duplicate_invoice_detection():
    """Rule 3: Identify duplicate invoices (matching invoice_number and supplier in DB)."""
    first_doc_id = str(uuid.uuid4())
    second_doc_id = str(uuid.uuid4())
    dup_inv_number = f"INV-TEST-DUP-{uuid.uuid4().hex[:6]}"
    dup_supplier = "Acme Global Deliveries"

    # Save initial original invoice
    save_document({
        "id": first_doc_id,
        "filename": "original_invoice.pdf",
        "file_type": "application/pdf",
        "status": "completed",
    })
    save_extraction_result({
        "document_id": first_doc_id,
        "invoice_number": dup_inv_number,
        "supplier_name": dup_supplier,
        "total": 1250.00,
        "currency": "USD",
        "data": {},
    })

    try:
        # Validate second document with exact same invoice number and supplier
        extracted_second = {
            "is_supported_format": True,
            "invoice_number": dup_inv_number,
            "invoice_date": "2026-03-20",
            "supplier_name": dup_supplier,
            "subtotal": 1200.00,
            "tax": 50.00,
            "total": 1250.00,
            "line_items": [{"description": "Freight", "amount": 1200.00}],
        }

        issues, needs_review, stage = validate_document_data(extracted_second, second_doc_id, "duplicate_copy.pdf", check_duplicates=True)
        codes = [i["code"] for i in issues]
        assert "DUPLICATE_INVOICE" in codes
        assert needs_review is True
        assert "Duplicate Invoice" in (stage or "")
    finally:
        delete_document(first_doc_id)


def test_clean_valid_invoice_passes_without_review():
    """Verify clean invoice with matching math and no duplicates passes with 0 error flags."""
    extracted = {
        "is_supported_format": True,
        "invoice_number": f"INV-CLEAN-{uuid.uuid4().hex[:6]}",
        "invoice_date": "2026-03-22",
        "supplier_name": "Pristine Services LLC",
        "customer_name": "Enterprise Buyer Co",
        "subtotal": 1000.00,
        "tax": 100.00,
        "total": 1100.00,
        "currency": "USD",
        "line_items": [
            {"description": "Engineering Consulting", "quantity": 10, "unit_price": 100.00, "amount": 1000.00}
        ],
    }

    issues, needs_review, stage = validate_document_data(extracted, str(uuid.uuid4()), "pristine_invoice.pdf", check_duplicates=True)
    error_issues = [i for i in issues if i["severity"] == "error"]
    assert len(error_issues) == 0
    assert needs_review is False
    assert stage is None
