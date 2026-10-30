"""Core database connection management for Supabase PostgreSQL."""
import logging
from typing import Optional
import psycopg2
from psycopg2 import pool
from app.config import DATABASE_URL

logger = logging.getLogger("docuintel.core.database")

db_pool: Optional[pool.ThreadedConnectionPool] = None


def init_db():
    """Initializes threaded PostgreSQL connection pool and verifies schema."""
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
    """Retrieves a dedicated connection from the connection pool."""
    if db_pool is None:
        init_db()
    if db_pool is None:
        raise RuntimeError("Database pool is not available.")
    return db_pool.getconn()


def release_conn(conn):
    """Releases connection back to the pool."""
    if db_pool and conn:
        db_pool.putconn(conn)


def ensure_schema():
    """Verifies and creates tables, pgvector extension, and HNSW indexes."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 1. Documents table (metadata & extracted text)
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
                    extracted_text TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_base64 TEXT;
            """)
            cur.execute("""
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_text TEXT;
            """)

            # 2. Extraction results table (structured results & validation errors)
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
                    validation_errors JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                ALTER TABLE extraction_results ADD COLUMN IF NOT EXISTS validation_errors JSONB;
            """)

            # 3. Processing history table (audit trail & status lineage)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processing_history (
                    id SERIAL PRIMARY KEY,
                    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    message TEXT NOT NULL,
                    stage TEXT,
                    reviewer TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_processing_history_doc 
                ON processing_history(document_id, created_at ASC);
            """)

            # 4. Ensure pgvector extension and document embeddings table (semantic search)
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as ext_err:
                logger.warning(f"Vector extension notice: {ext_err}")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_embeddings (
                    id SERIAL PRIMARY KEY,
                    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_text TEXT NOT NULL,
                    embedding vector(384),
                    vector JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # 4. HNSW cosine index for sub-millisecond similarity search
            try:
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS doc_embed_hnsw 
                    ON document_embeddings USING hnsw (embedding vector_cosine_ops);
                """)
            except Exception as index_err:
                logger.warning(f"HNSW index notice: {index_err}")

            conn.commit()
            logger.info("Supabase database tables and pgvector HNSW index verified successfully.")
    except Exception as exc:
        conn.rollback()
        logger.error(f"Schema verification error: {exc}")
    finally:
        release_conn(conn)
