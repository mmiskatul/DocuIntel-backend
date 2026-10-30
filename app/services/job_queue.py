"""Durable background job queue.

Jobs run on a bounded worker pool (concurrency cap, no unbounded threads). Durability comes from
PostgreSQL: the uploaded file and its status are persisted before a job is queued, and on startup any
document left in 'queued'/'processing' (server restart, crash, deploy) is re-queued by recover_stuck_jobs().
"""
import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from psycopg2.extras import RealDictCursor

from app.config import WORKER_CONCURRENCY
from app.core.database import get_conn, release_conn
from app.models.document_repo import add_history, save_document
from app.services.pipeline import run_document_pipeline

logger = logging.getLogger("docuintel.services.job_queue")

_executor: Optional[ThreadPoolExecutor] = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=WORKER_CONCURRENCY, thread_name_prefix="docuintel-worker")
    return _executor


def _run(doc_id: str, filename: str, content: bytes, mime: str):
    try:
        run_document_pipeline(doc_id, filename, content, mime)
    except Exception:  # pipeline handles its own errors; this guards the worker thread
        logger.exception(f"Unhandled worker error for {doc_id}")


def enqueue(doc_id: str, filename: str, content: bytes, mime: str):
    """Schedules a document for processing on the worker pool."""
    return _pool().submit(_run, doc_id, filename, content, mime)


def recover_stuck_jobs() -> int:
    """Re-queues documents interrupted by a restart. Returns the number re-queued."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, filename, file_type, file_base64 FROM documents
                WHERE status IN ('queued', 'processing') AND file_base64 IS NOT NULL
                ORDER BY created_at ASC;
            """)
            rows = cur.fetchall()
    finally:
        release_conn(conn)

    count = 0
    for row in rows:
        try:
            content = base64.b64decode(row["file_base64"])
        except Exception:
            continue
        save_document({"id": row["id"], "status": "queued", "stage": "Recovered After Restart", "progress": 0.1})
        add_history(row["id"], "Job Recovered", "Interrupted job re-queued automatically after a server restart.")
        enqueue(row["id"], row["filename"], content, row["file_type"] or "application/pdf")
        count += 1
    if count:
        logger.info(f"Recovered {count} interrupted job(s).")
    return count
