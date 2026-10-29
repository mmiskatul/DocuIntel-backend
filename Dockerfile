# ==============================================================================
# DocuIntel Unified FastAPI Backend Dockerfile
# Optimized for Python 3.11 Slim, Render, Docker Compose, and Cloud Run
# ==============================================================================

FROM python:3.11-slim

# Prevent writing .pyc files and enable unbuffered console logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Set container working directory
WORKDIR /app

# Install minimal OS dependencies for PyMuPDF, Pillow, and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for optimal Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose port (default 8000, compatible with dynamic $PORT on Render)
EXPOSE 8000

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start the FastAPI server with uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
