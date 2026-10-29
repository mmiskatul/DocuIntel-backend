"""Search and Question Answering Pydantic schemas."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class SearchHit(BaseModel):
    document_id: str
    filename: str
    text: str
    score: float
    page: int = 1


class Citation(BaseModel):
    document_id: str
    filename: str
    snippet: str
    similarity: float


class AnswerResponse(BaseModel):
    answer: str
    citations: List[Citation]
    hits: List[Dict[str, Any]]


# Backwards compatibility alias
RAGResponse = AnswerResponse
