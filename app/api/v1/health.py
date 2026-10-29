"""Health check and service status probe routes."""
from fastapi import APIRouter
from app.core import database

router = APIRouter(tags=["Health & Discovery"])


@router.get("/health")
@router.get("/api/health")
@router.get("/api/wakeup")
def health():
    """Health check endpoint used by uptime probes and frontend status indicators."""
    return {
        "status": "ok",
        "backend": "online",
        "service": "DocuIntel Backend",
        "version": "2.0.0",
        "engine": "Document Intelligence Engine",
        "database": "connected" if database.db_pool else "connecting",
    }
