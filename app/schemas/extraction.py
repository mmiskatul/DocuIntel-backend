"""Extraction result and correction Pydantic schemas."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = 1.0
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class ExtractionResultResponse(BaseModel):
    id: Optional[int] = None
    document_id: str
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    supplier_name: Optional[str] = None
    customer_name: Optional[str] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    currency: Optional[str] = "USD"
    data: Optional[Dict[str, Any]] = None
    corrected_data: Optional[Dict[str, Any]] = None
    confidence: Optional[Dict[str, Any]] = None
    overall_confidence: float = 0.95
    needs_review: bool = False
    reviewed: bool = False
    created_at: Optional[Any] = None


class CorrectionRequest(BaseModel):
    corrections: Dict[str, Any]
    reviewer: Optional[str] = "reviewer@company.com"
