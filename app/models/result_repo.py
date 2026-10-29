"""Extraction results repository."""
import json
import logging
from typing import Any, Dict, List, Optional
from psycopg2.extras import RealDictCursor
from app.core.database import get_conn, release_conn

logger = logging.getLogger("docuintel.models.result")


def save_extraction_result(res_dict: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                INSERT INTO extraction_results (
                    document_id, invoice_number, invoice_date, supplier_name, customer_name,
                    subtotal, tax, total, currency, data, confidence, overall_confidence,
                    needs_review, reviewed, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING *;
            """
            cur.execute(query, (
                res_dict.get("document_id"),
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
            ))
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else res_dict
    finally:
        release_conn(conn)


def get_extraction_results(doc_id: str) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM extraction_results WHERE document_id = %s ORDER BY id ASC;", (doc_id,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if isinstance(r.get("subtotal"), (int, float)):
                    r["subtotal"] = float(r["subtotal"])
                if isinstance(r.get("tax"), (int, float)):
                    r["tax"] = float(r["tax"])
                if isinstance(r.get("total"), (int, float)):
                    r["total"] = float(r["total"])
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
