"""Durable background job queue backed by PostgreSQL (no Redis required).

* Uploads are persisted (file + status) before a job is queued, so nothing is lost if the process dies.
* Jobs run on a bounded worker pool (WORKER_CONCURRENCY) inside each API instance.
* Every instance heartbeats the documents it owns (touches updated_at). A document that is 'queued'/'processing'
  but has not been touched for STALE_JOB_SECONDS belongs to a dead instance and is re-claimed.
* Claiming uses UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED), so several API instances can run a
  recovery sweep at the same time without ever taking the same job twice.
"""
import base64
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from psycopg2.extras import RealDictCursor

from app.config import RECOVERY_INTERVAL_SECONDS, STALE_JOB_SECONDS, WORKER_CONCURRENCY
from app.core.database import get_conn, release_conn
from app.models.document_repo import add_history
from app.services.pipeline import run_document_pipeline

logger = logging.getLogger("docuintel.services.job_queue")

HEARTBEAT_SECONDS = 20

_executor: Optional[ThreadPoolExecutor] = None
_inflight: set = set()
_inflight_lock = threading.Lock()
_supervisor_started = False


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=WORKER_CONCURRENCY, thread_name_prefix="docuintel-worker")
    return _executor


def _run(doc_id: str, filename: str, content: bytes, mime: str):
    try:
        run_document_pipeline(doc_id, filename, content, mime)
    except Exception:  # the pipeline handles its own errors; this guards the worker thread
        logger.exception(f"Unhandled worker error for {doc_id}")
    finally:
        with _inflight_lock:
            _inflight.discard(doc_id)


def enqueue(doc_id: str, filename: str, content: bytes, mime: str):
    """Schedules a document for processing on this instance's worker pool."""
    with _inflight_lock:
        _inflight.add(doc_id)
    return _pool().submit(_run, doc_id, filename, content, mime)


def heartbeat() -> int:
    """Marks this instance's queued/running documents as alive."""
    with _inflight_lock:
        ids = list(_inflight)
    if not ids:
        return 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE documents SET updated_at = NOW() WHERE id::text = ANY(%s) AND status IN ('queued', 'processing');", (ids,))
            conn.commit()
    finally:
        release_conn(conn)
    return len(ids)


def recover_stuck_jobs(stale_seconds: Optional[int] = None, limit: int = 50) -> int:
    """Atomically claims abandoned jobs (stale, not owned by this instance) and re-queues them. Returns the count."""
    stale = STALE_JOB_SECONDS if stale_seconds is None else stale_seconds
    with _inflight_lock:
        mine = list(_inflight)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE documents SET status = 'queued', stage = 'Recovered After Restart', progress = 0.1, updated_at = NOW()
                WHERE id IN (
                    SELECT id FROM documents
                    WHERE status IN ('queued', 'processing') AND file_base64 IS NOT NULL
                      AND updated_at < NOW() - make_interval(secs => %s)
                      AND NOT (id::text = ANY(%s))
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, filename, file_type, file_base64;
            """, (stale, mine, limit))
            rows = cur.fetchall()
            conn.commit()
    finally:
        release_conn(conn)

    count = 0
    for row in rows:
        try:
            content = base64.b64decode(row["file_base64"])
        except Exception:
            continue
        add_history(row["id"], "Job Recovered", "Interrupted job re-queued automatically (its previous worker stopped responding).")
        enqueue(row["id"], row["filename"], content, row["file_type"] or "application/pdf")
        count += 1
    if count:
        logger.info(f"Recovered {count} interrupted job(s).")
    return count


def _supervise():
    last_sweep = 0.0
    while True:
        try:
            heartbeat()
            if time.time() - last_sweep >= RECOVERY_INTERVAL_SECONDS:
                recover_stuck_jobs()
                last_sweep = time.time()
        except Exception:
            logger.exception("Job supervisor error")
        time.sleep(HEARTBEAT_SECONDS)


def start_supervisor():
    """Starts the heartbeat + recovery loop once per process."""
    global _supervisor_started
    if _supervisor_started:
        return
    _supervisor_started = True
    threading.Thread(target=_supervise, daemon=True, name="job-supervisor").start()
