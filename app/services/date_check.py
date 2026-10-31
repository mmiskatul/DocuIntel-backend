"""Guards against a silently wrong invoice date when the document prints it as 10/01/2026.

That form is day-first in most of the world and month-first in the US, and the model has to pick one. This module looks at the
text of the invoice itself: another date in the same document that can only be read one way (25/03/2026), or European number
formatting (2.390,52), settles the order. If the document gives no evidence, the date is kept but flagged for review instead of
being trusted.
"""
import re
from typing import Any, Dict, Optional, Tuple

_NUMERIC_DATE = re.compile(r"(?<![\d.])(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_EU_NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{2}(?!\d)|\d+,\d{2}\s*(?:€|EUR)")


def _order_evidence(text: str) -> Optional[str]:
    """'day_first', 'month_first' or None (no evidence, or evidence that contradicts itself)."""
    day_first = month_first = False
    for a, b, _ in _NUMERIC_DATE.findall(text):
        a, b = int(a), int(b)
        if a > 12 >= b:
            day_first = True
        elif b > 12 >= a:
            month_first = True
    if not day_first and not month_first:
        day_first = len(_EU_NUMBER.findall(text)) >= 2          # European number formatting: weaker evidence, used only on its own
    if day_first and month_first:
        return None
    return "day_first" if day_first else "month_first" if month_first else None


def check_invoice_date(extracted: Dict[str, Any], text: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Returns (issue or None, corrected). May replace extracted['invoice_date'] when the document shows the order it was written in."""
    iso = _ISO.match(str(extracted.get("invoice_date") or ""))
    if not iso or not text:
        return None, False
    year, month, day = (int(g) for g in iso.groups())
    printed = next(((a, b) for a, b, y in _NUMERIC_DATE.findall(text)
                    if int(y) == year and int(a) != int(b) and {int(a), int(b)} == {month, day} and int(a) <= 12 and int(b) <= 12), None)
    if not printed:
        return None, False                                        # the date is not printed in an ambiguous numeric form
    a, b = int(printed[0]), int(printed[1])
    order = _order_evidence(text)
    shown = f"{printed[0]}/{printed[1]}/{year}"
    if order is None:
        return {"code": "AMBIGUOUS_DATE", "severity": "error", "field": "invoice_date",
                "message": f"The date is printed as {shown}, which could be read day-first or month-first, and nothing else in the document settles it. "
                           f"It was read as {extracted['invoice_date']}; please confirm.", "details": {"printed": shown}}, False
    right = (year, b, a) if order == "day_first" else (year, a, b)
    right_iso = f"{right[0]:04d}-{right[1]:02d}-{right[2]:02d}"
    if right_iso == extracted["invoice_date"][:10]:
        return None, False
    was = extracted["invoice_date"]
    extracted["invoice_date"] = right_iso
    return {"code": "DATE_ORDER_CORRECTED", "severity": "info", "field": "invoice_date",
            "message": f"The date is printed as {shown} and the rest of the document is written {order.replace('_', '-')}, so it was read as {right_iso} instead of {was}.",
            "details": {"printed": shown, "was": was}}, True
