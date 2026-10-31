"""Document repository for PostgreSQL interactions and in-memory file caching."""
import datetime
import uuid
import logging
from typing import Any, Dict, List, Optional
from psycopg2.extras import RealDictCursor
from app.core.database import get_conn, release_conn

logger = logging.getLogger("docuintel.models.document")

# Columns returned to API clients. The stored file (file_base64) and the full text are deliberately excluded:
# they can be megabytes per document and the UI polls these queries every couple of seconds.
_PUBLIC_COLUMNS = ["id", "filename", "file_type", "size_bytes", "status", "stage", "progress", "page_count", "pages_done", "error", "created_at", "updated_at"]
# Whether the original can still be read, and whether it was deliberately deleted after approval (cheap: no file bytes are read)
_ORIGINAL_FLAGS = "(file_base64 IS NOT NULL OR cloud_public_id IS NOT NULL) AS original_available, (file_removed_at IS NOT NULL) AS original_removed"
_TERMINAL = ("completed", "needs_review", "failed", "rejected")

_history_store: Dict[str, List[Dict[str, Any]]] = {}


def _add_timing(doc: Dict[str, Any]) -> None:
    """processing_ms = time from upload to the last update, for finished documents."""
    c, u = doc.get("created_at"), doc.get("updated_at")
    doc["processing_ms"] = int((u - c).total_seconds() * 1000) if doc.get("status") in _TERMINAL and c and u else None


def get_document_file(doc_id: str) -> Optional[Dict[str, Any]]:
    """Returns {bytes, mimetype, filename} for the stored original, or None."""
    import base64
    try:
        uuid.UUID(str(doc_id))
    except ValueError:
        return None
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT filename, file_type, file_base64, cloud_public_id FROM documents WHERE id = %s;", (doc_id,))
            row = cur.fetchone()
    finally:
        release_conn(conn)
    if not row:
        return None
    mimetype = row.get("file_type") or "application/octet-stream"
    if row.get("file_base64"):
        return {"bytes": base64.b64decode(row["file_base64"]), "mimetype": mimetype, "filename": row["filename"]}
    if row.get("cloud_public_id"):
        try:
            from app.services import cloud_storage
            return {"bytes": cloud_storage.download(row["cloud_public_id"]), "mimetype": mimetype, "filename": row["filename"]}
        except Exception as exc:
            logger.error(f"Could not fetch {doc_id} from Cloudinary: {exc}")
    return None


def save_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            doc_id = doc.get("id")
            cur.execute("SELECT id FROM documents WHERE id = %s;", (doc_id,))
            exists = cur.fetchone()

            if exists:
                updates = []
                params = []
                for field in ["status", "stage", "progress", "page_count", "pages_done", "error", "file_base64", "cloud_public_id", "extracted_text", "filename", "file_type", "size_bytes"]:
                    if field in doc and doc[field] is not None:
                        updates.append(f"{field} = %s")
                        params.append(doc[field])
                updates.append("updated_at = NOW()")
                params.append(doc_id)
                query = f"UPDATE documents SET {', '.join(updates)} WHERE id = %s RETURNING *;"
                cur.execute(query, tuple(params))
                res = cur.fetchone()
                conn.commit()
                return dict(res) if res else doc
            else:
                if not doc.get("filename"):
                    # A progress update for a document that no longer exists: it was deleted while a worker was still running.
                    # Only an upload (which always carries a file name) may create a row; an update must never bring a deleted document back.
                    logger.info("Ignoring an update for a document that no longer exists: %s", doc_id)
                    return doc
                query = """
                    INSERT INTO documents (id, filename, file_type, size_bytes, status, stage, progress, page_count, pages_done, error, file_base64, cloud_public_id, extracted_text, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING *;
                """
                cur.execute(query, (
                    doc_id,
                    doc.get("filename", "document.pdf"),
                    doc.get("file_type", "application/pdf"),
                    doc.get("size_bytes", 0),
                    doc.get("status", "queued"),
                    doc.get("stage", "Uploaded"),
                    doc.get("progress", 0.0),
                    doc.get("page_count", 1),
                    doc.get("pages_done", 0),
                    doc.get("error"),
                    doc.get("file_base64"),
                    doc.get("cloud_public_id"),
                    doc.get("extracted_text"),
                ))
                res = cur.fetchone()
                conn.commit()
                return dict(res) if res else doc
    except Exception as exc:
        conn.rollback()
        logger.error(f"save_document error: {exc}")
        return doc
    finally:
        release_conn(conn)


