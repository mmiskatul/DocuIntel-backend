"""Application configuration settings loaded from environment."""
import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", "8000"))

# No default: credentials must come from the environment (.env locally, secrets manager in prod).
DATABASE_URL = os.getenv("DATABASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

# Upload limits
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_FILES_PER_REQUEST = int(os.getenv("MAX_FILES_PER_REQUEST", "50"))
ALLOWED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".xlsx", ".xls", ".csv")

# Large-document handling
MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "100"))
EXTRACT_HEAD_CHARS = int(os.getenv("EXTRACT_HEAD_CHARS", "6000"))
EXTRACT_TAIL_CHARS = int(os.getenv("EXTRACT_TAIL_CHARS", "4000"))
MAX_INDEX_CHUNKS = int(os.getenv("MAX_INDEX_CHUNKS", "200"))

# Security & workers
API_KEY = os.getenv("API_KEY", "")  # when set, every /api request must send it (X-API-Key or Bearer)
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "3"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "12"))
DB_POOL_WAIT_SECONDS = float(os.getenv("DB_POOL_WAIT_SECONDS", "30"))
MAX_INVOICES_PER_DOCUMENT = int(os.getenv("MAX_INVOICES_PER_DOCUMENT", "500"))
EXTRACTION_PARALLELISM = int(os.getenv("EXTRACTION_PARALLELISM", "4"))

# OCR: "auto" = local RapidOCR first, vision-LLM fallback when unsure; "local" = never call the LLM; "vision" = LLM only
OCR_ENGINE = os.getenv("OCR_ENGINE", "auto").lower()
OCR_MIN_CONFIDENCE = float(os.getenv("OCR_MIN_CONFIDENCE", "0.80"))
STALE_JOB_SECONDS = int(os.getenv("STALE_JOB_SECONDS", "120"))
RECOVERY_INTERVAL_SECONDS = int(os.getenv("RECOVERY_INTERVAL_SECONDS", "60"))
