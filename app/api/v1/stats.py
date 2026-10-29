"""Dashboard statistics and manual correction routes."""
from fastapi import APIRouter, HTTPException
from app.models.document_repo import get_stats
from app.models.result_repo import update_result_correction
from app.schemas.extraction import CorrectionRequest

router = APIRouter(tags=["Stats & Corrections"])


@router.get("/stats")
def dashboard_stats():
    """Retrieves document counts by status, review queues, and average confidence."""
    return get_stats()


@router.patch("/results/{res_id}")
def correct_result(res_id: int, dto: CorrectionRequest):
    """Applies human review corrections to extracted document fields."""
    updated = update_result_correction(res_id, dto.corrections, dto.reviewer or "reviewer@company.com")
    if not updated:
        raise HTTPException(status_code=404, detail="Result not found.")
    return updated
