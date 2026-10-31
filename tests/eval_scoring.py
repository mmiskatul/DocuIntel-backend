"""Scoring helpers for the extended evaluation (Task 7).

Everything here is plain Python with no AI and no database, so it can be unit-tested (see test_eval_scoring.py).
The rules are deliberately strict and are written down here so the reported numbers can be audited:

* numbers match within 0.01
* dates match when they are the same calendar date, however they are written ("15 March 2026" == "2026-03-15")
* names match after removing case, punctuation and legal suffixes (Inc, Ltd, GmbH ...), or at a similarity of 0.88 or more
* a field whose true value is "not present" is correct only when the system returns nothing for it
* a line item is correct when its description is similar (0.6 or more) AND its amount matches within 0.01
"""
import difflib
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser as date_parser

HEADER_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency")
AMOUNT_TOLERANCE = 0.01
NAME_SIMILARITY = 0.88
ITEM_DESCRIPTION_SIMILARITY = 0.6

_LEGAL = r"\b(inc|incorporated|ltd|limited|llc|gmbh|corp|corporation|co|company|plc|pvt|sa|ag|bv|the)\b"
_SYMBOLS = {"$": "USD", "us$": "USD", "€": "EUR", "£": "GBP", "৳": "BDT", "tk": "BDT", "bdt": "BDT", "usd": "USD", "eur": "EUR", "gbp": "GBP"}


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def norm_text(value: Any) -> str:
    s = re.sub(r"[^\w\s]", " ", str(value or "").lower())
    s = re.sub(_LEGAL, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_number_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def parse_date(value: Any) -> Optional[date]:
    if is_missing(value):
        return None
    try:
        return date_parser.parse(str(value), dayfirst=False, fuzzy=False).date()
    except (ValueError, OverflowError, TypeError):
        try:
            return date_parser.parse(str(value), dayfirst=True, fuzzy=False).date()
        except (ValueError, OverflowError, TypeError):
            return None


def to_number(value: Any) -> Optional[float]:
    if is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def currency_code(value: Any) -> Optional[str]:
    if is_missing(value):
        return None
    v = str(value).strip().lower()
    return _SYMBOLS.get(v, str(value).strip().upper())


def field_correct(field: str, expected: Any, actual: Any) -> bool:
    """True when the system's value for one header field is right. An expected 'not present' is right only if nothing was returned."""
    if is_missing(expected):
        return is_missing(actual)
    if is_missing(actual):
        return False
    if field == "invoice_number":
        return norm_number_id(expected) == norm_number_id(actual)
    if field == "invoice_date":
        e, a = parse_date(expected), parse_date(actual)
        return e is not None and e == a
    if field in ("supplier_name", "customer_name"):
        e, a = norm_text(expected), norm_text(actual)
        return e == a or difflib.SequenceMatcher(None, e, a).ratio() >= NAME_SIMILARITY
    if field in ("subtotal", "tax", "total"):
        e, a = to_number(expected), to_number(actual)
        return e is not None and a is not None and abs(e - a) <= AMOUNT_TOLERANCE
    if field == "currency":
        return currency_code(expected) == currency_code(actual)
    return str(expected).strip().lower() == str(actual).strip().lower()


def score_header(expected: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, bool]:
    return {f: field_correct(f, expected.get(f), actual.get(f)) for f in HEADER_FIELDS}


def hallucinated_fields(expected: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    """Fields whose true value is 'not present' but for which the system returned something."""
    return [f for f in HEADER_FIELDS if is_missing(expected.get(f)) and not is_missing(actual.get(f))]


def _desc_sim(a: Any, b: Any) -> float:
    return difflib.SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def _item_amount(item: Dict[str, Any]) -> Optional[float]:
    amt = to_number(item.get("amount"))
    if amt is None:
        q, p = to_number(item.get("quantity")), to_number(item.get("unit_price"))
        if q is not None and p is not None:
            amt = round(q * p, 2)
    return amt


def score_line_items(expected: List[Dict[str, Any]], actual: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """Returns (matched, expected_count, actual_count). Each actual item can be used once (greedy, best description first)."""
    used = set()
    matched = 0
    for e in expected:
        e_amt = _item_amount(e)
        best, best_sim = None, -1.0
        for i, a in enumerate(actual):
            if i in used:
                continue
            a_amt = _item_amount(a)
            if e_amt is None or a_amt is None or abs(e_amt - a_amt) > AMOUNT_TOLERANCE:
                continue
            sim = _desc_sim(e.get("description"), a.get("description"))
            if sim >= ITEM_DESCRIPTION_SIMILARITY and sim > best_sim:
                best, best_sim = i, sim
        if best is not None:
            used.add(best)
            matched += 1
    return matched, len(expected), len(actual)


def prf(matched: int, expected: int, actual: int) -> Tuple[float, float, float]:
    """precision, recall, F1 (1.0 for an empty-versus-empty comparison)."""
    if expected == 0 and actual == 0:
        return 1.0, 1.0, 1.0
    p = matched / actual if actual else 0.0
    r = matched / expected if expected else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def pair_invoices(expected: List[Dict[str, Any]], actual: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    """Pairs each expected invoice with the system's result: by invoice number first, then by position among the ones left."""
    remaining = list(range(len(actual)))
    pairs: List[Tuple[Dict[str, Any], Optional[int]]] = []
    for e in expected:
        hit = next((i for i in remaining if not is_missing(e.get("invoice_number")) and norm_number_id(actual[i].get("invoice_number")) == norm_number_id(e.get("invoice_number"))), None)
        if hit is not None:
            remaining.remove(hit)
        pairs.append((e, hit))
    leftovers = iter(remaining)
    out = []
    for e, hit in pairs:
        if hit is None:
            hit = next(leftovers, None)
        out.append((e, actual[hit] if hit is not None else None))
    return out


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)
