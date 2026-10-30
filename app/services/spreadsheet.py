"""Spreadsheet parsing with pandas (XLSX, XLS, CSV) and invoice-ledger detection.

* sheets_to_text keeps column alignment (empty cells stay empty), so line-item tables survive intact.
* detect_ledger recognises a table whose rows are invoices (header row with invoice number + total columns) and
  returns them as structured records without any LLM call: fast, free and deterministic.
"""
import io
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

CSV_ENCODINGS = ("utf-8-sig", "utf-16", "latin-1", "cp1252")

COLUMN_SYNONYMS = {
    "invoice_number": {"invoice number", "invoice no", "invoice #", "invoice num", "inv no", "inv #", "inv number", "invoice id", "invoice", "bill no", "bill number", "document number", "doc no"},
    "invoice_date": {"invoice date", "date", "issue date", "inv date", "bill date", "document date"},
    "supplier_name": {"supplier", "supplier name", "vendor", "vendor name", "seller", "issuer"},
    "customer_name": {"customer", "customer name", "client", "client name", "bill to", "buyer", "sold to"},
    "subtotal": {"subtotal", "sub total", "net", "net amount", "amount before tax", "taxable amount", "net total"},
    "tax": {"tax", "tax amount", "vat", "vat amount", "gst", "sales tax"},
    "total": {"total", "total amount", "grand total", "invoice total", "amount", "gross amount", "amount due", "total due"},
    "currency": {"currency", "ccy", "curr"},
}
NUMERIC_FIELDS = ("subtotal", "tax", "total")


def _norm_header(v: Any) -> str:
    return re.sub(r"[^a-z0-9# ]+", " ", str(v).lower()).strip() if v is not None and not pd.isna(v) else ""


def read_sheets(file_bytes: bytes, filename: str) -> Dict[str, pd.DataFrame]:
    """Reads every sheet as raw cells (no header inference)."""
    lower = filename.lower()
    if lower.endswith(".csv"):
        last = None
        for enc in CSV_ENCODINGS:
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=object, encoding=enc, skip_blank_lines=True, engine="python", on_bad_lines="skip")
                return {"Sheet1": df}
            except pd.errors.EmptyDataError:
                return {"Sheet1": pd.DataFrame()}
            except (UnicodeDecodeError, UnicodeError) as exc:
                last = exc
        raise ValueError(f"Corrupted or unreadable CSV file: unable to decode characters in '{filename}' ({last}).")
    engine = "xlrd" if lower.endswith(".xls") else "openpyxl"
    try:
        return pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None, dtype=object, engine=engine)
    except Exception as exc:
        kind = "legacy Excel (.xls)" if engine == "xlrd" else "Excel (.xlsx)"
        raise ValueError(f"Corrupted or invalid {kind} file: {exc}")


def _cell(v: Any) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def sheets_to_text(sheets: Dict[str, pd.DataFrame]) -> str:
    lines: List[str] = []
    for name, df in sheets.items():
        lines.append(f"=== Sheet: {name} ===")
        rows = []
        for row in df.itertuples(index=False):
            cells = [_cell(c) for c in row]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                rows.append(" | ".join(cells))
        lines.extend(rows if rows else ["[Empty Sheet]"])
        lines.append("")
    return "\n".join(lines).strip()


def parse_number(v: Any) -> Optional[float]:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^0-9.,\-]", "", s)
    if not s or s in ("-", ".", ","):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", "") if re.search(r",\d{3}$", s) else s.replace(",", ".")
    try:
        n = float(s)
    except ValueError:
        return None
    return -abs(n) if neg else n


def _date_str(v: Any) -> Optional[str]:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, pd.Timestamp, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    try:
        return pd.to_datetime(s, dayfirst=False).strftime("%Y-%m-%d") if s else None
    except Exception:
        return s or None


def detect_ledger(sheets: Dict[str, pd.DataFrame], min_rows: int = 2) -> Optional[List[Dict[str, Any]]]:
    """Returns invoice records when a sheet is an invoice table, else None (so normal LLM extraction applies)."""
    records: List[Dict[str, Any]] = []
    for sheet, df in sheets.items():
        if df.empty:
            continue
        header_idx, mapping = None, {}
        for i in range(min(15, len(df))):
            cand = {}
            for col_idx, v in enumerate(df.iloc[i].tolist()):
                h = _norm_header(v)
                for field, names in COLUMN_SYNONYMS.items():
                    if h in names and field not in cand.values():
                        cand[col_idx] = field
                        break
            fields = set(cand.values())
            if "invoice_number" in fields and "total" in fields:
                header_idx, mapping = i, cand
                break
        if header_idx is None:
            continue
        for r in range(header_idx + 1, len(df)):
            row = df.iloc[r].tolist()
            rec: Dict[str, Any] = {"line_items": [], "sheet": sheet, "row": r + 1}
            for col_idx, field in mapping.items():
                v = row[col_idx] if col_idx < len(row) else None
                if field in NUMERIC_FIELDS:
                    rec[field] = parse_number(v)
                elif field == "invoice_date":
                    rec[field] = _date_str(v)
                else:
                    rec[field] = _cell(v) or None
            if rec.get("invoice_number") and not re.match(r"(?i)^(total|grand total|subtotal)s?$", str(rec["invoice_number"])):
                rec.setdefault("currency", None)
                records.append(rec)
    return records if len(records) >= min_rows else None
