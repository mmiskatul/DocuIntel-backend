"""Documents controller handling upload, intake, file streaming, and approval."""
import base64
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile, status
from app.models.document_repo import (
    save_document,
    get_document,
    list_documents,
    delete_document,
    delete_all_documents,
    cache_file,
    get_cached_file,
    add_history,
    get_history,
)
from app.models.result_repo import get_extraction_results, update_result_correction
from app.schemas.document import BatchApproveRequest, ApproveRequest
from app.schemas.extraction import CorrectionRequest
from app.services.pipeline import run_document_pipeline

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
):
    """Uploads documents, persists raw bytes, and queues background processing."""
    results = []
    for file in files:
        doc_id = str(uuid.uuid4())
        content = await file.read()
        b64_content = base64.b64encode(content).decode("utf-8")
        mime = file.content_type or ("application/pdf" if file.filename.lower().endswith(".pdf") else "application/octet-stream")

        # Save initial document record
        save_document({
            "id": doc_id,
            "filename": file.filename,
            "file_type": mime,
            "size_bytes": len(content),
            "status": "queued",
            "stage": "Uploaded",
            "progress": 0.1,
            "page_count": 1,
            "pages_done": 0,
            "file_base64": b64_content,
        })

        add_history(
            doc_id,
            "Document Uploaded",
            f"File {file.filename} ({len(content) / 1024:.1f} KB) queued for intake.",
        )

        # Cache file in memory for instant delivery
        cache_file(doc_id, content, mime, file.filename)

        # Queue pipeline processing in background
        background_tasks.add_task(run_document_pipeline, doc_id, file.filename, content, mime)

        results.append({
            "id": doc_id,
            "filename": file.filename,
            "status": "queued",
        })

    return results


@router.get("")
def get_documents_list(
    q: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Lists all processed documents with summaries."""
    return list_documents(q=q, status=status, limit=limit, offset=offset)


@router.get("/{doc_id}")
def get_single_document(doc_id: str):
    """Retrieves single document metadata and extraction summary."""
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.get("/{doc_id}/file")
def stream_document_file(doc_id: str):
    """Streams the raw original document file (PDF or raster image)."""
    cached = get_cached_file(doc_id)
    if cached:
        return Response(
            content=cached["bytes"],
            media_type=cached["mimetype"],
            headers={"Content-Disposition": f'inline; filename="{cached["filename"]}"'},
        )

    doc = get_document(doc_id)
    if not doc or not doc.get("file_base64"):
        return Response(
            content="""
                <svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400" style="background:#f8fafc; font-family:sans-serif;">
                  <rect width="100%" height="100%" fill="#ffffff" stroke="#e2e8f0" stroke-width="2"/>
                  <text x="300" y="180" font-size="18" font-weight="600" fill="#475569" text-anchor="middle">Original File Preview Unavailable</text>
                  <text x="300" y="215" font-size="13" fill="#94a3b8" text-anchor="middle">Document not found</text>
                </svg>
            """,
            media_type="image/svg+xml",
        )

    file_bytes = base64.b64decode(doc["file_base64"])
    mime = doc.get("file_type") or ("application/pdf" if doc["filename"].lower().endswith(".pdf") else "application/octet-stream")
    return Response(
        content=file_bytes,
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{doc["filename"]}"'},
    )


@router.get("/{doc_id}/results")
def get_results_for_document(doc_id: str):
    """Retrieves structured extraction results."""
    return get_extraction_results(doc_id)


@router.get("/{doc_id}/history")
@router.get("/{doc_id}/events")
def get_audit_history(doc_id: str):
    """Retrieves human review audit history log events."""
    return get_history(doc_id)


@router.post("/{doc_id}/approve")
def approve_document(doc_id: str, body: ApproveRequest = ApproveRequest()):
    """Marks document as approved by reviewer."""
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    save_document({
        "id": doc_id,
        "status": "completed",
        "stage": "Approved",
        "progress": 1.0,
    })
    add_history(doc_id, "Document Approved", f"Approved by {body.reviewer}.", body.reviewer)
    return {"success": True, "status": "completed", "stage": "Approved"}


@router.post("/batch-approve")
def batch_approve(dto: BatchApproveRequest):
    """Batch approves multiple documents."""
    count = 0
    for doc_id in dto.document_ids:
        save_document({
            "id": doc_id,
            "status": "completed",
            "stage": "Approved",
            "progress": 1.0,
        })
        add_history(doc_id, "Document Approved", f"Batch approved by {dto.reviewer}.", dto.reviewer)
        count += 1
    return {"count": count, "results": []}


@router.delete("/{doc_id}")
def delete_single(doc_id: str):
    """Deletes single document and associated extraction data."""
    delete_document(doc_id)
    return {"success": True}


@router.delete("")
def delete_all():
    """Wipes all documents and associated extraction data."""
    delete_all_documents()
    return {"success": True}
