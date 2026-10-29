"""API Router aggregating all API v1 endpoints."""
from fastapi import APIRouter
from app.api.v1.documents import router as documents_router
from app.api.v1.search import router as search_router
from app.api.v1.stats import router as stats_router
from app.api.v1.health import router as health_router

api_router = APIRouter(prefix="/api")

api_router.include_router(documents_router)
api_router.include_router(search_router)
api_router.include_router(stats_router)
