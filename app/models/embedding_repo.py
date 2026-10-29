"""Document vector embeddings repository (RAG storage and retrieval)."""
import json
import logging
from typing import Any, Dict, List
from psycopg2.extras import RealDictCursor
from app.core.database import get_conn, release_conn

logger = logging.getLogger("docuintel.models.embedding")


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
    """Performs cosine similarity search over stored vectors in Supabase with joined metadata."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    e.id, 
                    e.document_id, 
                    e.chunk_text, 
                    e.vector, 
                    e.embedding,
                    d.filename, 
                    d.status,
                    d.created_at,
                    er.invoice_number, 
                    er.supplier_name, 
                    er.total, 
                    er.currency, 
                    er.invoice_date
                FROM document_embeddings e
                JOIN documents d ON e.document_id = d.id
                LEFT JOIN (
                    SELECT DISTINCT ON (document_id) document_id, invoice_number, supplier_name, total, currency, invoice_date 
                    FROM extraction_results 
                    ORDER BY document_id, id DESC
                ) er ON er.document_id = d.id;
            """)
            rows = cur.fetchall()

        results = []
        for r in rows:
            vec = r.get("vector") or r.get("embedding")
            if isinstance(vec, str):
                try:
                    vec = json.loads(vec)
                except Exception:
                    continue
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
                "status": r.get("status") or "completed",
                "invoice_number": r.get("invoice_number"),
                "supplier_name": r.get("supplier_name"),
                "total": float(r["total"]) if r.get("total") is not None else None,
                "currency": r.get("currency") or "USD",
                "invoice_date": r.get("invoice_date"),
                "created_at": str(r.get("created_at") or ""),
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]
    finally:
        release_conn(conn)
