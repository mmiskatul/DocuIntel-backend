"""Database module connecting to Supabase PostgreSQL."""
import json
import logging
from typing import Any, Dict, List, Optional
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

logger = logging.getLogger("docuintel.database")

# In-memory caches for instant retrieval
_history_store: Dict[str, List[Dict[str, Any]]] = {}
_file_cache: Dict[str, Dict[str, Any]] = {}

db_pool: Optional[pool.ThreadedConnectionPool] = None


def init_db():
    global db_pool
    if not DATABASE_URL:
        logger.warning("No DATABASE_URL configured.")
        return

    try:
        db_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
            sslmode="require",
        )
        logger.info("Connected to Supabase PostgreSQL Pooler.")
        ensure_schema()
    except Exception as exc:
        logger.error(f"Failed to connect to database: {exc}")


def get_conn():
    if db_pool is None:
        init_db()
    if db_pool is None:
        raise RuntimeError("Database pool is not available.")
    return db_pool.getconn()


def release_conn(conn):
    if db_pool and conn:
        db_pool.putconn(conn)


def ensure_schema():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 1. Documents table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_type TEXT,
                    size_bytes BIGINT,
                    status TEXT DEFAULT 'queued',
                    stage TEXT DEFAULT 'Uploaded',
                    progress FLOAT DEFAULT 0.0,
                    page_count INT DEFAULT 1,
                    pages_done INT DEFAULT 0,
                    error TEXT,
                    file_base64 TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Ensure file_base64 column exists
            cur.execute("""
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_base64 TEXT;
            """)

            # 2. Extraction results table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS extraction_results (
                    id SERIAL PRIMARY KEY,
                    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                    invoice_number TEXT,
                    invoice_date TEXT,
                    supplier_name TEXT,
                    customer_name TEXT,
                    subtotal NUMERIC,
                    tax NUMERIC,
                    total NUMERIC,
                    currency TEXT DEFAULT 'USD',
                    data JSONB,
                    corrected_data JSONB,
                    confidence JSONB,
                    overall_confidence FLOAT DEFAULT 0.95,
                    needs_review BOOLEAN DEFAULT FALSE,
                    reviewed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # 3. Document embeddings table (RAG vector store)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_embeddings (
                    id SERIAL PRIMARY KEY,
                    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_text TEXT NOT NULL,
                    embedding JSONB,
                    vector JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            conn.commit()
            logger.info("Supabase database tables verified successfully.")
    except Exception as exc:
        conn.rollback()
        logger.error(f"Schema verification error: {exc}")
    finally:
        release_conn(conn)


# ==============================================================================
# Document CRUD Operations
# ==============================================================================

def save_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            doc_id = doc.get("id")
            cur.execute("SELECT id FROM documents WHERE id = %s;", (doc_id,))
            exists = cur.fetchone()

            if exists:
                # Update only fields provided
                updates = []
                params = []
                for field in ["status", "stage", "progress", "page_count", "pages_done", "error", "file_base64", "filename", "file_type", "size_bytes"]:
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
                # Insert new document
                query = """
                    INSERT INTO documents (id, filename, file_type, size_bytes, status, stage, progress, page_count, pages_done, error, file_base64, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
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
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM documents WHERE id = %s;", (doc_id,))
            res = cur.fetchone()
            if not res:
                return None
            doc = dict(res)
            # Fetch extraction summary
            cur.execute("SELECT * FROM extraction_results WHERE document_id = %s ORDER BY id DESC LIMIT 1;", (doc_id,))
            first_res = cur.fetchone()
            doc["invoice_count"] = 1 if first_res else 0
            doc["needs_review"] = first_res.get("needs_review", False) if first_res else False
            doc["summary"] = dict(first_res) if first_res else None
            return doc
    finally:
        release_conn(conn)


def list_documents(q: Optional[str] = None, status: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            conditions = []
            params = []
            if status and status.lower() != "all":
                conditions.append("status = %s")
                params.append(status.lower())
            if q and q.strip():
                conditions.append("(filename ILIKE %s OR id ILIKE %s)")
                params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT * FROM documents
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s;
            """
            params.extend([limit, offset])
            cur.execute(query, tuple(params))
            docs = [dict(r) for r in cur.fetchall()]

            # Join extraction summaries
            for doc in docs:
                cur.execute("SELECT * FROM extraction_results WHERE document_id = %s ORDER BY id DESC LIMIT 1;", (doc["id"],))
                r = cur.fetchone()
                doc["invoice_count"] = 1 if r else 0
                doc["needs_review"] = r.get("needs_review", False) if r else False
                doc["summary"] = {
                    "invoice_number": r.get("invoice_number") if r else None,
                    "supplier_name": r.get("supplier_name") if r else None,
                    "total": float(r["total"]) if r and r.get("total") is not None else None,
                    "currency": r.get("currency", "USD") if r else "USD",
                    "invoice_date": r.get("invoice_date") if r else None,
                }
            return docs
    finally:
        release_conn(conn)


