"""Exact lookups on specific data (document id, invoice number, supplier, amount, file name, `field:value` filters)."""
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from app.core.database import get_conn, release_conn

FIELD_ALIASES = {
    "id": "id", "doc": "id", "document": "id", "document_id": "id",
    "invoice": "invoice_number", "invoice_number": "invoice_number", "inv": "invoice_number", "number": "invoice_number",
    "supplier": "supplier_name", "supplier_name": "supplier_name", "vendor": "supplier_name",
    "customer": "customer_name", "customer_name": "customer_name", "client": "customer_name",
    "total": "total", "amount": "total", "subtotal": "subtotal", "tax": "tax",
    "currency": "currency", "date": "invoice_date", "invoice_date": "invoice_date",
    "file": "filename", "filename": "filename", "name": "filename", "status": "status",
}

_NORM_SQL = "regexp_replace(upper({col}), '[^A-Z0-9]', '', 'g')"


def _to_number(v: str) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _er_exists(cond: str) -> str:
    return f"EXISTS (SELECT 1 FROM extraction_results er WHERE er.document_id = d.id AND {cond})"


def find_exact(any_value: Optional[str] = None, fields: Optional[Dict[str, str]] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Returns [{id, matched_field, matched_value}], most exact first.

    * any_value: a bare identifier. Matches a document id (full, or a prefix of 8+ characters), a file name, an invoice number
      (ignoring case, spaces and punctuation: INV-001 == inv001), a supplier/customer name, or an amount (when it is a number).
    * fields: {field: value} from a scoped query such as `supplier:Acme total:1200`. Every field must match (AND), and the
      invoice fields must all hold for the SAME invoice. Text fields match the whole value, ignoring case.
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if fields:
                doc_conds: List[str] = []
                doc_params: List[Any] = []
                inv_conds: List[str] = []
                inv_params: List[Any] = []
                for key, value in fields.items():
                    col = FIELD_ALIASES.get(key.lower())
                    value = str(value).strip()
                    if not col or not value:
                        continue
                    if col == "id":
                        doc_conds.append("(CAST(d.id AS text) = %s OR CAST(d.id AS text) ILIKE %s)")
                        doc_params += [value.lower(), value.lower() + "%"]
                    elif col == "filename":
                        doc_conds.append("lower(d.filename) = lower(%s)")
                        doc_params.append(value)
                    elif col == "status":
                        doc_conds.append("d.status = %s")
                        doc_params.append(value.lower())
                    elif col in ("total", "subtotal", "tax"):
                        num = _to_number(value)
                        if num is None:
                            return []
                        inv_conds.append(f"er.{col} = %s")
                        inv_params.append(num)
                    elif col == "invoice_number":
                        inv_conds.append(_NORM_SQL.format(col="er.invoice_number") + " = " + _NORM_SQL.format(col="%s"))
                        inv_params.append(value)
                    elif col == "invoice_date":
                        inv_conds.append("er.invoice_date = %s")
                        inv_params.append(value)
                    else:  # supplier_name, customer_name, currency
                        inv_conds.append(f"lower(trim(er.{col})) = lower(%s)")
                        inv_params.append(value)
                conds, params = list(doc_conds), list(doc_params)
                if inv_conds:
                    conds.append(_er_exists(" AND ".join(inv_conds)))
                    params += inv_params
                if not conds:
                    return []
                cur.execute(f"SELECT d.id FROM documents d WHERE {' AND '.join(conds)} ORDER BY d.created_at DESC LIMIT %s;", (*params, limit))
                label = ", ".join(f"{k}={v}" for k, v in fields.items())
                return [{"id": r["id"], "matched_field": "fields", "matched_value": label} for r in cur.fetchall()]

            q = (any_value or "").strip()
            if not q:
                return []
            norm = "".join(ch for ch in q.upper() if ch.isalnum())
            num = _to_number(q)
            checks = [
                ("id", "CAST(d.id AS text) = %s", [q.lower()]),
                ("filename", "lower(d.filename) = lower(%s)", [q]),
            ]
            if len(q) >= 8:
                checks.append(("id", "CAST(d.id AS text) ILIKE %s", [q.lower() + "%"]))
            if norm:
                checks.append(("invoice_number", _er_exists(_NORM_SQL.format(col="er.invoice_number") + " = %s"), [norm]))
            checks.append(("supplier_name", _er_exists("lower(trim(er.supplier_name)) = lower(%s)"), [q]))
            checks.append(("customer_name", _er_exists("lower(trim(er.customer_name)) = lower(%s)"), [q]))
            if num is not None:
                checks.append(("total", _er_exists("er.total = %s"), [num]))

            found: Dict[str, Dict[str, Any]] = {}
            for field, cond, params in checks:  # ordered: id and file name first, amounts last
                cur.execute(f"SELECT d.id FROM documents d WHERE {cond} ORDER BY d.created_at DESC LIMIT %s;", (*params, limit))
                for r in cur.fetchall():
                    found.setdefault(r["id"], {"id": r["id"], "matched_field": field, "matched_value": q})
            return list(found.values())[:limit]
    finally:
        release_conn(conn)
