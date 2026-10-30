"""DocuIntel Application Entrypoint."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.core import database
from app.api.router import api_router, api_v1_router
from app.api.v1.health import router as health_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("docuintel.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting DocuIntel Backend...")
    database.init_db()
    yield
    logger.info("Stopping DocuIntel Backend...")


app = FastAPI(
    title="DocuIntel Document Intelligence API",
    description="Clean, modular FastAPI Backend with Intelligent OCR, Semantic Vector Search, and Supabase PostgreSQL.",
    version="2.1.0",
    lifespan=lifespan,
)

# CORS middleware allowing Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Health check at root & /api
app.include_router(health_router)

# Mount both /api and /api/v1 routes
app.include_router(api_router)
app.include_router(api_v1_router)


@app.get("/")
def root():
    return {
        "service": "DocuIntel Document Intelligence API",
        "status": "active",
        "version": "2.1.0",
        "docs": "/docs",
        "health": "/health",
    }