def get_document(doc_id: str) -> Optional[Dict[str, Any]]:
    try:
        uuid.UUID(str(doc_id))  # ids are UUIDs; anything else cannot exist (and would make PostgreSQL raise)
    except ValueError:
        return None
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT {', '.join(_PUBLIC_COLUMNS)}, {_ORIGINAL_FLAGS} FROM documents WHERE id = %s;", (doc_id,))
            res = cur.fetchone()
            if not res:
                return None
            doc = dict(res)
            _add_timing(doc)
            cur.execute("SELECT * FROM extraction_results WHERE document_id = %s ORDER BY id DESC LIMIT 1;", (doc_id,))
            r = cur.fetchone()
            cur.execute("SELECT count(*) AS n, bool_or(needs_review AND NOT reviewed) AS pending FROM extraction_results WHERE document_id = %s;", (doc_id,))
            agg = cur.fetchone()
            doc["invoice_count"] = int(agg["n"] or 0)
            doc["needs_review"] = bool(agg["pending"]) if agg["n"] else False
            doc["confidence"] = float(r["overall_confidence"]) if r and r.get("overall_confidence") is not None else None
            doc["summary"] = {
                "invoice_number": r.get("invoice_number") if r else None,
                "supplier_name": r.get("supplier_name") if r else None,
                "customer_name": r.get("customer_name") if r else None,
                "subtotal": float(r["subtotal"]) if r and r.get("subtotal") is not None else None,
                "tax": float(r["tax"]) if r and r.get("tax") is not None else None,
                "total": float(r["total"]) if r and r.get("total") is not None else None,
                "currency": r.get("currency", "USD") if r else "USD",
                "invoice_date": r.get("invoice_date") if r else None,
            } if r else None
            return doc
    finally:
        release_conn(conn)


