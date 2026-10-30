"""Cross-verification between two documents (PDF vs PDF, PDF vs Excel ledger, ledger vs ledger).

Every processed document, whatever its format, yields extraction rows. Two documents are compared by matching their
invoices on normalised invoice number, then comparing supplier, customer, date, currency and amounts.
"""
import difflib
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

AMOUNT_TOLERANCE = 0.01
NAME_SIMILARITY = 0.85
AMOUNT_FIELDS = ("subtotal", "tax", "total")
COMPARED_FIELDS = ("supplier_name", "customer_name", "invoice_date", "currency", "subtotal", "tax", "total")
FIELD_SEVERITY = {"total": "error", "subtotal": "error", "tax": "error", "invoice_date": "warning", "supplier_name": "warning", "customer_name": "warning", "currency": "error"}


def norm_invoice_no(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def _norm_name(v: Any) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", str(v or "").lower())
    s = re.sub(r"\b(ltd|limited|inc|incorporated|llc|gmbh|corp|corporation|co|company|plc|pvt|the)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_date(v: Any) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d %B %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s.lower()


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def compare_records(left: Dict[str, Any], right: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Returns the differences between two records of the same invoice. Missing on one side is not a difference."""
    diffs = []
    for f in COMPARED_FIELDS:
        a, b = left.get(f), right.get(f)
        if a in (None, "") or b in (None, ""):
            continue
        if f in AMOUNT_FIELDS:
            x, y = _num(a), _num(b)
            same = x is not None and y is not None and abs(x - y) <= AMOUNT_TOLERANCE
        elif f in ("supplier_name", "customer_name"):
            na, nb = _norm_name(a), _norm_name(b)
            same = na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= NAME_SIMILARITY
        elif f == "invoice_date":
            same = _norm_date(a) == _norm_date(b)
        elif f == "currency":
            same = str(a).strip().upper() == str(b).strip().upper()
        else:
            same = a == b
        if not same:
            if f in AMOUNT_FIELDS:
                a, b = _num(a), _num(b)
            diff = {"field": f, "left": a, "right": b, "severity": FIELD_SEVERITY[f]}
            if f in AMOUNT_FIELDS and a is not None and b is not None:
                diff["difference"] = round(a - b, 2)
            diffs.append(diff)
    return diffs


def cross_verify(left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Matches invoices of two documents and reports matches, mismatches and invoices present on one side only."""
    def index(rows):
        idx: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            k = norm_invoice_no(r.get("invoice_number"))
            if k:
                idx.setdefault(k, []).append(r)
        return idx

    li, ri = index(left_rows), index(right_rows)
    matched, mismatched, only_left, only_right = [], [], [], []
    for key, lrows in li.items():
        if key not in ri:
            only_left.extend({"invoice_number": r.get("invoice_number"), "total": _num(r.get("total")), "supplier_name": r.get("supplier_name"), "result_id": r.get("id")} for r in lrows)
            continue
        lrec, rrec = lrows[0], ri[key][0]
        diffs = compare_records(lrec, rrec)
        entry = {"invoice_number": lrec.get("invoice_number"), "left_result_id": lrec.get("id"), "right_result_id": rrec.get("id"), "differences": diffs}
        (mismatched if diffs else matched).append(entry)
    for key, rrows in ri.items():
        if key not in li:
            only_right.extend({"invoice_number": r.get("invoice_number"), "total": _num(r.get("total")), "supplier_name": r.get("supplier_name"), "result_id": r.get("id")} for r in rrows)
    unnumbered = sum(1 for r in left_rows + right_rows if not norm_invoice_no(r.get("invoice_number")))
    verdict = "match" if not (mismatched or only_left or only_right) and matched else ("no_comparable_invoices" if not (matched or mismatched) else "discrepancies_found")
    return {
        "verdict": verdict,
        "summary": {"left_invoices": len(left_rows), "right_invoices": len(right_rows), "matched": len(matched), "mismatched": len(mismatched),
                    "only_in_left": len(only_left), "only_in_right": len(only_right), "without_invoice_number": unnumbered},
        "matched": matched, "mismatched": mismatched, "only_in_left": only_left, "only_in_right": only_right,
    }
