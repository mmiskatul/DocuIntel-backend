-- ==============================================================================
-- DocuIntel Supabase Database Schema
-- Run this in your Supabase SQL Editor (https://app.supabase.com/project/_/sql)
-- ==============================================================================

-- 1. Enable pgvector extension (optional for vector search)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Documents Table
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued', -- queued, processing, completed, needs_review, failed
    stage TEXT DEFAULT 'Uploaded',
    progress FLOAT DEFAULT 0.0,
    page_count INT DEFAULT 1,
    pages_done INT DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Extraction Results Table
CREATE TABLE IF NOT EXISTS extraction_results (
    id BIGSERIAL PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    invoice_number TEXT,
    invoice_date TEXT,
    supplier_name TEXT,
    customer_name TEXT,
    subtotal NUMERIC,
    tax NUMERIC,
    total NUMERIC,
    currency TEXT,
    data JSONB DEFAULT '{}'::jsonb,
    corrected_data JSONB,
    confidence JSONB DEFAULT '{}'::jsonb,
    overall_confidence FLOAT DEFAULT 0.0,
    needs_review BOOLEAN DEFAULT FALSE,
    reviewed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Validation Issues Table
CREATE TABLE IF NOT EXISTS validation_issues (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT REFERENCES extraction_results(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning', -- error, warning
    field TEXT,
    message TEXT NOT NULL,
    details JSONB DEFAULT '{}'::jsonb,
    resolved BOOLEAN DEFAULT FALSE
);

-- 5. Document Embeddings (Vector Database for Semantic Search via pgvector)
CREATE TABLE IF NOT EXISTS document_embeddings (
    id BIGSERIAL PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    chunk_text TEXT NOT NULL,
    embedding vector(384), -- BAAI/bge-small-en-v1.5 384-dimensional dense vectors
    vector JSONB,          -- JSON fallback
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Vector Cosine Index for ultra-fast ANN similarity search
CREATE INDEX IF NOT EXISTS idx_document_embeddings_cosine
ON document_embeddings
USING hnsw (embedding vector_cosine_ops);

-- Semantic Vector Match RPC Function for Supabase
CREATE OR REPLACE FUNCTION match_documents (
  query_embedding vector(384),
  match_threshold float DEFAULT 0.3,
  match_count int DEFAULT 10
)
RETURNS TABLE (
  id bigint,
  document_id uuid,
  chunk_text text,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    document_embeddings.id,
    document_embeddings.document_id,
    document_embeddings.chunk_text,
    1 - (document_embeddings.embedding <=> query_embedding) AS similarity
  FROM document_embeddings
  WHERE document_embeddings.embedding IS NOT NULL
    AND 1 - (document_embeddings.embedding <=> query_embedding) > match_threshold
  ORDER BY document_embeddings.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- 6. Users Table (for JWT Authentication)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT,
    role TEXT DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Indexes for High Performance Search
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_doc_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_res_inv_num ON extraction_results(invoice_number);
CREATE INDEX IF NOT EXISTS idx_res_supplier ON extraction_results(supplier_name);
CREATE INDEX IF NOT EXISTS idx_res_needs_review ON extraction_results(needs_review);