def list_documents(
    q: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    supplier: Optional[str] = None,
    invoice_number: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_total: Optional[float] = None,
    max_total: Optional[float] = None,
    needs_review: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Lists documents, newest first.

    `q` matches the file name, id, extracted text or any party/number (every word must match somewhere).
    The invoice filters (supplier, invoice_number, date range, total range, needs_review) are combined with AND and must all hold for
    the SAME invoice, so a bundle that contains one cheap and one expensive invoice is not matched by "cheap supplier AND expensive total".
    Dates are compared as ISO text (YYYY-MM-DD); an invoice whose date was not read as ISO is left out when a date filter is used.
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            conditions = []
            params = []
            if status and status.lower() != "all":
                conditions.append("d.status = %s")
                params.append(status.lower())
            if q and q.strip():
                # Every word must match somewhere (filename, id, text, or any extracted party/number): AND across words, OR across fields
                for term in q.split()[:8]:
                    like = f"%{term}%"
                    conditions.append("""(
                        d.filename ILIKE %s
                        OR CAST(d.id AS text) ILIKE %s
                        OR d.extracted_text ILIKE %s
                        OR EXISTS (
                            SELECT 1 FROM extraction_results er
                            WHERE er.document_id = d.id
                              AND (er.supplier_name ILIKE %s OR er.invoice_number ILIKE %s OR er.customer_name ILIKE %s)
                        )
                    )""")
                    params.extend([like] * 6)

            invoice_conds: List[str] = []
            if supplier and supplier.strip():
                invoice_conds.append("er.supplier_name ILIKE %s")
                params.append(f"%{supplier.strip()}%")
            if invoice_number and invoice_number.strip():
                invoice_conds.append("er.invoice_number ILIKE %s")
                params.append(f"%{invoice_number.strip()}%")
            if date_from:
                invoice_conds.append("(er.invoice_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND er.invoice_date >= %s)")
                params.append(date_from)
            if date_to:
                invoice_conds.append("(er.invoice_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND er.invoice_date <= %s)")
                params.append(date_to)
            if min_total is not None:
                invoice_conds.append("er.total >= %s")
                params.append(min_total)
            if max_total is not None:
                invoice_conds.append("er.total <= %s")
                params.append(max_total)
            if needs_review:
                invoice_conds.append("(er.needs_review AND NOT er.reviewed)")
            if invoice_conds:
                conditions.append(f"EXISTS (SELECT 1 FROM extraction_results er WHERE er.document_id = d.id AND {' AND '.join(invoice_conds)})")

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT {", ".join("d." + c for c in _PUBLIC_COLUMNS)}, {_ORIGINAL_FLAGS} FROM documents d
                {where_clause}
                ORDER BY d.created_at DESC
                LIMIT %s OFFSET %s;
            """
            params.extend([limit, offset])
            cur.execute(query, tuple(params))
            docs = [dict(r) for r in cur.fetchall()]

            for doc in docs:
                _add_timing(doc)
                cur.execute("SELECT * FROM extraction_results WHERE document_id = %s ORDER BY id DESC LIMIT 1;", (doc["id"],))
                r = cur.fetchone()
                cur.execute("SELECT count(*) AS n, COALESCE(bool_or(needs_review AND NOT reviewed), false) AS pending FROM extraction_results WHERE document_id = %s;", (doc["id"],))
                agg = cur.fetchone()
                doc["invoice_count"] = int(agg["n"] or 0)
                doc["needs_review"] = bool(agg["pending"])
                doc["confidence"] = float(r["overall_confidence"]) if r and r.get("overall_confidence") is not None else None
                doc["summary"] = {
                    "invoice_number": r.get("invoice_number") if r else None,
                    "supplier_name": r.get("supplier_name") if r else None,
                    "customer_name": r.get("customer_name") if r else None,
                    "subtotal": float(r["subtotal"]) if r and r.get("subtotal") is not None else None,
                    "tax": float(r["tax"]) if r and r.get("tax") is not None else None,
                    "total": float(r["total"]) if r and r.get("total") is not None else None,
                    "currency": r.get("currency", "USD") if r else "USD",
                    "invoice_date": r.get("invoice_date") if r else None,
                }
            return docs
    finally:
        release_conn(conn)


def release_original(doc_id: str) -> bool:
    """Discards the stored original once a document is approved: the Cloudinary copy is deleted and the pointer
    (or the database fallback copy) is cleared. The extracted data and text stay. Returns True when no original
    remains afterwards; False if Cloudinary could not be reached (the pointer is kept so it can be retried)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT cloud_public_id, file_base64 IS NOT NULL AS has_b64, file_removed_at IS NOT NULL AS removed FROM documents WHERE id = %s;", (doc_id,))
            row = cur.fetchone()
            if not row:
                return False
            if not row["cloud_public_id"] and not row["has_b64"]:
                return True  # nothing stored (already removed, or never kept)
            if row["cloud_public_id"]:
                from app.services import cloud_storage
                if not cloud_storage.safe_delete(row["cloud_public_id"]):
                    logger.warning(f"Original of {doc_id} could not be deleted from Cloudinary; keeping the pointer to retry.")
                    return False
            cur.execute("UPDATE documents SET file_base64 = NULL, cloud_public_id = NULL, file_removed_at = NOW() WHERE id = %s;", (doc_id,))
            conn.commit()
            return True
    except Exception as exc:
        conn.rollback()
        logger.error(f"release_original error: {exc}")
        return False
    finally:
        release_conn(conn)


def _cloud_ids(doc_id: Optional[str] = None) -> List[str]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if doc_id:
                cur.execute("SELECT cloud_public_id FROM documents WHERE id = %s AND cloud_public_id IS NOT NULL;", (doc_id,))
            else:
                cur.execute("SELECT cloud_public_id FROM documents WHERE cloud_public_id IS NOT NULL;")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        release_conn(conn)


def delete_document(doc_id: str) -> bool:
    cloud_ids = _cloud_ids(doc_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_embeddings WHERE document_id = %s;", (doc_id,))
            cur.execute("DELETE FROM extraction_results WHERE document_id = %s;", (doc_id,))
            cur.execute("DELETE FROM processing_history WHERE document_id = %s;", (doc_id,))
            cur.execute("DELETE FROM documents WHERE id = %s;", (doc_id,))
            conn.commit()
            _history_store.pop(doc_id, None)
            from app.services import cloud_storage
            for cid in cloud_ids:
                cloud_storage.safe_delete(cid)
            return True
    except Exception as exc:
        conn.rollback()
        logger.error(f"delete_document error: {exc}")
        return False
    finally:
        release_conn(conn)


def delete_all_documents() -> bool:
    cloud_ids = _cloud_ids()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_embeddings;")
            cur.execute("DELETE FROM extraction_results;")
            cur.execute("DELETE FROM processing_history;")
            cur.execute("DELETE FROM documents;")
            conn.commit()
            _history_store.clear()
            from app.services import cloud_storage
            for cid in cloud_ids:
                cloud_storage.safe_delete(cid)
            return True
    except Exception as exc:
        conn.rollback()
        logger.error(f"delete_all_documents error: {exc}")
        return False
    finally:
        release_conn(conn)


def add_history(doc_id: str, action: str, message: str, reviewer: Optional[str] = None, stage: Optional[str] = None):
    # 1. Update in-memory store for fast read access
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if doc_id not in _history_store:
        _history_store[doc_id] = []
    _history_store[doc_id].append({
        "id": f"HIST-{len(_history_store[doc_id]) + 1}",
        "document_id": doc_id,
        "action": action,
        "message": message,
        "reviewer": reviewer,
        "stage": stage,
        "timestamp": now_str,
    })

    # 2. Persist to PostgreSQL processing_history table
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processing_history (document_id, action, message, stage, reviewer, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW());
            """, (doc_id, action, message, stage, reviewer))
            conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.warning(f"add_history db persist notice: {exc}")
    finally:
        release_conn(conn)


def get_history(doc_id: str) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, document_id, action, message, stage, reviewer, created_at AS timestamp
                FROM processing_history
                WHERE document_id = %s
                ORDER BY created_at ASC;
            """, (doc_id,))
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                for r in rows:
                    if r.get("timestamp"):
                        r["timestamp"] = r["timestamp"].isoformat() if hasattr(r["timestamp"], "isoformat") else str(r["timestamp"])
                return rows
    except Exception as exc:
        logger.warning(f"get_history db query notice, using cache: {exc}")
    finally:
        release_conn(conn)

    return _history_store.get(doc_id, [
        {
            "id": "HIST-INIT",
            "document_id": doc_id,
            "action": "Intake Complete",
            "message": "Document intake and OCR parsing completed successfully.",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    ])


def get_stats() -> Dict[str, Any]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status, count(*) FROM documents GROUP BY status;")
            counts = {r["status"]: r["count"] for r in cur.fetchall()}
            total = sum(counts.values())

            cur.execute("SELECT count(*) FROM extraction_results WHERE needs_review = TRUE;")
            review_row = cur.fetchone()
            review_count = review_row["count"] if review_row else 0

            cur.execute("SELECT AVG(overall_confidence) AS avg_conf FROM extraction_results;")
            conf_row = cur.fetchone()
            avg_conf = float(conf_row["avg_conf"]) if conf_row and conf_row.get("avg_conf") is not None else 0.0

            cur.execute("SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at)) * 1000) AS ms FROM documents WHERE status IN ('completed', 'needs_review');")
            ms_row = cur.fetchone()
            avg_ms = int(ms_row["ms"]) if ms_row and ms_row.get("ms") is not None else None

            completed_count = counts.get("completed", 0)
            completed_pct = round((completed_count / total * 100), 1) if total > 0 else 0.0

            return {
                "total": total,
                "by_status": {
                    "completed": completed_count,
                    "needs_review": counts.get("needs_review", 0),
                    "processing": counts.get("processing", 0),
                    "queued": counts.get("queued", 0),
                    "failed": counts.get("failed", 0),
                    "rejected": counts.get("rejected", 0),
                },
                "avg_processing_ms": avg_ms,
                "completed_percentage": completed_pct,
                "open_review_items": review_count,
                "avg_confidence": avg_conf,
            }
    finally:
        release_conn(conn)
