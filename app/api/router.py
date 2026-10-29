"""API Router aggregating all API v1 endpoints with both /api and /api/v1 prefixes."""
from fastapi import APIRouter
from app.api.v1.documents import router as documents_router
from app.api.v1.search import router as search_router
from app.api.v1.stats import router as stats_router
from app.api.v1.health import router as health_router

# Root /api prefix
api_router = APIRouter(prefix="/api")
api_router.include_router(documents_router)
api_router.include_router(search_router)
api_router.include_router(stats_router)

# Versioned /api/v1 prefix
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(documents_router)
api_v1_router.include_router(search_router)
api_v1_router.include_router(stats_router)
