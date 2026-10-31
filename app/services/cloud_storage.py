"""Temporary storage for the original files in Cloudinary.

A file is uploaded when a document arrives, is read back for previews and re-processing, and is deleted once the
document has been approved (the extracted data stays in the database). Files are stored as *authenticated* raw
assets: they have no public URL and are only fetched through URLs signed by this server.

Nothing here is required: when Cloudinary is not configured (or a call fails) the app keeps the file in the
database instead, so uploads never fail because of this module.
"""
import io
import logging
import re
import urllib.request
from typing import Optional

from app.config import CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, CLOUDINARY_CLOUD_NAME, CLOUDINARY_FOLDER, CLOUDINARY_URL

logger = logging.getLogger("docuintel.services.cloud_storage")

_RESOURCE = {"resource_type": "raw", "type": "authenticated"}
_configured = False


def enabled() -> bool:
    """True when Cloudinary credentials are present."""
    return bool(CLOUDINARY_URL or (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET))


def _sdk():
    """Imports and configures the Cloudinary SDK on first use."""
    global _configured
    import cloudinary
    import cloudinary.api  # noqa: F401
    import cloudinary.uploader  # noqa: F401
    import cloudinary.utils  # noqa: F401

    if not _configured:
        if not CLOUDINARY_URL:  # the SDK reads CLOUDINARY_URL from the environment by itself
            cloudinary.config(cloud_name=CLOUDINARY_CLOUD_NAME, api_key=CLOUDINARY_API_KEY, api_secret=CLOUDINARY_API_SECRET, secure=True)
        _configured = True
    return cloudinary


def _safe_name(filename: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (filename or "document").strip()) or "document"
    return base[-80:]


def upload(doc_id: str, filename: str, data: bytes) -> str:
    """Stores the file and returns its Cloudinary public id (the extension is part of it for raw assets)."""
    sdk = _sdk()
    public_id = f"{CLOUDINARY_FOLDER}/{doc_id}/{_safe_name(filename)}"
    result = sdk.uploader.upload(io.BytesIO(data), public_id=public_id, overwrite=True, use_filename=False, unique_filename=False, **_RESOURCE)
    return result.get("public_id") or public_id


def download_url(public_id: str) -> str:
    """A short-lived download URL signed with the API secret. (Plain "signed delivery" URLs are refused for
    authenticated assets on many accounts, so the signed API download endpoint is used instead.)"""
    return _sdk().utils.private_download_url(public_id, "", resource_type="raw", type="authenticated")


def download(public_id: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(download_url(public_id), timeout=timeout) as resp:  # noqa: S310 - https URL signed by us
        return resp.read()


def delete(public_id: str) -> bool:
    """Deletes the asset. A file that is already gone counts as deleted. Returns False if Cloudinary could not be reached."""
    try:
        result = _sdk().uploader.destroy(public_id, invalidate=True, **_RESOURCE)
        return result.get("result") in ("ok", "not found")
    except Exception as exc:
        logger.warning("Could not delete %s from Cloudinary: %s", public_id, exc)
        return False


def safe_delete(public_id: Optional[str]) -> bool:
    return delete(public_id) if public_id and enabled() else False
