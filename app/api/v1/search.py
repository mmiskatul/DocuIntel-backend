"""Search and RAG query controller."""
from typing import Optional
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
                "filename": m["filename"],
                "text": m["chunk_text"],
                "score": m["similarity"],
                "page": 1,
            }
            for m in matches
        ]

    docs = list_documents(q=q, limit=limit)
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


@router.get("/rag")
def search_rag(
    q: str = Query(..., description="User question for RAG answer synthesis"),
    provider: str = Query("auto", description="LLM provider"),
):
    """Answers user question using retrieved semantic document context with citations."""
    return rag_search_and_answer(q, limit=5)
