"""Search and RAG query controller."""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query
from app.models.document_repo import list_documents
from app.models.embedding_repo import search_document_embeddings
from app.services.rag_service import generate_embedding, rag_search_and_answer

router = APIRouter(prefix="/search", tags=["Search & RAG"])


@router.get("")
def search(
    q: str = Query("", description="Search query string"),
    mode: str = Query("keyword", description="keyword | semantic | hybrid"),
    limit: int = 20,
):
    """Searches documents using keyword or dense semantic vector embeddings."""
    if not q.strip():
        return []

    if mode in ("semantic", "hybrid"):
        query_vec = generate_embedding(q)
        matches = search_document_embeddings(query_vec, limit=limit)
        return [
            {
                "document_id": m["document_id"],
                "page": 1,
                "score": m["similarity"],
                "snippet": m["chunk_text"][:250],
                "match": mode,
                "document": {
                    "id": m["document_id"],
                    "filename": m["filename"],
                    "file_type": "application/pdf",
                    "size_bytes": 0,
                    "status": m.get("status") or "completed",
                    "stage": "Completed",
                    "progress": 1.0,
                    "page_count": 1,
                    "pages_done": 1,
                    "error": None,
                    "attempts": 1,
                    "processing_ms": 100,
                    "created_at": m.get("created_at") or "",
                    "updated_at": m.get("created_at") or "",
                    "invoice_count": 1,
                    "needs_review": False,
                    "summary": {
                        "invoice_number": m.get("invoice_number"),
                        "supplier_name": m.get("supplier_name"),
                        "total": m.get("total"),
                        "currency": m.get("currency") or "USD",
                        "invoice_date": m.get("invoice_date"),
                    },
                },
            }
            for m in matches
        ]

    docs = list_documents(q=q, limit=limit)
    return [
        {
            "document_id": d["id"],
            "page": 1,
            "score": 1.0,
            "snippet": f"Filename: {d['filename']} | Vendor: {d.get('summary', {}).get('supplier_name') or 'Document'}",
            "match": "keyword",
            "document": d,
        }
        for d in docs
    ]


@router.get("/rag")
def search_rag(
    q: str = Query(..., description="User question for RAG answer synthesis"),
    provider: str = Query("auto", description="LLM provider"),
):
    """Answers user question using retrieved semantic document context with citations."""
    return rag_search_and_answer(q, limit=5)
