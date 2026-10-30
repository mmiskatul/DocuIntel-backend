# DocuIntel — Enterprise Document Intelligence & Financial Extraction Platform

DocuIntel is a production-grade, asynchronous AI Document Intelligence system built with **FastAPI**, **LangChain**, **OpenAI GPT-4o-mini**, **PostgreSQL / Supabase pgvector**, and **Next.js 16 (App Router + Redux Toolkit)**.

It processes multi-modal financial documents (**multi-page digital & scanned PDFs, high-resolution images, multi-sheet Excel workbooks, and CSVs**), performs zero-hallucination structured extraction, enforces enterprise-grade validation rules with arithmetic reconciliations, and powers grounded semantic retrieval (RAG) with source citations.

---

## Deliverables Checklist & Compliance

| # | Submission Requirement | Implementation Status & Verification |
| :--- | :--- | :--- |
| **1** | **Git repository containing the source code** | **Backend Repo**: [DocuIntel-backend](https://github.com/mmiskatul/DocuIntel-backend)<br/>**Frontend Repo**: [DocuIntel-frontend](https://github.com/mmiskatul/DocuIntel-frontend) |
| **2** | **README and setup instructions** | Complete setup guide below (Local Python/Next.js, Docker, & Environment configs). |
| **3** | **Architecture diagram & database schema** | Detailed Mermaid diagrams and relational DDL below + in [`ARCHITECTURE_AND_SCHEMA.md`](./ARCHITECTURE_AND_SCHEMA.md). |
| **4** | **Working backend API and frontend** | Modular FastAPI backend with interactive Swagger (`http://localhost:8000/docs`) and responsive Next.js 16 UI (`http://localhost:3000`). |
| **5** | **PDF, image, Excel, and CSV pipelines** | Supported formats: Multi-page digital PDFs, scanned PDFs (Vision OCR), Images (PNG/JPG/TIFF), Multi-sheet Excel (`.xlsx` & `.xls`), and CSVs. |
| **6** | **Automated tests and test results** | Full test suite: 43 automated tests in `tests/` covering validation, APIs, database, pgvector search, and dynamic rejection (100% pass rate). |
| **7** | **Evaluation report with measured metrics** | Exhaustive benchmark report with actual metrics in [`EVALUATION_REPORT.md`](./EVALUATION_REPORT.md). |
| **8** | **Recorded walkthrough of the working application** | Walkthrough demonstration script & step-by-step recording guide included below. |
| **9** | **AI prompts & disclosure of AI development** | Complete disclosure of extraction prompts, anti-hallucination constraints, and AI-assisted tooling below. |

---

## 1. System Architecture

```mermaid
flowchart TD
    subgraph Client["Next.js 16 + Redux Toolkit Client"]
        Upload["Batch Multi-File Upload Zone"]
        Dashboard["Live Operations Dashboard (1.5s Polling)"]
        Viewer["Document & OCR Text Split-Viewer"]
        Review["Reconciliation & Edit Mode"]
        Analyst["Smart Search & AI Document Analyst (RAG)"]
    end

    subgraph Backend_Gateway["FastAPI Application (Port 8000)"]
        Endpoints["REST API Controllers (/api/v1)"]
        WorkerPool["Async Non-Blocking Processing Pipeline"]
    end

    subgraph Parsing_Engines["Multi-Modal Parsing Pipeline"]
        PDF_Engine["PyMuPDF Digital Text Engine"]
        Vision_Engine["GPT-4o-mini Vision OCR (Images & Scans)"]
        Excel_Engine["Openpyxl & Xlrd Multi-Sheet Extractors"]
        CSV_Engine["Auto-Charset CSV Parser"]
    end

    subgraph Intelligence["Extraction & Validation Engine"]
        LangChain_LLM["LangChain LCEL Structured Output (Pydantic)"]
        Doc_Classifier["Dynamic Content Classifier (Invoices vs Others)"]
        Validator["Validation Engine (Missing Fields, Duplicates, Math Variances)"]
    end

    subgraph Storage["PostgreSQL + pgvector (Supabase)"]
        T_Docs[("documents (Metadata, Stages, OCR Text)")]
        T_Results[("extraction_results (Headers, Line Items, Errors)")]
        T_History[("processing_history (Audit Trail)")]
        T_Embeddings[("document_embeddings (384-dim HNSW Cosine Index)")]
    end

    Upload -->|Multipart Upload| Endpoints
    Endpoints -->|Background Task| WorkerPool
    WorkerPool --> PDF_Engine & Vision_Engine & Excel_Engine & CSV_Engine
    PDF_Engine & Vision_Engine & Excel_Engine & CSV_Engine --> LangChain_LLM
    LangChain_LLM --> Doc_Classifier --> Validator
    Validator --> T_Docs & T_Results & T_History
    Validator -->|Only Validated & Approved| T_Embeddings
    Dashboard & Viewer & Review <--> Endpoints
    Analyst <-->|RAG Query| Endpoints
    Endpoints <--> T_Embeddings
```

---

## 2. Database Schema

The database utilizes PostgreSQL with the `pgvector` extension. Full schema details are documented in [`ARCHITECTURE_AND_SCHEMA.md`](./ARCHITECTURE_AND_SCHEMA.md).

```mermaid
erDiagram
    DOCUMENTS ||--o{ EXTRACTION_RESULTS : "stores results"
    DOCUMENTS ||--o{ PROCESSING_HISTORY : "tracks audit"
    DOCUMENTS ||--o{ DOCUMENT_EMBEDDINGS : "stores vector chunks"

    DOCUMENTS {
        varchar id PK
        varchar filename
        varchar file_type
        bigint size_bytes
        varchar status "queued | processing | needs_review | completed | rejected | failed"
        varchar stage
        float progress
        int page_count
        text error
        text extracted_text
        timestamp created_at
    }

    EXTRACTION_RESULTS {
        serial id PK
        varchar document_id FK
        varchar invoice_number
        varchar invoice_date
        varchar supplier_name
        varchar customer_name
        numeric subtotal
        numeric tax
        numeric total
        varchar currency
        jsonb data "Line items, summary, metadata"
        jsonb validation_errors
        boolean needs_review
        boolean reviewed
    }

    PROCESSING_HISTORY {
        serial id PK
        varchar document_id FK
        varchar action
        text message
        varchar reviewer
        timestamp timestamp
    }

    DOCUMENT_EMBEDDINGS {
        serial id PK
        varchar document_id FK
        text chunk_text
        vector embedding "384-dim vector (HNSW index)"
    }
```

---

## 3. Supported File Formats & Processing Pipelines

1. **Digital Multi-Page PDFs**: PyMuPDF extracts full text layout, page markers, and geometry in `< 20ms`.
2. **Scanned PDFs & Poor Quality Images**: PyMuPDF rasterizes pages into pixmaps; OpenAI `gpt-4o-mini` Vision OCR extracts textual and tabular data with high resilience to noise, skew, and low contrast.
3. **High-Resolution Images**: PNG, JPG, JPEG, TIFF, and WEBP processed directly through Vision OCR.
4. **Excel Spreadsheets (`.xlsx` & legacy `.xls`)**: Iterates through all workbook sheets via `openpyxl` and `xlrd`, capturing grid coordinates, multi-sheet headers, and itemized rows.
5. **CSV Files**: Decoded with automatic charset detection (`utf-8`, `utf-16`, `latin-1`, `cp1252`).

---

## 4. Enterprise Validation Engine

The validator ([`app/services/validator.py`](./app/services/validator.py)) applies strict validation before allowing any document into the system:

1. **Missing Fields**: Identifies missing mandatory fields (`invoice_number`, `supplier_name`, `total`) and flags warnings for secondary fields (`invoice_date`, `subtotal`, `customer_name`).
2. **Incorrect Numeric Values**: Catches `NaN`, infinite values, negative amounts (`total < 0`, `tax < 0`), and zero totals when billable line items exist.
3. **Duplicate Invoices**: Detects duplicate invoice numbers from the same vendor in the database.
4. **Arithmetic Inconsistencies**: Cross-checks:
   $$\sum \text{Line Items} = \text{Subtotal}$$
   $$\text{Subtotal} + \text{Tax} = \text{Total}$$
   $$\text{Quantity} \times \text{Unit Price} = \text{Line Amount}$$
5. **Dynamic Document Rejection**: Dynamically classifies non-billing documents (Resumes, NDAs, Academic Papers, Specs) and explains why they fail intake without static or hardcoded error strings.
6. **Manual Review Flagging**: Inconsistencies or errors set `needs_review = True`, update the stage tag (e.g., `Needs Review (Arithmetic Inconsistency)`), and prevent automatic vector indexing until approved or reconciled.

---

## 5. Local Setup & Installation

### Prerequisites
* Python 3.11+
* Node.js 18+ & npm
* PostgreSQL with `pgvector` (or free Supabase project)
* OpenAI API Key

### Backend Setup

```bash
# 1. Clone repository
git clone https://github.com/mmiskatul/DocuIntel-backend.git
cd DocuIntel-backend

# 2. Create virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment (.env)
cat << 'EOF' > .env
OPENAI_API_KEY=your_openai_api_key_here
DATABASE_URL=postgresql://postgres.your-project:your-password@aws-0-region.pooler.supabase.com:6543/postgres
PORT=8000
HOST=0.0.0.0
EOF

# 5. Run Backend Server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive API documentation: `http://localhost:8000/docs`

### Frontend Setup

```bash
# 1. Clone repository
git clone https://github.com/mmiskatul/DocuIntel-frontend.git
cd DocuIntel-frontend

# 2. Install dependencies
npm install

# 3. Configure environment (.env.local)
echo "NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1" > .env.local

# 4. Start Next.js Development Server
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

### Docker Setup

```bash
# Build and run backend container
docker build -t docuintel-backend .
docker run -p 8000:8000 --env-file .env docuintel-backend

# Or run with Docker Compose
docker compose up --build -d
```

---

## 6. REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/documents` | Multi-document multipart upload (queues async pipeline). |
| `GET` | `/api/v1/documents` | List documents with summaries, stages, and status filter (`status=completed`). |
| `GET` | `/api/v1/documents/{id}` | Get document metadata and extraction overview. |
| `GET` | `/api/v1/documents/{id}/file` | Stream original file bytes for preview. |
| `GET` | `/api/v1/documents/{id}/results` | Get extracted structured invoice fields and line items. |
| `GET` | `/api/v1/documents/{id}/history` | Audit trail of processing steps and human interventions. |
| `POST` | `/api/v1/documents/{id}/approve` | Mark document approved and trigger vector indexing. |
| `POST` | `/api/v1/documents/{id}/reconcile` | Resolve arithmetic discrepancy with calculated sum or override. |
| `PATCH`| `/api/v1/documents/{id}/corrections`| Update corrected field values and synchronize with vector store. |
| `POST` | `/api/v1/documents/{id}/rerun` | Reprocess a failed or flagged document. |
| `DELETE`| `/api/v1/documents/{id}` | Permanently delete document and its vector embeddings. |
| `GET` | `/api/v1/search` | Search documents (`mode=keyword`, `mode=semantic`, or `mode=hybrid`). |
| `GET` | `/api/v1/search/rag` | AI Document Analyst grounded question answering with citations. |
| `GET` | `/api/v1/stats` | Dashboard metrics (total, processing, needs_review, completed). |
| `GET` | `/health` | Liveness and readiness probe. |

---

## 7. Automated Testing & Benchmark Evaluation

DocuIntel includes a comprehensive automated test suite in `tests/`:

```bash
# Run all automated tests
pytest tests/test_validation_rules.py tests/test_dynamic_reasons.py tests/test_rest_apis.py tests/test_database_and_search.py -v

# Run Task 7 Benchmark Evaluation Suite
python tests/evaluate_benchmark.py
```

### Measured Benchmark Summary (Task 7)
* **Document Success Rate**: **100.0%** (SLA $\ge 90\%$)
* **Failure & Rejection Rate**: **100.0%** (SLA $\ge 90\%$)
* **Overall Field Accuracy**: **100%** on a 7-document synthetic benchmark (see EVALUATION_REPORT.md for limits)
* **Average Processing Latency**: **~10-12 s** per document (measured, includes remote DB + LLM round-trips)
* See [`EVALUATION_REPORT.md`](./EVALUATION_REPORT.md) for full metrics and field breakdown.

---

## 8. AI Prompts & Disclosure of AI-Assisted Development

### Core Extraction Prompt (`app/services/extractor.py`)

```python
EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are an enterprise AI Document Intelligence and Financial Extraction Engine.\n\n"
            "MANDATORY REQUIREMENT:\n"
            "Extract invoice number, invoice date, supplier name, customer name, subtotal, tax, total, currency, and line items. "
            "Return structured JSON and handle missing or uncertain information without inventing values.\n\n"
            "CRITICAL EXTRACTION & ANTI-HALLUCINATION RULES:\n"
            "1. STRICT FACTUALITY (ZERO HALLUCINATION):\n"
            "   - Extract ONLY values explicitly present in the source text.\n"
            "   - NEVER invent, fabricate, guess, or extrapolate values.\n"
            "   - If any field is missing, ambiguous, or uncertain, you MUST set that field to null.\n"
            "   - If line items are not present, return an empty array []. DO NOT create dummy items.\n\n"
            "2. DYNAMIC DOCUMENT CLASSIFICATION & FORMAT VALIDATION:\n"
            "   - Analyze document content dynamically. If valid commercial billing: is_supported_format=True.\n"
            "   - If NOT billing (Resume, Legal Contract, Academic Paper, Spec, Medical Record): is_supported_format=False, "
            "     and provide a dynamic, factual rejection_reason explaining what the document actually is and why it fails intake.\n\n"
            "3. NUMERIC FIELDS:\n"
            "   - subtotal, tax, and total must be raw float numbers (e.g., 2890.00).\n"
        ),
    ),
    (
        "human",
        "Filename: {filename}\n\nDocument Text:\n{text}",
    ),
])
```

### Disclosure of AI Tools Used
In compliance with Task 9:
* **AI Coding Assistance**: Google Antigravity & LLM pair programming assistants were used during development for accelerated scaffolding, test dataset synthesis, and refactoring.
* **Core Application AI Models**:
  * `gpt-4o-mini`: Utilized for vision OCR, structured Pydantic JSON extraction, and dynamic document classification.
  * `text-embedding-3-small`: Utilized for 384-dimensional vector embeddings stored in PostgreSQL `pgvector`.
* **Zero-Hallucination Guardrails**: All extraction schemas use strict Pydantic models with explicit `null` defaults; downstream deterministic validation code cross-checks all math against raw line item numbers.

---

## 9. Application Video Walkthrough Guide

When recording the walkthrough video, follow this recommended sequence to demonstrate all capabilities:

1. **Batch Upload**:
   - Navigate to the Dashboard.
   - Drag and drop multiple files at once: a clean PDF invoice, a scanned receipt image, an Excel workbook (`.xlsx`), and an adversarial non-invoice file (e.g., a Resume or Contract).
2. **Processing Progress**:
   - Show the real-time polling updates on the Dashboard: documents move from `Queued` $\to$ `OCR & Parsing` $\to$ `Information Extraction` $\to$ `Validation`.
3. **Inspect Extracted Information**:
   - Click into a completed invoice.
   - Show the split-screen view: original document preview alongside raw OCR text.
   - Point out the extracted Header Cards (Invoice #, Date, Vendor, Customer, Amounts, Currency) and the structured Line Items Table.
4. **Review Validation & Reconciliation**:
   - Open a document with an arithmetic discrepancy or click into a document flagged as `Needs Review`.
   - Show the validation banner identifying the discrepancy (e.g. Subtotal + Tax $\neq$ Total).
   - Demonstrate 1-click reconciliation: click **"Apply Sum"** or **"Override Total"** to resolve the variance and approve the document into the vector database.
   - Demonstrate **"Edit Fields"**: edit an extracted field and save corrections.
5. **Dynamic Document Rejection**:
   - Click into the non-invoice document (e.g. Resume or Contract).
   - Show the dynamic rejection banner stating the exact detected document type and explaining why it was excluded from financial intake and vector indexing.
6. **Smart Search & AI Document Analyst (RAG)**:
   - Navigate to `/search`.
   - Perform a **Keyword Search** (e.g. searching for a vendor name or invoice number).
   - Switch to **Smart Search** (Semantic Vector Search) to find documents based on natural language meaning.
   - Switch to **⚡ AI Document Analyst**: ask a natural language question (e.g., *"What is our total spend with Acme Cloud?"* or *"What are the payment terms?"*) and show the synthesized answer with grounded document citations.

---

## Operations notes

* **Authentication**: set `API_KEY` in the backend `.env`; every `/api` request must then send `X-API-Key` (or `Authorization: Bearer`). Set the same value as `BACKEND_API_KEY` in the frontend (server-side only; the Next.js proxy adds it, so the browser never sees it). `/health` stays public. If `API_KEY` is empty the API is open (development only; a warning is logged).
* **Background jobs**: uploads are persisted to PostgreSQL first, then processed by a bounded worker pool (`WORKER_CONCURRENCY`, default 3). On startup, documents left in `queued`/`processing` (crash/redeploy) are re-queued automatically. This is a PostgreSQL-backed design instead of Redis/BullMQ to avoid an extra service; multi-instance deployments need row locking (`SKIP LOCKED`) before recovery.
* **Limits** (env): `MAX_UPLOAD_MB`, `MAX_FILES_PER_REQUEST`, `MAX_OCR_PAGES`, `MAX_INDEX_CHUNKS`, `EXTRACT_HEAD_CHARS`, `EXTRACT_TAIL_CHARS`, `CORS_ORIGINS`.
* **Data handling**: document text and images are sent to OpenAI for OCR, extraction and embeddings.
* **Low-quality scans**: OCR emits `[illegible]` for unreadable text; such documents are routed to manual review (`LOW_OCR_QUALITY`).
* **Multi-invoice PDFs**: pages are grouped by the invoice number printed on them (`app/services/segmenter.py`); each invoice gets its own extraction result, validation and review state. The detail page shows a tab per invoice, and manual corrections target one invoice via `result_id`. `MAX_INVOICES_PER_DOCUMENT` and `EXTRACTION_PARALLELISM` tune this.
