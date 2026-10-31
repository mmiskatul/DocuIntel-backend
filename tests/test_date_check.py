"""Ambiguous numeric dates (10/01/2026): corrected when the document shows its order, flagged when it does not. No AI, no database."""
from app.services.date_check import check_invoice_date


def _inv(date):
    return {"invoice_date": date}


def test_a_date_that_cannot_be_read_two_ways_is_left_alone():
    inv = _inv("2026-03-25")
    assert check_invoice_date(inv, "Invoice Date: 25/03/2026") == (None, False) and inv["invoice_date"] == "2026-03-25"
    assert check_invoice_date(_inv("2026-03-15"), "Date: 15 March 2026") == (None, False)       # not printed in numeric form


def test_an_ambiguous_date_with_no_evidence_is_flagged_not_trusted():
    inv = _inv("2026-10-01")
    issue, corrected = check_invoice_date(inv, "Invoice Date: 10/01/2026\nTotal $2,379.04")
    assert corrected is False and inv["invoice_date"] == "2026-10-01"
    assert issue["code"] == "AMBIGUOUS_DATE" and issue["severity"] == "error"


def test_another_date_in_the_document_settles_the_order_and_a_wrong_reading_is_corrected():
    inv = _inv("2026-10-01")                                                      # read month-first, but the due date proves day-first
    issue, corrected = check_invoice_date(inv, "Date: 10/01/2026\nDue date: 25/01/2026")
    assert corrected is True and inv["invoice_date"] == "2026-01-10"
    assert issue["code"] == "DATE_ORDER_CORRECTED" and issue["severity"] == "info"


def test_a_right_reading_is_left_alone_when_the_evidence_agrees():
    inv = _inv("2026-01-10")
    assert check_invoice_date(inv, "Date: 10/01/2026\nDue date: 25/01/2026") == (None, False)
    inv = _inv("2026-10-01")                                                      # month-first document: 03/25 proves it
    assert check_invoice_date(inv, "Date: 10/01/2026\nShipped: 03/25/2026") == (None, False)


def test_european_number_formatting_counts_as_day_first_evidence():
    text = "Date: 08/01/2026\nCloud hosting 6 398,42 EUR 2.390,52 EUR\nSubtotal 6.102,76 EUR"
    inv = _inv("2026-08-01")
    issue, corrected = check_invoice_date(inv, text)
    assert corrected is True and inv["invoice_date"] == "2026-01-08"


def test_conflicting_evidence_is_flagged_and_equal_day_and_month_is_not_ambiguous():
    inv = _inv("2026-10-01")
    assert check_invoice_date(inv, "Date: 10/01/2026\nA: 25/01/2026\nB: 03/25/2026")[0]["code"] == "AMBIGUOUS_DATE"
    assert check_invoice_date(_inv("2026-05-05"), "Date: 05/05/2026") == (None, False)
    assert check_invoice_date(_inv(None), "Date: 10/01/2026") == (None, False)
