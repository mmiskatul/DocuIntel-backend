"""Search and Question Answering query controller."""
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Query
from app.services.rag_service import rag_search_and_answer
from app.services.search_service import search as run_search

router = APIRouter(prefix="/search", tags=["Search & Intelligence"])

MODES = ("keyword", "semantic", "hybrid")


@router.get("")
def search(
    q: str = Query("", description="Search query string"),
    mode: str = Query("keyword", description="keyword | semantic | hybrid"),
    limit: int = Query(20, ge=1, le=100),
) -> List[Dict[str, Any]]:
    """Keyword filtering, pgvector semantic search, or both merged (hybrid)."""
    if mode not in MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of {', '.join(MODES)}")
    return run_search(q, mode, limit)


@router.get("/rag")
@router.get("/answer")
def search_answer(q: str = Query(..., min_length=1, description="User question for document answer synthesis")):
    """Answers a question from the retrieved semantic context, with citations."""
    return rag_search_and_answer(q, limit=5)
