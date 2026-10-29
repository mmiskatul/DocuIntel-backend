"""Unified DocuIntel FastAPI Backend with AI-Powered OCR, RAG & Supabase PostgreSQL."""
import base64
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
import database
from services import extractor, rag

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("docuintel.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing DocuIntel FastAPI Backend...")
    database.init_db()
    yield
    logger.info("Shutting down DocuIntel FastAPI Backend...")


app = FastAPI(
    title="DocuIntel Document Intelligence API",
    description="FastAPI Backend for AI-Powered OCR, Information Extraction, and RAG Vector Search.",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# Background Processing Worker
# ==============================================================================

def process_document_pipeline(doc_id: str, filename: str, file_bytes: bytes, mime_type: str):
    """Asynchronous background pipeline for text extraction, AI parsing, and RAG indexing."""
    try:
        # Step 1: Text & Page Extraction
        database.save_document({
            "id": doc_id,
            "status": "processing",
            "stage": "OCR & Parsing",
            "progress": 0.3,
        })
        text, page_count = extractor.extract_text_and_pages(file_bytes, filename)
        database.add_history(
            doc_id,
            "OCR Parsing Completed",
            f"Extracted {len(text)} characters across {page_count} page(s).",
        )

        # Step 2: AI Structured Extraction
        database.save_document({
            "id": doc_id,
            "status": "processing",
            "stage": "AI Information Extraction",
            "progress": 0.6,
            "page_count": page_count,
            "pages_done": page_count,
        })
        extracted = extractor.extract_structured_fields(text, filename)
        issues = extracted.get("issues", [])
        has_errors = any(i.get("severity") == "error" for i in issues)
        needs_review = has_errors

        database.add_history(
            doc_id,
            "AI Extraction Completed",
            f"Extracted fields via GPT-4o-mini. Title/Vendor: {extracted.get('supplier_name') or extracted.get('document_title') or 'Document'}.",
        )

        # Step 3: Save Extraction Results
        database.save_extraction_result({
            "document_id": doc_id,
            "invoice_number": extracted.get("invoice_number"),
            "invoice_date": extracted.get("invoice_date"),
            "supplier_name": extracted.get("supplier_name") or extracted.get("document_title"),
            "customer_name": extracted.get("customer_name"),
            "subtotal": extracted.get("subtotal"),
            "tax": extracted.get("tax"),
            "total": extracted.get("total"),
            "currency": extracted.get("currency", "USD"),
            "data": extracted,
            "confidence": {"overall": 0.96},
            "overall_confidence": 0.85 if needs_review else 0.96,
            "needs_review": needs_review,
            "reviewed": False,
        })

        # Step 4: RAG Vector Embeddings Indexing
        database.save_document({
            "id": doc_id,
            "status": "processing",
            "stage": "Vector Indexing",
            "progress": 0.85,
        })
        rag.chunk_and_index_document(doc_id, text)

        # Step 5: Mark Completed
        final_status = "needs_review" if needs_review else "completed"
        database.save_document({
            "id": doc_id,
            "status": final_status,
            "stage": "Completed",
            "progress": 1.0,
            "page_count": page_count,
            "pages_done": page_count,
        })
        database.add_history(
            doc_id,
            "Flagged for Review" if needs_review else "Processing Completed",
            f"Pipeline finished. Status: {final_status}.",
        )
        logger.info(f"Document {doc_id} processed successfully ({final_status}).")

    except Exception as exc:
        logger.error(f"Error processing document {doc_id}: {exc}")
        database.save_document({
            "id": doc_id,
            "status": "failed",
            "stage": "Failed",
            "error": str(exc),
            "progress": 1.0,
        })
        database.add_history(doc_id, "Processing Failed", str(exc))


# ==============================================================================
# Health & Discovery Routes
# ==============================================================================

@app.get("/")
@app.get("/health")
@app.get("/api/health")
@app.get("/api/wakeup")
def health_check():
    return {
        "status": "ok",
        "backend": "online",
        "service": "DocuIntel FastAPI Backend",
        "version": "2.0.0",
        "ai_engine": "OpenAI GPT-4o-mini + PyMuPDF",
        "database": "connected" if database.db_pool else "connecting",
    }


# ==============================================================================
# Documents Routes
# ==============================================================================

@app.post("/api/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
):
    """Uploads documents, persists raw bytes, and queues background processing."""
    results = []
    for file in files:
        doc_id = str(uuid.uuid4())
        content = await file.read()
        b64_content = base64.b64encode(content).decode("utf-8")
        mime = file.content_type or ("application/pdf" if file.filename.lower().endswith(".pdf") else "application/octet-stream")

        # Save initial document record with base64 for preview
        database.save_document({
            "id": doc_id,
            "filename": file.filename,
            "file_type": mime,
            "size_bytes": len(content),
            "status": "queued",
            "stage": "Uploaded",
            "progress": 0.1,
            "page_count": 1,
            "pages_done": 0,
            "file_base64": b64_content,
        })

        database.add_history(
            doc_id,
            "Document Uploaded",
            f"File {file.filename} ({len(content) / 1024:.1f} KB) queued for intake.",
        )

        # Cache file in memory for instant delivery
        database._file_cache[doc_id] = {
            "bytes": content,
            "mimetype": mime,
            "filename": file.filename,
        }

        # Queue pipeline processing in background
        background_tasks.add_task(process_document_pipeline, doc_id, file.filename, content, mime)

        results.append({
            "id": doc_id,
            "filename": file.filename,
            "status": "queued",
        })

    return results


@app.get("/api/documents")
def list_documents(
    q: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    return database.list_documents(q=q, status=status, limit=limit, offset=offset)


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    doc = database.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@app.get("/api/documents/{doc_id}/file")
def get_document_file(doc_id: str):
    """Streams the original raw file (PDF, image, spreadsheet)."""
    # Check memory cache first
    cached = database._file_cache.get(doc_id)
    if cached:
        return Response(
            content=cached["bytes"],
            media_type=cached["mimetype"],
            headers={"Content-Disposition": f'inline; filename="{cached["filename"]}"'},
        )

    # Fallback to database file_base64
    doc = database.get_document(doc_id)
    if not doc or not doc.get("file_base64"):
        return Response(
            content="""
                <svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400" style="background:#f8fafc; font-family:sans-serif;">
                  <rect width="100%" height="100%" fill="#ffffff" stroke="#e2e8f0" stroke-width="2"/>
                  <text x="300" y="180" font-size="18" font-weight="600" fill="#475569" text-anchor="middle">Original File Preview Unavailable</text>
                  <text x="300" y="215" font-size="13" fill="#94a3b8" text-anchor="middle">Document not found</text>
                </svg>
            """,
            media_type="image/svg+xml",
        )

    file_bytes = base64.b64decode(doc["file_base64"])
    mime = doc.get("file_type") or ("application/pdf" if doc["filename"].lower().endswith(".pdf") else "application/octet-stream")
    return Response(
        content=file_bytes,
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{doc["filename"]}"'},
    )


@app.get("/api/documents/{doc_id}/results")
def get_document_results(doc_id: str):
    return database.get_extraction_results(doc_id)


@app.get("/api/documents/{doc_id}/history")
@app.get("/api/documents/{doc_id}/events")
def get_document_history(doc_id: str):
    return database.get_history(doc_id)


@app.post("/api/documents/{doc_id}/approve")
def approve_document(doc_id: str, body: Dict[str, Any] = {}):
    reviewer = body.get("reviewer", "reviewer@company.com")
    doc = database.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    database.save_document({
        "id": doc_id,
        "status": "completed",
        "stage": "Approved",
        "progress": 1.0,
    })
    database.add_history(doc_id, "Document Approved", f"Approved by {reviewer}.", reviewer)
    return {"success": True, "status": "completed", "stage": "Approved"}


class BatchApproveDto(BaseModel):
    document_ids: List[str]
    reviewer: Optional[str] = "reviewer@company.com"


@app.post("/api/documents/batch-approve")
def batch_approve_documents(dto: BatchApproveDto):
    count = 0
    for doc_id in dto.document_ids:
        database.save_document({
            "id": doc_id,
            "status": "completed",
            "stage": "Approved",
            "progress": 1.0,
        })
        database.add_history(doc_id, "Document Approved", f"Batch approved by {dto.reviewer}.", dto.reviewer)
        count += 1
    return {"count": count, "results": []}


@app.delete("/api/documents/{doc_id}")
def delete_single_document(doc_id: str):
    database.delete_document(doc_id)
    return {"success": True}


@app.delete("/api/documents")
def delete_all_documents():
    database.delete_all_documents()
    return {"success": True}


class CorrectionDto(BaseModel):
    corrections: Dict[str, Any]
    reviewer: Optional[str] = "reviewer@company.com"


@app.patch("/api/results/{res_id}")
def patch_result(res_id: int, dto: CorrectionDto):
    updated = database.update_result_correction(res_id, dto.corrections, dto.reviewer or "reviewer@company.com")
    if not updated:
        raise HTTPException(status_code=404, detail="Result not found.")
    return updated


# ==============================================================================
# Stats & Search Routes
# ==============================================================================

@app.get("/api/stats")
def get_stats():
    return database.get_stats()


@app.get("/api/search")
def search(
    q: str = Query("", description="Search query string"),
    mode: str = Query("keyword", description="keyword | semantic | hybrid"),
    limit: int = 20,
):
    if not q.strip():
        return []

    # Semantic vector search via embeddings
    if mode in ("semantic", "hybrid"):
        query_vec = rag.generate_embedding(q)
        matches = database.search_document_embeddings(query_vec, limit=limit)
        return [
            {
                "document_id": m["document_id"],
                "filename": m["filename"],
                "text": m["chunk_text"],
                "score": m["similarity"],
                "page": 1,
            }
            for m in matches
        ]

    # Keyword search across documents
    docs = database.list_documents(q=q, limit=limit)
    return [
        {
            "document_id": d["id"],
            "filename": d["filename"],
            "text": d.get("summary", {}).get("supplier_name") or d["filename"],
            "score": 1.0,
            "page": 1,
        }
        for d in docs
    ]


@app.get("/api/search/rag")
def search_rag(
    q: str = Query(..., description="User question for RAG answering"),
    provider: str = Query("auto", description="LLM provider"),
):
    return rag.rag_query(q, limit=5)

