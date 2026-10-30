"""API-key authentication for the REST API."""
import hmac
import logging
from typing import Optional
from fastapi import Header, HTTPException, status
from app.config import API_KEY

logger = logging.getLogger("docuintel.core.security")


def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    """Rejects requests without the configured API key. No-op (dev mode) when API_KEY is unset."""
    if not API_KEY:
        return
    supplied = x_api_key
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), API_KEY.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key.")
