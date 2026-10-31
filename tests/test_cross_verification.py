"""Spreadsheet parsing (pandas), ledger detection, PDF-vs-PDF and PDF-vs-Excel cross-verification."""
import io
import uuid

import openpyxl
import pymupdf
from fastapi.testclient import TestClient

from app.main import app
from app.models.document_repo import delete_document, get_document, save_document
from app.models.result_repo import get_extraction_results
from app.services.cross_verify import compare_records, cross_verify
from app.services.pipeline import run_document_pipeline
from app.services.spreadsheet import detect_ledger, parse_number, read_sheets, sheets_to_text

client = TestClient(app)


# ------------------------------------------------------------------ pure logic
def test_parse_number_handles_currency_and_separators():
    assert parse_number("$1,234.50") == 1234.5
    assert parse_number("1.234,50") == 1234.5
    assert parse_number("(100)") == -100.0
    assert parse_number("") is None and parse_number("n/a") is None


def test_sheets_to_text_keeps_column_alignment_and_all_sheets():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Items"
    ws.append(["Item", "Qty", "Price"])
    ws.append(["Bolts", None, 5])
    wb.create_sheet("Notes").append(["hello"])
    buf = io.BytesIO()
    wb.save(buf)
    text = sheets_to_text(read_sheets(buf.getvalue(), "x.xlsx"))
    assert "Bolts |  | 5" in text  # empty Qty cell stays empty, price stays in its own column
    assert "=== Sheet: Items ===" in text and "=== Sheet: Notes ===" in text


def test_ledger_detected_from_header_synonyms_and_totals_row_ignored():
    csv = b'Invoice No,Date,Vendor,Net,VAT,Total\nINV-1,2026-01-05,Acme,100,10,110\nINV-2,2026-01-06,Acme,"1,000.00",100,"1,100.00"\nTotal,,,,,1210\n'
    rows = detect_ledger(read_sheets(csv, "l.csv"))
    assert [r["invoice_number"] for r in rows] == ["INV-1", "INV-2"]
    assert rows[1]["total"] == 1100.0 and rows[1]["subtotal"] == 1000.0 and rows[0]["supplier_name"] == "Acme"


def test_single_invoice_spreadsheet_is_not_a_ledger():
    assert detect_ledger(read_sheets(b"Invoice Number,INV-9\nSupplier,X\nTotal,5\n", "one.csv")) is None


def test_compare_records_tolerates_formatting_but_catches_amount_differences():
    a = {"invoice_number": "INV-1", "supplier_name": "Acme Ltd", "total": 110.0, "invoice_date": "2026-01-05", "currency": "USD"}
    b = {"invoice_number": "inv 1", "supplier_name": "ACME Limited", "total": "110.00", "invoice_date": "05/01/2026", "currency": "usd"}
    assert compare_records(a, b) == []
    b["total"] = 111.5
    diffs = compare_records(a, b)
    assert len(diffs) == 1 and diffs[0]["field"] == "total" and diffs[0]["difference"] == -1.5


def test_cross_verify_reports_matches_mismatches_and_missing():
    left = [{"id": 1, "invoice_number": "A-1", "total": 10}, {"id": 2, "invoice_number": "A-2", "total": 20}, {"id": 3, "invoice_number": "A-3", "total": 30}]
    right = [{"id": 7, "invoice_number": "a1", "total": 10}, {"id": 8, "invoice_number": "A-2", "total": 25}, {"id": 9, "invoice_number": "A-4", "total": 40}]
    r = cross_verify(left, right)
    assert r["summary"]["matched"] == 1 and r["summary"]["mismatched"] == 1
    assert [x["invoice_number"] for x in r["only_in_left"]] == ["A-3"] and [x["invoice_number"] for x in r["only_in_right"]] == ["A-4"]
    assert r["verdict"] == "discrepancies_found"


# ------------------------------------------------------------------ ledger through the pipeline (no LLM involved)
def _make_ledger(tag):
    return (
        "Invoice Number,Invoice Date,Supplier,Subtotal,Tax,Total,Currency\n"
        f"L-{tag}-1,2026-03-01,Acme Ltd,100,10,110,USD\n"
        f"L-{tag}-2,2026-03-02,Acme Ltd,200,20,220,USD\n"
        f"L-{tag}-3,2026-03-03,Acme Ltd,50,5,999,USD\n"  # row 3: subtotal + tax != total
    ).encode()


def _process(doc_id, name, data, mime):
    save_document({"id": doc_id, "filename": name, "file_type": mime, "status": "queued"})
    run_document_pipeline(doc_id, name, data, mime)


