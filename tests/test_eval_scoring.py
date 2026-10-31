"""The scoring rules used by the extended evaluation, tested on their own (no AI, no database)."""
from tests.eval_scoring import (
    field_correct, hallucinated_fields, pair_invoices, parse_date, percentile, prf, score_header, score_line_items,
)


def test_numbers_match_within_a_cent_and_not_beyond():
    assert field_correct("total", 1879.20, 1879.2)
    assert field_correct("total", 100, "100.005")
    assert not field_correct("total", 100, 100.02)
    assert not field_correct("total", 100, None)


def test_dates_match_whatever_the_written_form():
    assert field_correct("invoice_date", "2026-03-15", "15 March 2026")
    assert field_correct("invoice_date", "2026-03-15", "March 15, 2026")
    assert field_correct("invoice_date", "2026-03-15", "15/03/2026")
    assert not field_correct("invoice_date", "2026-03-15", "2026-03-16")
    assert not field_correct("invoice_date", "2026-03-15", "not a date")
    assert parse_date("") is None


def test_names_ignore_case_punctuation_and_legal_suffixes_but_not_different_companies():
    assert field_correct("supplier_name", "Acme Cloud Technologies Inc.", "ACME CLOUD TECHNOLOGIES")
    assert field_correct("supplier_name", "Nordwind GmbH", "Nordwind")
    assert not field_correct("supplier_name", "Acme Cloud Technologies", "Apex Freight")


def test_currency_symbols_count_as_their_codes():
    assert field_correct("currency", "EUR", "€")
    assert field_correct("currency", "USD", "$")
    assert not field_correct("currency", "EUR", "USD")


def test_a_field_that_is_truly_missing_is_right_only_when_nothing_is_returned():
    assert field_correct("invoice_date", None, None)
    assert field_correct("customer_name", None, "")
    assert not field_correct("customer_name", None, "Invented Corp")        # an invented value is wrong
    assert hallucinated_fields({"invoice_date": None, "total": 5}, {"invoice_date": "2026-01-01", "total": 5}) == ["invoice_date"]
    assert hallucinated_fields({"invoice_date": None}, {"invoice_date": None}) == []


def test_header_scoring_reports_each_field():
    exp = {"invoice_number": "EV-1", "invoice_date": "2026-03-15", "supplier_name": "Acme", "customer_name": "Nexus", "subtotal": 100, "tax": 10, "total": 110, "currency": "USD"}
    act = dict(exp, total=111, supplier_name="Acme Ltd")
    s = score_header(exp, act)
    assert s["total"] is False and s["supplier_name"] is True and sum(s.values()) == 7


def test_line_items_need_a_similar_description_and_the_right_amount():
    exp = [{"description": "USB-C docking station", "quantity": 2, "unit_price": 50, "amount": 100},
           {"description": "Laptop stand (aluminium)", "quantity": 1, "unit_price": 30, "amount": 30}]
    act = [{"description": "Laptop stand aluminium", "quantity": 1, "unit_price": 30, "amount": 30},
           {"description": "USB-C docking station", "quantity": 2, "unit_price": 50, "amount": 100},
           {"description": "Something invented", "quantity": 1, "unit_price": 9, "amount": 9}]
    matched, e, a = score_line_items(exp, act)
    assert (matched, e, a) == (2, 2, 3)                                        # order does not matter; the extra item costs precision
    p, r, f = prf(matched, e, a)
    assert round(p, 3) == 0.667 and r == 1.0 and round(f, 3) == 0.8
    assert score_line_items(exp, [{"description": "USB-C docking station", "amount": 99}])[0] == 0   # wrong amount
    assert prf(0, 0, 0) == (1.0, 1.0, 1.0)


def test_an_actual_item_is_never_used_twice():
    exp = [{"description": "Widget", "amount": 10}, {"description": "Widget", "amount": 10}]
    act = [{"description": "Widget", "amount": 10}]
    assert score_line_items(exp, act) == (1, 2, 1)


def test_invoices_are_paired_by_number_then_by_position():
    exp = [{"invoice_number": "A-1"}, {"invoice_number": "A-2"}, {"invoice_number": "A-3"}]
    act = [{"invoice_number": "a2"}, {"invoice_number": "WRONG"}, {"invoice_number": "A1"}]
    pairs = pair_invoices(exp, act)
    assert [p[1]["invoice_number"] if p[1] else None for p in pairs] == ["A1", "a2", "WRONG"]
    assert pair_invoices(exp, [])[0][1] is None


