"""Document Pydantic schemas for request & response validation."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class DocumentSummary(BaseModel):
    invoice_number: Optional[str] = None
    supplier_name: Optional[str] = None
    total: Optional[float] = None
    currency: Optional[str] = "USD"
    invoice_date: Optional[str] = None


class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: Optional[str] = None
    size_bytes: Optional[int] = None
    status: str
    stage: str
    progress: float
    page_count: int = 1
    pages_done: int = 0
    error: Optional[str] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None
    invoice_count: int = 0
    needs_review: bool = False
    summary: Optional[DocumentSummary] = None


class UploadResponse(BaseModel):
    id: str
    filename: str
    status: str


class BatchApproveRequest(BaseModel):
    document_ids: List[str]
    reviewer: Optional[str] = "reviewer@company.com"


class ApproveRequest(BaseModel):
    reviewer: Optional[str] = "reviewer@company.com"