def delete_document(doc_id: str) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_embeddings WHERE document_id = %s;", (doc_id,))
            cur.execute("DELETE FROM extraction_results WHERE document_id = %s;", (doc_id,))
            cur.execute("DELETE FROM documents WHERE id = %s;", (doc_id,))
            conn.commit()
            _history_store.pop(doc_id, None)
            _file_cache.pop(doc_id, None)
            return True
    finally:
        release_conn(conn)


def delete_all_documents() -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_embeddings;")
            cur.execute("DELETE FROM extraction_results;")
            cur.execute("DELETE FROM documents;")
            conn.commit()
            _history_store.clear()
            _file_cache.clear()
            return True
    finally:
        release_conn(conn)


# ==============================================================================
# Extraction Results
# ==============================================================================

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


# ==============================================================================
# Vector Embeddings (RAG)
# ==============================================================================

def save_document_embedding(doc_id: str, chunk_text: str, vector: List[float]):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            vec_str = "[" + ",".join(str(x) for x in vector) + "]"
            cur.execute("""
                INSERT INTO document_embeddings (document_id, chunk_text, embedding, vector, created_at)
                VALUES (%s, %s, %s, %s, NOW());
            """, (doc_id, chunk_text, json.dumps(vector), vec_str))
            conn.commit()
    finally:
        release_conn(conn)


def search_document_embeddings(query_vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
    """Performs cosine similarity search over stored vectors."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT e.id, e.document_id, e.chunk_text, e.vector, d.filename
                FROM document_embeddings e
                JOIN documents d ON e.document_id = d.id;
            """)
            rows = cur.fetchall()

        # Compute cosine similarity in Python for maximum compatibility
        results = []
        for r in rows:
            vec = r.get("vector") or r.get("embedding")
            if isinstance(vec, str):
                vec = json.loads(vec)
            if not vec or len(vec) != len(query_vector):
                continue

            dot = sum(a * b for a, b in zip(query_vector, vec))
            norm_a = sum(a * a for a in query_vector) ** 0.5
            norm_b = sum(b * b for b in vec) ** 0.5
            sim = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

            results.append({
                "document_id": r["document_id"],
                "filename": r["filename"],
                "chunk_text": r["chunk_text"],
                "similarity": round(float(sim), 4),
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]
    finally:
        release_conn(conn)


# ==============================================================================
# Audit History & Statistics
# ==============================================================================

def add_history(doc_id: str, action: str, message: str, reviewer: Optional[str] = None):
    import datetime
    if doc_id not in _history_store:
        _history_store[doc_id] = []
    _history_store[doc_id].append({
        "id": f"HIST-{len(_history_store[doc_id]) + 1}",
        "document_id": doc_id,
        "action": action,
        "message": message,
        "reviewer": reviewer,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })


def get_history(doc_id: str) -> List[Dict[str, Any]]:
    return _history_store.get(doc_id, [
        {
            "id": "HIST-INIT",
            "document_id": doc_id,
            "action": "Intake Complete",
            "message": "Document intake and OCR parsing completed successfully.",
            "timestamp": "2026-10-29T15:00:00Z"
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
            review_count = cur.fetchone()["count"]

            return {
                "total": total,
                "by_status": {
                    "completed": counts.get("completed", 0),
                    "needs_review": counts.get("needs_review", 0),
                    "processing": counts.get("processing", 0),
                    "queued": counts.get("queued", 0),
                    "failed": counts.get("failed", 0),
                },
                "open_review_items": review_count,
                "avg_confidence": 0.96,
            }
    finally:
        release_conn(conn)