def test_percentiles():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([10], 95) == 10
    assert percentile([], 95) == 0.0
    assert round(percentile(list(range(1, 101)), 95), 2) == 95.05


# ---- the report itself: score_case and summarize on hand-made records (no AI, no database)
from tests.evaluate_extended import score_case, summarize   # noqa: E402

_INV = {"invoice_number": "A-1", "invoice_date": "2026-01-02", "supplier_name": "Acme", "customer_name": "Nexus", "subtotal": 100, "tax": 10, "total": 110,
        "currency": "USD", "line_items": [{"description": "Widget", "quantity": 1, "unit_price": 100, "amount": 100}]}
_CASE = {"id": "X1", "category": "digital_pdf", "quality": None, "filename": "x.pdf", "expected_status": "completed", "expect_codes": [], "invoices": [_INV]}


def _row(**over):
    return {**{k: v for k, v in _INV.items() if k != "line_items"}, "supplier_name": "Acme Inc", "data": {"line_items": _INV["line_items"], "issues": []}, **over}


def test_a_correct_completed_document_is_perfect_and_not_a_silent_error():
    r = score_case(_CASE, {"status": "completed"}, [_row()], 10.0, {})
    assert r["fully_correct"] is True and r["silent_error"] is False and r["field_correct"] == r["field_checks"] == 8


def test_a_wrong_value_on_a_completed_document_is_a_silent_error_but_not_when_it_was_flagged():
    wrong = _row(total=999)
    assert score_case(_CASE, {"status": "completed"}, [wrong], 5.0, {})["silent_error"] is True
    flagged = score_case(_CASE, {"status": "needs_review"}, [wrong], 5.0, {})
    assert flagged["silent_error"] is False and "A-1:total" in flagged["wrong_fields"]


def test_an_invented_value_for_a_field_that_is_not_on_the_page_is_counted():
    case = dict(_CASE, invoices=[dict(_INV, invoice_date=None)], expected_status="needs_review", expect_codes=["MISSING_INVOICE_DATE"])
    r = score_case(case, {"status": "completed"}, [_row(invoice_date="2026-05-05")], 5.0, {})
    assert r["invented_fields"] == ["invoice_date"] and r["fully_correct"] is False


def test_refused_documents_are_scored_on_how_they_were_handled():
    case = dict(_CASE, expected_status="failed", invoices=[])
    ok = score_case(case, {"status": "failed", "error": "Corrupted File Error"}, [], 1.0, {})
    assert ok["handled"] and ok["has_reason"]
    assert not score_case(case, {"status": "completed"}, [], 1.0, {})["handled"]
    assert not score_case(case, {"status": "failed", "error": "x"}, [], 1.0, {}, crashed="ValueError: boom")["handled"]


def test_summary_counts_flagging_silent_errors_and_time():
    good = score_case(_CASE, {"status": "completed"}, [_row()], 12.0, {})
    silent = score_case(dict(_CASE, id="X2"), {"status": "completed"}, [_row(total=999)], 8.0, {})
    needs = dict(_CASE, id="X3", expected_status="needs_review", expect_codes=["TOTAL_MISMATCH"])
    caught = score_case(needs, {"status": "needs_review"}, [_row(data={"line_items": _INV["line_items"], "issues": [{"code": "TOTAL_MISMATCH"}]})], 10.0, {})
    missed = score_case(dict(needs, id="X4"), {"status": "completed"}, [_row()], 10.0, {})
    s = summarize([good, silent, caught, missed])
    assert s["review_flagging"]["flagged_correctly"] == 1 and s["review_flagging"]["missed"] == 1 and s["review_flagging"]["recall"] == 50.0
    # completed: X1 (right), X2 (wrong total, unflagged = a silent error), X4 (extraction right, but it should have been flagged = a missed flag, not a wrong value)
    assert s["silent_errors"] == {"completed_documents": 3, "wrong_but_unflagged": 1, "percent_of_completed": 33.3, "ids": ["X2"]}
    assert s["time_seconds"]["mean"] == 10.0 and s["per_field_accuracy"]["total"] == 75.0
