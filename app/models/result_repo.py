"""Extraction results repository."""
import json
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional
from psycopg2.extras import RealDictCursor
from app.core.database import get_conn, release_conn

logger = logging.getLogger("docuintel.models.result")


def save_extraction_result(res_dict: Dict[str, Any], insert_new: bool = False) -> Dict[str, Any]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            res_id = res_dict.get("id")
            doc_id = res_dict.get("document_id")

            # Check if an existing extraction result exists for this document_id
            existing_id = res_id
            if not existing_id and doc_id and not insert_new:
                cur.execute("SELECT id FROM extraction_results WHERE document_id = %s ORDER BY id DESC LIMIT 1;", (doc_id,))
                existing_row = cur.fetchone()
                if existing_row:
                    existing_id = existing_row["id"]

            val_errors = res_dict.get("validation_errors") or res_dict.get("data", {}).get("issues", [])
            if existing_id:
                query = """
                    UPDATE extraction_results
                    SET invoice_number = %s,
                        invoice_date = %s,
                        supplier_name = %s,
                        customer_name = %s,
                        subtotal = %s,
                        tax = %s,
                        total = %s,
                        currency = %s,
                        data = %s,
                        confidence = %s,
                        overall_confidence = %s,
                        needs_review = %s,
                        reviewed = %s,
                        validation_errors = %s
                    WHERE id = %s
                    RETURNING *;
                """
                cur.execute(query, (
                    res_dict.get("invoice_number"),
                    res_dict.get("invoice_date"),
                    res_dict.get("supplier_name"),
                    res_dict.get("customer_name"),
                    res_dict.get("subtotal"),
                    res_dict.get("tax"),
                    res_dict.get("total"),
                    res_dict.get("currency", "USD"),
                    json.dumps(res_dict.get("data", {})),
                    json.dumps(res_dict.get("confidence", {})),
                    res_dict.get("overall_confidence", 0.95),
                    res_dict.get("needs_review", False),
                    res_dict.get("reviewed", False),
                    json.dumps(val_errors),
                    existing_id,
                ))
                row = cur.fetchone()
                conn.commit()
                return dict(row) if row else res_dict
            else:
                query = """
                    INSERT INTO extraction_results (
                        document_id, invoice_number, invoice_date, supplier_name, customer_name,
                        subtotal, tax, total, currency, data, confidence, overall_confidence,
                        needs_review, reviewed, validation_errors, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING *;
                """
                cur.execute(query, (
                    doc_id,
                    res_dict.get("invoice_number"),
                    res_dict.get("invoice_date"),
                    res_dict.get("supplier_name"),
                    res_dict.get("customer_name"),
                    res_dict.get("subtotal"),
                    res_dict.get("tax"),
                    res_dict.get("total"),
                    res_dict.get("currency", "USD"),
                    json.dumps(res_dict.get("data", {})),
                    json.dumps(res_dict.get("confidence", {})),
                    res_dict.get("overall_confidence", 0.95),
                    res_dict.get("needs_review", False),
                    res_dict.get("reviewed", False),
                    json.dumps(val_errors),
                ))
                row = cur.fetchone()
                conn.commit()
                return dict(row) if row else res_dict
    finally:
        release_conn(conn)


def delete_results_for_document(doc_id: str) -> None:
    """Removes previous extraction rows (used before re-processing so stale invoices do not linger)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM extraction_results WHERE document_id = %s;", (doc_id,))
            conn.commit()
    finally:
        release_conn(conn)


def get_extraction_results(doc_id: str) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM extraction_results WHERE document_id = %s ORDER BY id ASC;", (doc_id,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for field in ("subtotal", "tax", "total"):
                    if isinstance(r.get(field), (int, float, Decimal)):
                        r[field] = float(r[field])
            return rows
    finally:
        release_conn(conn)


def update_result_correction(res_id: int, corrections: Dict[str, Any], reviewer: str = "reviewer@company.com") -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM extraction_results WHERE id = %s;", (res_id,))
            existing = cur.fetchone()
            if not existing:
                return None
            data = existing.get("data") or {}
            data.update(corrections)
            query = """
                UPDATE extraction_results
                SET corrected_data = %s,
                    data = %s,
                    needs_review = FALSE,
                    reviewed = TRUE
                WHERE id = %s
                RETURNING *;
            """
            cur.execute(query, (json.dumps(corrections), json.dumps(data), res_id))
            updated = cur.fetchone()
            conn.commit()
            return dict(updated) if updated else None
    finally:
        release_conn(conn)


def find_duplicate_invoice(doc_id: str, invoice_number: Optional[str], supplier_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Checks if another document with the same invoice number already exists in the database."""
    if not invoice_number or not str(invoice_number).strip():
        return None

    clean_inv = str(invoice_number).strip()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if supplier_name and str(supplier_name).strip():
                clean_sup = str(supplier_name).strip()
                query = """
                    SELECT er.document_id, er.invoice_number, er.supplier_name, er.total, d.filename, d.status
                    FROM extraction_results er
                    JOIN documents d ON er.document_id = d.id
                    WHERE er.document_id != %s
                      AND COALESCE(er.data->>'source', '') <> 'ledger'
                      AND LOWER(TRIM(er.invoice_number)) = LOWER(%s)
                      AND (
                        LOWER(TRIM(er.supplier_name)) = LOWER(%s)
                        OR er.supplier_name ILIKE %s
                      )
                    LIMIT 1;
                """
                cur.execute(query, (doc_id, clean_inv, clean_sup, f"%{clean_sup}%"))
            else:
                query = """
                    SELECT er.document_id, er.invoice_number, er.supplier_name, er.total, d.filename, d.status
                    FROM extraction_results er
                    JOIN documents d ON er.document_id = d.id
                    WHERE er.document_id != %s
                      AND COALESCE(er.data->>'source', '') <> 'ledger'
                      AND LOWER(TRIM(er.invoice_number)) = LOWER(%s)
                    LIMIT 1;
                """
                cur.execute(query, (doc_id, clean_inv))

            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.warning(f"find_duplicate_invoice query error: {exc}")
        return None
    finally:
        release_conn(conn)


def append_result_issue(result_id: int, issue: Dict[str, Any], needs_review: bool) -> bool:
    """Adds a validation issue to a result row (also mirrored in data.issues) and optionally flags it for review."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT validation_errors, data, needs_review FROM extraction_results WHERE id = %s;", (result_id,))
            row = cur.fetchone()
            if not row:
                return False
            errors = [e for e in (row["validation_errors"] or []) if e.get("code") != issue.get("code")] + [issue]
            data = row["data"] or {}
            data["issues"] = errors
            cur.execute(
                "UPDATE extraction_results SET validation_errors = %s, data = %s, needs_review = %s, reviewed = CASE WHEN %s THEN FALSE ELSE reviewed END WHERE id = %s;",
                (json.dumps(errors), json.dumps(data), bool(row["needs_review"] or needs_review), needs_review, result_id),
            )
            conn.commit()
            return True
    finally:
        release_conn(conn)
