# DocuIntel Unified FastAPI Backend

Production-ready, modular **FastAPI** backend delivering instant OCR parsing, OpenAI GPT-4o-mini structured extraction, pgvector RAG semantic search, and direct Supabase PostgreSQL persistence.

---

## Clean Architecture Directory Structure

```
backend/
├── app/
│   ├── main.py                     # FastAPI application entrypoint & middleware
│   ├── config.py                   # Environment configuration loader
│   │
│   ├── api/                        # Route controllers
│   │   ├── router.py               # Aggregates v1 sub-routers under /api
│   │   └── v1/
│   │       ├── documents.py        # /api/documents (upload, stream, details, approve)
│   │       ├── search.py           # /api/search & /api/search/rag
│   │       ├── stats.py            # /api/stats & /api/results/{id} corrections
│   │       └── health.py           # /health, /api/health probes
│   │
│   ├── core/                       # Core database & infrastructure
│   │   └── database.py             # Supabase PostgreSQL threaded connection pool
│   │
│   ├── models/                     # Data Access Objects (Repositories)
│   │   ├── document_repo.py        # Documents table CRUD & cache
│   │   ├── result_repo.py          # Extraction results persistence
│   │   └── embedding_repo.py       # pgvector embeddings & similarity search
│   │
│   ├── schemas/                    # Pydantic request & response models
│   │   ├── document.py             # Document schemas
│   │   ├── extraction.py           # Extraction & correction schemas
│   │   └── search.py               # Search & RAG schemas
│   │
│   └── services/                   # Business logic & AI pipelines
│       ├── pdf_parser.py           # PyMuPDF digital text & page parser (<20ms)
│       ├── vision_ocr.py           # OpenAI GPT-4o-mini Vision OCR for images
│       ├── extractor.py            # AI Structured invoice & document field extractor
│       ├── rag_service.py          # OpenAI text-embedding-3-small & RAG synthesis
│       └── pipeline.py             # Asynchronous processing pipeline orchestrator
│
├── tests/                          # Automated test suite
│   └── test_pipeline.py            # End-to-end extraction and persistence tests
│
├── Dockerfile                      # Production container image
├── docker-compose.yml              # Local multi-container setup
├── requirements.txt                # Python package dependencies
├── Procfile                        # Render start command
└── README.md
```

---

## Local Development

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the application
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
API running on: `http://localhost:8000`  
Interactive Swagger Docs: `http://localhost:8000/docs`

---

## Docker Deployment

```bash
# Build and run
docker build -t docuintel-backend .
docker run -p 8000:8000 --env-file .env docuintel-backend

# Or with Docker Compose
docker compose up --build -d
```

---

## REST API Endpoints

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
