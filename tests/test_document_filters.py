"""Document list filters: supplier, invoice number, date range, total range, needs-review, and rejection of bad input.

Every document here carries a unique supplier tag, so the filters are checked against exactly these documents
and never touch (or depend on) anything else in the database.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.document_repo import delete_document, save_document
from app.models.result_repo import save_extraction_result

client = TestClient(app)


def _doc(filename):
    doc_id = str(uuid.uuid4())
    save_document({"id": doc_id, "filename": filename, "file_type": "application/pdf", "status": "completed"})
    return doc_id


def _result(doc_id, supplier, number, date, total, needs_review=False, reviewed=False, new=False):
    save_extraction_result({
        "document_id": doc_id, "invoice_number": number, "invoice_date": date, "supplier_name": supplier, "customer_name": "QA Retail",
        "subtotal": total, "tax": 0, "total": total, "currency": "USD", "data": {"line_items": []},
        "needs_review": needs_review, "reviewed": reviewed, "overall_confidence": 0.9,
    }, insert_new=new)


@pytest.fixture
def docs():
    tag = uuid.uuid4().hex[:8].upper()
    sup = f"Zeta{tag} Trading"
    ids = {
        "cheap_early": _doc(f"{tag}_a.pdf"),
        "mid_late": _doc(f"{tag}_b.pdf"),
        "pricey_review": _doc(f"{tag}_c.pdf"),
        "odd_date": _doc(f"{tag}_d.pdf"),
        "bundle": _doc(f"{tag}_e.pdf"),
        "other_supplier": _doc(f"{tag}_f.pdf"),
    }
    _result(ids["cheap_early"], sup, f"{tag}-001", "2026-01-10", 100.00)
    _result(ids["mid_late"], sup, f"{tag}-002", "2026-06-20", 500.00)
    _result(ids["pricey_review"], sup, f"{tag}-003", "2026-03-05", 1500.00, needs_review=True)
    _result(ids["odd_date"], sup, f"{tag}-004", "30/10/2026", 700.00)           # a date that was not read as ISO
    _result(ids["bundle"], sup, f"{tag}-005", "2026-02-01", 100.00)              # a bundle: one cheap and one expensive invoice
    _result(ids["bundle"], sup, f"{tag}-006", "2026-02-02", 900.00, new=True)
    _result(ids["other_supplier"], f"Different{tag} Ltd", f"{tag}-007", "2026-04-04", 300.00)
    yield tag, sup, ids
    for i in ids.values():
        delete_document(i)


def _names(tag, **params):
    r = client.get("/api/v1/documents", params={"q": tag, "limit": 100, **params})
    assert r.status_code == 200, r.text
    return sorted(d["filename"].replace(f"{tag}_", "").replace(".pdf", "") for d in r.json())


def test_supplier_filter_matches_part_of_the_name_in_any_case(docs):
    tag, sup, _ = docs
    assert _names(tag, supplier=f"zeta{tag.lower()}") == ["a", "b", "c", "d", "e"]          # lower-case, part of the name
    assert _names(tag, supplier=f"different{tag}") == ["f"]
    assert _names(tag, supplier="no such supplier") == []


def test_invoice_number_filter(docs):
    tag, _, _ = docs
    assert _names(tag, invoice_number=f"{tag}-003") == ["c"]
    assert _names(tag, invoice_number="-00") == ["a", "b", "c", "d", "e", "f"]


def test_date_range_is_inclusive_and_skips_dates_that_are_not_iso(docs):
    tag, _, _ = docs
    assert _names(tag, date_from="2026-03-05") == ["b", "c", "f"]                               # 03-05 itself is included
    assert _names(tag, date_from="2026-01-10", date_to="2026-01-10") == ["a"]                  # both ends included
    in_q1 = _names(tag, date_from="2026-01-01", date_to="2026-03-31")
    assert in_q1 == ["a", "c", "e"]
    assert "d" not in _names(tag, date_from="2026-01-01")                                        # "30/10/2026" is not comparable, so it is left out
    assert "d" in _names(tag)                                                                    # ...but it is still listed without a date filter


def test_total_range(docs):
    tag, _, _ = docs
    assert _names(tag, min_total=500) == ["b", "c", "d", "e"]          # 500, 1500, 700, and the bundle's 900
    assert _names(tag, max_total=300) == ["a", "e", "f"]               # 100, the bundle's 100, and 300
    assert _names(tag, min_total=500, max_total=800) == ["b", "d"]
    assert _names(tag, min_total=100, max_total=100) == ["a", "e"]


def test_needs_review_only(docs):
    tag, _, ids = docs
    assert _names(tag, needs_review="true") == ["c"]
    # once a person has reviewed it, it no longer counts as waiting
    _result(ids["pricey_review"], f"Zeta{tag} Trading", f"{tag}-003", "2026-03-05", 1500.00, needs_review=True, reviewed=True)
    assert _names(tag, needs_review="true") == []


def test_filters_must_hold_for_the_same_invoice_in_a_bundle(docs):
    tag, sup, _ = docs
    # the bundle has a 100 invoice and a 900 invoice from the same supplier; "total over 800 AND date in early Feb" must be judged per invoice
    assert "e" in _names(tag, min_total=800)                                                     # the 900 invoice
    assert "e" in _names(tag, min_total=800, date_from="2026-02-02", date_to="2026-02-02")      # ...which is dated 02-02
    assert "e" not in _names(tag, min_total=800, date_from="2026-02-01", date_to="2026-02-01")  # the invoice dated 02-01 costs 100, not 800+


def test_filters_combine_with_status_and_text(docs):
    tag, sup, _ = docs
    assert _names(tag, supplier=f"zeta{tag.lower()}", min_total=500, status="completed") == ["b", "c", "d", "e"]
    assert _names(tag, supplier=f"zeta{tag.lower()}", max_total=300, status="completed") == ["a", "e"]
    assert _names(tag, supplier=f"zeta{tag.lower()}", status="rejected") == []


@pytest.mark.parametrize("params", [
    {"date_from": "2026-13-40"},                             # not a date
    {"date_from": "2026-05-01", "date_to": "2026-04-01"},    # backwards range
    {"min_total": 900, "max_total": 100},                    # backwards range
    {"min_total": -5},                                       # negative
    {"limit": 0},
    {"limit": 100000},
    {"offset": -1},
    {"needs_review": "maybe"},
])
def test_bad_filter_input_is_rejected_with_a_clear_422(params):
    r = client.get("/api/v1/documents", params=params)
    assert r.status_code == 422, (params, r.text)


def test_no_filters_still_lists_documents():
    r = client.get("/api/v1/documents", params={"limit": 5})
    assert r.status_code == 200 and isinstance(r.json(), list)
