"""Validation messages use the document's own currency, never an assumed dollar sign."""
from app.services.validator import validate_document_data


def _invoice(currency):
    return {"invoice_number": "X1", "invoice_date": "2026-01-01", "supplier_name": "S", "customer_name": "C",
            "subtotal": 100, "tax": 10, "total": 999, "currency": currency, "is_supported_format": True,
            "line_items": [{"description": "w", "quantity": 1, "unit_price": 100, "amount": 100}]}


def test_arithmetic_message_names_the_invoice_currency():
    issues, needs_review, _ = validate_document_data(_invoice("EUR"), "cur-1", "t.pdf", check_duplicates=False)
    msg = next(i["message"] for i in issues if i["code"] == "TOTAL_MISMATCH")
    assert needs_review
    assert "EUR 889.00" in msg and "$" not in msg


def test_message_has_plain_numbers_when_currency_is_unknown():
    issues, _, _ = validate_document_data(_invoice(None), "cur-2", "t.pdf", check_duplicates=False)
    msg = next(i["message"] for i in issues if i["code"] == "TOTAL_MISMATCH")
    assert "889.00" in msg and "$" not in msg
