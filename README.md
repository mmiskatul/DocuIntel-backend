# DocuIntel Unified FastAPI Backend

Production-ready, lightweight **FastAPI** backend delivering instant OCR parsing, OpenAI GPT-4o-mini structured extraction, pgvector RAG, and direct Supabase PostgreSQL persistence.

## Features
- **Fast Parsing Engine**: Direct PDF parsing with PyMuPDF in <20ms.
- **AI OCR & Extraction**: Powered by OpenAI GPT-4o-mini & Vision with strict schema compliance (zero dummy data).
- **RAG Vector Search**: Dense 384-dimensional vector embeddings with OpenAI `text-embedding-3-small` stored in Supabase PostgreSQL (`document_embeddings`).
- **File Retention**: Stores raw file base64 in PostgreSQL so original documents are always viewable via inline PDF viewer.
- **REST APIs**: Full suite of endpoints for upload, streaming, human review corrections, approval, and semantic search.
- **Interactive API Docs**: Swagger UI available at `http://localhost:8000/docs`.

---

## Local Development

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Docker Deployment

### 1. Build and Run with Docker
```bash
docker build -t docuintel-backend .
docker run -p 8000:8000 --env-file .env docuintel-backend
```

### 2. Run with Docker Compose
```bash
docker compose up --build -d
```

---

## API Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/documents` | Upload documents and queue async background AI extraction |
| `GET` | `/api/documents` | List documents with extraction summary |
| `GET` | `/api/documents/{id}` | Get single document metadata |
| `GET` | `/api/documents/{id}/file` | Stream original file (PDF/Image) |
| `GET` | `/api/documents/{id}/results` | Get extracted structured invoice/document fields |
| `GET` | `/api/documents/{id}/history` | Audit log of human approvals & corrections |
| `POST` | `/api/documents/{id}/approve` | Mark document as approved |
| `POST` | `/api/documents/batch-approve` | Batch approve selected documents |
| `DELETE` | `/api/documents/{id}` | Delete document and associated vector embeddings |
| `DELETE` | `/api/documents` | Wipe all documents |
| `PATCH` | `/api/results/{id}` | Submit manual value corrections |
| `GET` | `/api/stats` | Dashboard KPI stats |
| `GET` | `/api/search` | Search documents by keyword or semantic similarity |
| `GET` | `/api/search/rag` | Ask questions with grounded source citations |
| `GET` | `/health` | Healthcheck probe |