def test_ledger_csv_is_parsed_without_llm_and_validated_per_row():
    tag = uuid.uuid4().hex[:6].upper()
    doc_id = str(uuid.uuid4())
    try:
        _process(doc_id, "ledger.csv", _make_ledger(tag), "text/csv")
        rows = get_extraction_results(doc_id)
        assert [r["invoice_number"] for r in rows] == [f"L-{tag}-1", f"L-{tag}-2", f"L-{tag}-3"]
        assert rows[2]["needs_review"] and not rows[0]["needs_review"]
        assert rows[0]["data"]["source"] == "ledger"
        assert get_document(doc_id)["status"] == "needs_review"
    finally:
        delete_document(doc_id)


# ------------------------------------------------------------------ end to end: PDF vs Excel ledger and PDF vs PDF
def _pdf(number, total, subtotal, tax, date="2026-03-01"):
    d = pymupdf.open()
    d.new_page().insert_text(
        (50, 60),
        f"INVOICE\nInvoice Number: {number}\nInvoice Date: {date}\nSupplier: Acme Ltd\nCustomer: Orion Retail\n"
        f"Subtotal: {subtotal:.2f}\nTax: {tax:.2f}\nTotal: {total:.2f}\nCurrency: USD",
    )
    return d.tobytes()


def test_pdf_vs_ledger_and_pdf_vs_pdf_cross_verification():
    tag = uuid.uuid4().hex[:6].upper()
    ids = {k: str(uuid.uuid4()) for k in ("ledger", "pdf1", "pdf2", "pdf1b")}
    try:
        _process(ids["ledger"], "ledger.csv", _make_ledger(tag), "text/csv")
        _process(ids["pdf1"], "inv1.pdf", _pdf(f"L-{tag}-1", 110, 100, 10), "application/pdf")  # agrees with the ledger
        _process(ids["pdf2"], "inv2.pdf", _pdf(f"L-{tag}-2", 200, 200, 20, "2026-03-02"), "application/pdf")  # ledger says 220
        # a ledger/PDF pair shares invoice numbers by design: it must not count as a duplicate
        assert "DUPLICATE_INVOICE" not in [e["code"] for r in get_extraction_results(ids["pdf1"]) for e in r["validation_errors"]]
        assert get_document(ids["pdf1"])["status"] == "completed"

        # PDF vs ledger: PDF 1 matches, PDF 2 total differs
        ok = client.post("/api/v1/documents/cross-verify", json={"left_document_id": ids["pdf1"], "right_document_id": ids["ledger"], "flag_mismatches": False}).json()
        assert ok["summary"]["matched"] == 1 and ok["summary"]["mismatched"] == 0 and ok["summary"]["only_in_right"] == 2

        bad = client.post("/api/v1/documents/cross-verify", json={"left_document_id": ids["pdf2"], "right_document_id": ids["ledger"]}).json()
        assert bad["verdict"] == "discrepancies_found" and bad["summary"]["mismatched"] == 1
        assert [d["field"] for d in bad["mismatched"][0]["differences"]] == ["total"]  # date, supplier etc. agree
        diff = bad["mismatched"][0]["differences"][0]
        assert diff["field"] == "total" and diff["left"] == 200.0 and diff["right"] == 220.0 and diff["difference"] == -20.0
        assert bad["flagged_results"] == 2
        assert get_document(ids["pdf2"])["status"] == "needs_review"
        codes = [e["code"] for e in get_extraction_results(ids["pdf2"])[0]["validation_errors"]]
        assert "CROSS_VERIFY_MISMATCH" in codes

        # PDF vs PDF: the same invoice number issued twice with different totals
        _process(ids["pdf1b"], "inv1_copy.pdf", _pdf(f"L-{tag}-1", 150, 140, 10), "application/pdf")
        pp = client.post("/api/v1/documents/cross-verify", json={"left_document_id": ids["pdf1"], "right_document_id": ids["pdf1b"], "flag_mismatches": False}).json()
        assert pp["summary"]["mismatched"] == 1 and "total" in [d["field"] for d in pp["mismatched"][0]["differences"]]
    finally:
        for i in ids.values():
            delete_document(i)


def test_cross_verify_validates_input():
    r = client.post("/api/v1/documents/cross-verify", json={"left_document_id": "x", "right_document_id": "x"})
    assert r.status_code == 400
    r = client.post("/api/v1/documents/cross-verify", json={"left_document_id": "nope1", "right_document_id": "nope2"})
    assert r.status_code == 404
