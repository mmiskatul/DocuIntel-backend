"""Documents controller handling upload, processing status, extracted results, manual corrections, and failed-job reprocessing."""
import asyncio
import base64
import uuid
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from datetime import date
from app.core.errors import handle_errors
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, Response, UploadFile, status

from app.models.document_repo import (
    save_document,
    get_document,
    list_documents,
    delete_document,
    delete_all_documents,
    get_document_file,
    release_original,
    add_history,
    get_history,
)
from app.models.result_repo import get_extraction_results, save_extraction_result
from app.schemas.document import BatchApproveRequest, ApproveRequest
from app.config import ALLOWED_EXTENSIONS, MAX_FILES_PER_REQUEST, MAX_UPLOAD_MB
from app.models.result_repo import append_result_issue, mark_results_reviewed
from app.services.cross_verify import cross_verify
from app.services import cloud_storage
from app.services.job_queue import enqueue

logger = logging.getLogger("docuintel.api.documents")
router = APIRouter(prefix="/documents", tags=["Documents"])


def _original(doc_id: str) -> Optional[Dict[str, Any]]:
    """The stored original ({bytes, mimetype, filename}) from the database or Cloudinary; None when gone."""
    return get_document_file(doc_id)


def _original_or_error(doc_id: str) -> Dict[str, Any]:
    """Like _original, but raises the right HTTP error: 410 when the file was deleted after approval, otherwise 404."""
    stored = _original(doc_id)
    if stored:
        return stored
    doc = get_document(doc_id)
    if doc and doc.get("original_removed"):
        raise HTTPException(status_code=410, detail="The original file was deleted after this document was approved. The extracted data and text are kept.")
    raise HTTPException(status_code=404, detail="Original file not found")


def _index_text(doc_id: str, raw_text: str = "") -> bool:
    """Indexes the document's text for semantic search. Never raises; returns True only when chunks were really stored."""
    try:
        if not raw_text:
            cached = _original(doc_id)
            if cached:
                from app.services.extractor import extract_raw_text
                raw_text, _ = extract_raw_text(cached["bytes"], cached["filename"])
        if not raw_text:
            return False
        from app.services.rag_service import index_document_chunks
        index_document_chunks(doc_id, raw_text)
        return True
    except Exception as exc:
        logger.warning("Vector indexing failed for %s: %s", doc_id, exc)
        return False


class RejectRequest(BaseModel):
    reviewer: str = "reviewer@docuintel.com"
    reason: str = "Format Mismatch: Structure does not match supported business invoice/waybill format"


class ReconcileRequest(BaseModel):
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    action: str = "apply_sum"  # "apply_sum" | "override" | "manual"
    reviewer: str = "reviewer@docuintel.com"


class CrossVerifyRequest(BaseModel):
    left_document_id: str
    right_document_id: str
    flag_mismatches: bool = True  # mark mismatching invoices (in both documents) for manual review
    reviewer: str = "reviewer@docuintel.com"


class ManualCorrectionRequest(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    supplier_name: Optional[str] = None
    customer_name: Optional[str] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    currency: Optional[str] = None
    line_items: Optional[List[Dict[str, Any]]] = None
    reviewer: str = "reviewer@docuintel.com"
    notes: Optional[str] = None
    result_id: Optional[int] = None  # which invoice of a multi-invoice document; defaults to the first
    corrections: Optional[Dict[str, Any]] = None  # nested form used by the web UI; merged over the flat fields


# ==============================================================================
# 1. DOCUMENT UPLOADS (REST API with Background Processing for OCR & AI)
# ==============================================================================

@router.post("", status_code=status.HTTP_202_ACCEPTED)
@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
@handle_errors
async def upload_documents(
    files: List[UploadFile] = File(...),
):
    """
    REST API endpoint for document uploads.
    Persists file bytes, returns HTTP 202 Accepted instantly, and executes time-consuming
    OCR, multimodal AI extraction, and vector indexing asynchronously in the background.
    """
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail=f"Too many files; maximum is {MAX_FILES_PER_REQUEST} per request.")

    # 1. Validate and read everything first, so a bad file rejects the request before anything is stored
    prepared = []
    for file in files:
        name = (file.filename or "").replace("\\", "/").split("/")[-1]
        if not name.lower().endswith(ALLOWED_EXTENSIONS):
            raise HTTPException(status_code=415, detail=f"Unsupported file type: '{name}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
        try:
            content = await file.read()
        except Exception as exc:
            logger.warning("Could not read upload '%s': %s", name, exc)
            raise HTTPException(status_code=400, detail=f"'{name}' could not be read from the request.")
        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"'{name}' exceeds the {MAX_UPLOAD_MB} MB limit.")
        mime = file.content_type or ("application/pdf" if name.lower().endswith(".pdf") else "application/octet-stream")
        prepared.append((str(uuid.uuid4()), name, content, mime))

    # 2. Keep each original in Cloudinary while it awaits review (deleted on approval). The uploads run side by side.
    #    If Cloudinary is not set up, or a call fails, that file falls back to the database so an upload never fails on storage.
    gate = asyncio.Semaphore(6)

    async def _to_cloud(doc_id: str, name: str, content: bytes) -> Optional[str]:
        if not cloud_storage.enabled():
            return None
        async with gate:
            try:
                return await asyncio.to_thread(cloud_storage.upload, doc_id, name, content)
            except Exception as exc:
                logger.warning("Cloudinary upload failed for %s (%s); keeping the file in the database instead.", name, exc)
                return None

    cloud_ids = await asyncio.gather(*[_to_cloud(d, n, c) for d, n, c, _ in prepared])

    # 3. Record each document and queue it for background processing
    results = []
    for (doc_id, name, content, mime), cloud_id in zip(prepared, cloud_ids):
        try:
            save_document({
                "id": doc_id,
                "filename": name,
                "file_type": mime,
                "size_bytes": len(content),
                "status": "queued",
                "stage": "Uploaded",
                "progress": 0.1,
                "page_count": 1,
                "pages_done": 0,
                "file_base64": None if cloud_id else base64.b64encode(content).decode("utf-8"),
                "cloud_public_id": cloud_id,
            })
            add_history(doc_id, "Document Uploaded", f"File {name} ({len(content) / 1024:.1f} KB) queued for intake.")
            enqueue(doc_id, name, content, mime)
            results.append({"id": doc_id, "filename": name, "status": "queued", "stage": "Uploaded"})
        except Exception as exc:  # one bad file must not abort the rest of the batch
            logger.exception("Could not queue %s", name)
            results.append({"id": doc_id, "filename": name, "status": "failed", "stage": "Upload Error",
                            "error": "The file could not be queued. Please try uploading it again."})

    return results


# ==============================================================================
# 1b. CROSS-VERIFICATION (PDF vs PDF, PDF vs Excel/CSV ledger)
# ==============================================================================

@router.post("/cross-verify")
@handle_errors
def cross_verify_documents(body: CrossVerifyRequest):
    """
    Matches the invoices of two processed documents by invoice number and compares supplier, customer, date,
    currency and amounts. Reports matches, mismatches and invoices present in only one document.
    """
    if body.left_document_id == body.right_document_id:
        raise HTTPException(status_code=400, detail="Choose two different documents to cross-verify.")
    docs = {}
    rows = {}
    for side, doc_id in (("left", body.left_document_id), ("right", body.right_document_id)):
        d = get_document(doc_id)
        if not d:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
        if d.get("status") in ("queued", "processing", "failed", "rejected"):
            raise HTTPException(status_code=409, detail=f"'{d['filename']}' is {d['status']}; only processed documents can be cross-verified.")
        docs[side], rows[side] = d, get_extraction_results(doc_id)
        if not rows[side]:
            raise HTTPException(status_code=409, detail=f"'{d['filename']}' has no extracted invoices.")

    report = cross_verify(rows["left"], rows["right"])
    report["left_document"] = {"id": docs["left"]["id"], "filename": docs["left"]["filename"]}
    report["right_document"] = {"id": docs["right"]["id"], "filename": docs["right"]["filename"]}

    flagged = 0
    if body.flag_mismatches:
        for m in report["mismatched"]:
            blocking = any(x["severity"] == "error" for x in m["differences"])
            fields = ", ".join(x["field"] for x in m["differences"])
            for side, rid, other in (("left", m["left_result_id"], docs["right"]), ("right", m["right_result_id"], docs["left"])):
                issue = {
                    "code": "CROSS_VERIFY_MISMATCH", "severity": "error" if blocking else "warning", "field": fields,
                    "message": f"Invoice {m['invoice_number']} differs from '{other['filename']}' in: {fields}.",
                    "details": {"other_document_id": other["id"], "differences": m["differences"]},
                }
                if append_result_issue(rid, issue, needs_review=blocking):
                    flagged += 1
                    if blocking and docs[side].get("status") == "completed":
                        save_document({"id": docs[side]["id"], "status": "needs_review", "stage": "Needs Review (Cross-Verification Mismatch)"})
                        docs[side]["status"] = "needs_review"
    report["flagged_results"] = flagged
    summary = report["summary"]
    for side, other in (("left", "right"), ("right", "left")):
        add_history(
            docs[side]["id"], "Cross-Verified",
            f"Compared with '{docs[other]['filename']}': {summary['matched']} matched, {summary['mismatched']} mismatched, "
            f"{summary['only_in_left'] if side == 'left' else summary['only_in_right']} only in this document.",
            body.reviewer,
        )
    return report


# ==============================================================================
# 2. PROCESSING STATUS & DOCUMENT LISTING
# ==============================================================================

@router.get("")
@handle_errors
def get_documents_list(
    q: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    supplier: Optional[str] = Query(None, description="Part of the supplier name"),
    invoice_number: Optional[str] = Query(None, description="Part of the invoice number"),
    date_from: Optional[date] = Query(None, description="Invoice date on or after (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Invoice date on or before (YYYY-MM-DD)"),
    min_total: Optional[float] = Query(None, ge=0, description="Invoice total at least this much"),
    max_total: Optional[float] = Query(None, ge=0, description="Invoice total at most this much"),
    needs_review: Optional[bool] = Query(None, description="Only documents with an invoice still waiting for review"),
):
    """Lists processed documents with summaries and status. All filters are optional and combined with AND."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to.")
    if min_total is not None and max_total is not None and min_total > max_total:
        raise HTTPException(status_code=422, detail="min_total must not be greater than max_total.")
    return list_documents(
        q=q, status=status, limit=limit, offset=offset, supplier=supplier, invoice_number=invoice_number,
        date_from=date_from.isoformat() if date_from else None, date_to=date_to.isoformat() if date_to else None,
        min_total=min_total, max_total=max_total, needs_review=needs_review,
    )


@router.get("/{doc_id}")
@handle_errors
def get_single_document(doc_id: str):
    """Retrieves single document metadata, status, and extraction summary."""
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.get("/{doc_id}/status")
@handle_errors
def get_document_processing_status(doc_id: str):
    """
    REST API endpoint for polling document processing status and pipeline progress.
    Returns status ('queued' | 'processing' | 'completed' | 'needs_review' | 'rejected' | 'failed'),
    active pipeline stage, progress percentage (0.0 to 1.0), and error/flag messages.
    """
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    return {
        "id": doc["id"],
        "filename": doc["filename"],
        "status": doc["status"],
        "stage": doc.get("stage", "Uploaded"),
        "progress": doc.get("progress", 0.0),
        "page_count": doc.get("page_count", 1),
        "pages_done": doc.get("pages_done", 0),
        "error": doc.get("error"),
        "needs_review": doc.get("needs_review", False),
        "updated_at": doc.get("updated_at"),
    }


@router.get("/{doc_id}/file")
@handle_errors
def stream_document_file(doc_id: str):
    """Streams the raw original document file (PDF or raster image)."""
    stored = _original_or_error(doc_id)
    return Response(
        content=stored["bytes"],
        media_type=stored["mimetype"],
        headers={"Content-Disposition": f'inline; filename="{stored["filename"]}"'},
    )


@router.get("/{doc_id}/page/{page}/image")
@handle_errors
def document_page_image(doc_id: str, page: int, scale: float = 1.6):
    """One page of the original as a PNG (PDFs and images), for the in-app preview."""
    from app.services.page_render import PageOutOfRange, PreviewUnavailable, render_page_png

    stored = _original_or_error(doc_id)
    try:
        png = render_page_png(stored["bytes"], stored["mimetype"], stored["filename"], page, scale)
    except PageOutOfRange as exc:
        raise HTTPException(status_code=404, detail=f"Page not found ({exc})")
    except PreviewUnavailable:
        raise HTTPException(status_code=415, detail="This file type cannot be previewed as an image")
    except Exception as exc:
        logger.warning("Preview render failed for %s: %s", doc_id, exc)
        raise HTTPException(status_code=422, detail="The file could not be rendered (it may be corrupt)")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


# ==============================================================================
# 3. EXTRACTED RESULTS
# ==============================================================================

@router.get("/{doc_id}/results")
@handle_errors
def get_results_for_document(doc_id: str):
    """
    REST API endpoint for retrieving structured extraction results:
    Returns invoice_number, invoice_date, supplier_name, customer_name,
    subtotal, tax, total, currency, line_items, confidence, and validation issues.
    """
    return get_extraction_results(doc_id)


@router.get("/{doc_id}/history")
@router.get("/{doc_id}/events")
@handle_errors
def get_audit_history(doc_id: str):
    """Retrieves human review audit history log events."""
    return get_history(doc_id)


# ==============================================================================
# 4. MANUAL CORRECTIONS & RECONCILIATION
# ==============================================================================

@router.post("/{doc_id}/correct")
@router.put("/{doc_id}/correct")
@router.put("/{doc_id}/results")
@handle_errors
def manual_correct_document(doc_id: str, body: ManualCorrectionRequest):
    """
    REST API endpoint for manual corrections.
    Allows human reviewers to update extracted key-value fields, override totals,
    clear validation flags, set status to 'completed', and store verified chunks in vector database.
    """
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    results = get_extraction_results(doc_id)
    res = next((r for r in results if body.result_id is not None and r.get("id") == body.result_id), None)
    if res is None:
        if body.result_id is not None:
            raise HTTPException(status_code=404, detail="Extraction result not found for this document.")
        res = results[0] if results else {}
    data = res.get("data") or {}

    # Merge the nested UI form into the flat fields
    if body.corrections:
        allowed = {"invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency", "line_items"}
        for k, v in body.corrections.items():
            if k in allowed and v is not None and getattr(body, k, None) is None:
                setattr(body, k, v)

    # Apply manual corrections
    if body.invoice_number is not None:
        data["invoice_number"] = body.invoice_number
    if body.invoice_date is not None:
        data["invoice_date"] = body.invoice_date
    if body.supplier_name is not None:
        data["supplier_name"] = body.supplier_name
    if body.customer_name is not None:
        data["customer_name"] = body.customer_name
    if body.subtotal is not None:
        data["subtotal"] = body.subtotal
    if body.tax is not None:
        data["tax"] = body.tax
    if body.total is not None:
        data["total"] = body.total
    if body.currency is not None:
        data["currency"] = body.currency
    if body.line_items is not None:
        data["line_items"] = body.line_items

    # Clear issues upon human review
    data["issues"] = []
    data["reviewed"] = True

    save_extraction_result({
        "id": res.get("id"),
        "document_id": doc_id,
        "invoice_number": data.get("invoice_number"),
        "invoice_date": data.get("invoice_date"),
        "supplier_name": data.get("supplier_name"),
        "customer_name": data.get("customer_name"),
        "subtotal": data.get("subtotal"),
        "tax": data.get("tax"),
        "total": data.get("total"),
        "currency": data.get("currency", "USD"),
        "data": data,
        "overall_confidence": 1.0,
        "needs_review": False,
        "reviewed": True,
    })

    # In a multi-invoice document the document stays in review until every invoice has been reviewed
    pending = [r for r in results if r.get("id") != res.get("id") and r.get("needs_review") and not r.get("reviewed")]
    doc_status = "needs_review" if pending else "completed"
    save_document({
        "id": doc_id,
        "status": doc_status,
        "stage": "Manually Corrected" if pending else "Manually Corrected & Vectorized",
        "progress": 1.0,
        "needs_review": bool(pending),
    })

    add_history(
        doc_id,
        "Manual Correction Applied",
        f"Corrected by {body.reviewer}. {body.notes or 'Fields updated and verified.'}",
        body.reviewer,
    )

    # Store verified chunks in the vector database once nothing is left to review
    vector_stored = False if pending else _index_text(doc_id, res.get("raw_text") or data.get("raw_text") or "")

    original_removed = False if pending else release_original(doc_id)

    return {
        "success": True,
        "status": doc_status,
        "stage": "Manually Corrected" if pending else "Manually Corrected & Vectorized",
        "vector_stored": vector_stored,
        "original_removed": original_removed,
        "data": data,
    }


@router.post("/{doc_id}/reconcile")
@handle_errors
def reconcile_document(doc_id: str, body: ReconcileRequest):
    """Reconciles arithmetic variance, marks document completed, and stores in vector database."""
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    results = get_extraction_results(doc_id)
    raw_text = ""
    if results and len(results) > 0:
        res = results[0]
        data = res.get("data") or {}
        if body.subtotal is not None:
            data["subtotal"] = body.subtotal
        if body.tax is not None:
            data["tax"] = body.tax
        if body.total is not None:
            data["total"] = body.total
        data["issues"] = []  # Clear arithmetic error!
        save_extraction_result({
            "id": res["id"],
            "document_id": doc_id,
            "invoice_number": res.get("invoice_number"),
            "invoice_date": res.get("invoice_date"),
            "supplier_name": res.get("supplier_name"),
            "customer_name": res.get("customer_name"),
            "subtotal": body.subtotal if body.subtotal is not None else res.get("subtotal"),
            "tax": body.tax if body.tax is not None else res.get("tax"),
            "total": body.total if body.total is not None else res.get("total"),
            "currency": res.get("currency", "USD"),
            "data": data,
            "overall_confidence": 0.99,
            "needs_review": False,
            "reviewed": True,
        })
        raw_text = res.get("raw_text") or data.get("raw_text") or ""

    save_document({
        "id": doc_id,
        "status": "completed",
        "stage": "Reconciled & Vectorized",
        "progress": 1.0,
    })
    mark_results_reviewed(doc_id)
    add_history(
        doc_id,
        "Reconciled & Approved",
        f"Variance resolved via {body.action}. Total confirmed as {body.total if body.total is not None else 'the stated total'}. Stored in vector database.",
        body.reviewer,
    )

    vector_stored = _index_text(doc_id, raw_text)
    return {"success": True, "status": "completed", "stage": "Reconciled & Vectorized", "vector_stored": vector_stored, "original_removed": release_original(doc_id)}


@router.post("/{doc_id}/approve")
@handle_errors
def approve_document(doc_id: str, body: ApproveRequest = ApproveRequest()):
    """Marks document as approved and automatically syncs all chunks into vector database."""
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    save_document({
        "id": doc_id,
        "status": "completed",
        "stage": "Approved & Vectorized",
        "progress": 1.0,
    })
    mark_results_reviewed(doc_id)
    add_history(doc_id, "Document Approved & Vectorized", f"Approved by {body.reviewer}. Chunks stored in vector database.", body.reviewer)

    results = get_extraction_results(doc_id)
    raw_text = ""
    if results:
        raw_text = results[0].get("raw_text") or (results[0].get("data") or {}).get("raw_text") or ""
    vector_stored = _index_text(doc_id, raw_text)

    # The document is accepted: its original is no longer needed (the extracted data and text stay)
    return {"success": True, "status": "completed", "stage": "Approved & Vectorized", "vector_stored": vector_stored, "original_removed": release_original(doc_id)}


@router.post("/{doc_id}/reject")
@handle_errors
def reject_document(doc_id: str, body: RejectRequest = RejectRequest()):
    """Marks document as rejected due to format mismatch or unverified data."""
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    save_document({
        "id": doc_id,
        "status": "rejected",
        "stage": "Format Rejected",
        "error": body.reason,
        "progress": 1.0,
    })
    add_history(doc_id, "Document Rejected", f"Rejected by {body.reviewer}: {body.reason}", body.reviewer)
    return {"success": True, "status": "rejected", "stage": "Format Rejected", "reason": body.reason}


# ==============================================================================
# 5. FAILED-JOB REPROCESSING & RETRY
# ==============================================================================

@router.post("/{doc_id}/reprocess")
@router.post("/{doc_id}/retry")
@router.post("/{doc_id}/re-ocr")
@handle_errors
def reprocess_document(doc_id: str):
    """
    REST API endpoint for failed-job reprocessing.
    Queues OCR, multimodal AI extraction, and vector pipeline in the background.
    """
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    content = (get_document_file(doc_id) or {}).get("bytes") or b""
    if not content:
        if doc.get("original_removed"):
            raise HTTPException(status_code=409, detail="The original file was deleted after approval, so this document cannot be read again.")
        raise HTTPException(status_code=400, detail="Document binary not available for re-processing.")

    mime = doc.get("file_type") or "application/pdf"

    # Reset status to queued
    save_document({
        "id": doc_id,
        "status": "queued",
        "stage": "Reprocessing Queued",
        "progress": 0.1,
        "error": None,
    })

    enqueue(doc_id, doc["filename"], content, mime)
    add_history(doc_id, "Reprocessing Queued", "Background worker queued to re-run OCR and extraction pipeline.")
    return {
        "success": True,
        "id": doc_id,
        "status": "queued",
        "stage": "Reprocessing Queued",
        "message": "Background reprocessing job initiated.",
    }


@router.post("/reprocess-failed")
@handle_errors
def reprocess_all_failed_documents():
    """
    REST API endpoint to batch reprocess all failed jobs.
    Scans the database for failed documents and re-queues them in the background.
    """
    failed_docs = list_documents(status="failed", limit=50)
    requeued = []
    for doc in failed_docs:
        doc_id = doc["id"]
        content = (get_document_file(doc_id) or {}).get("bytes") or b""
        if content:
            mime = doc.get("file_type") or "application/pdf"
            save_document({
                "id": doc_id,
                "status": "queued",
                "stage": "Reprocessing Queued",
                "progress": 0.1,
                "error": None,
            })
            enqueue(doc_id, doc["filename"], content, mime)
            requeued.append(doc_id)

    return {
        "success": True,
        "requeued_count": len(requeued),
        "requeued_document_ids": requeued,
        "message": f"Queued {len(requeued)} failed document(s) for background reprocessing.",
    }


# ==============================================================================
# BATCH & DELETE
# ==============================================================================

@router.post("/batch-approve")
@handle_errors
def batch_approve(dto: BatchApproveRequest):
    """Batch approves multiple documents."""
    count = 0
    for doc_id in dto.document_ids:
        if not get_document(doc_id):
            continue  # unknown or already deleted ids are skipped, not counted as approved
        save_document({
            "id": doc_id,
            "status": "completed",
            "stage": "Approved",
            "progress": 1.0,
        })
        mark_results_reviewed(doc_id)
        add_history(doc_id, "Document Approved", f"Batch approved by {dto.reviewer}.", dto.reviewer)
        release_original(doc_id)
        count += 1
    return {"count": count, "results": []}


@router.delete("/{doc_id}")
@handle_errors
def delete_single(doc_id: str):
    """Deletes single document and associated extraction data."""
    if not delete_document(doc_id):
        raise HTTPException(status_code=500, detail="Could not delete the document.")
    return {"success": True}


@router.delete("")
@handle_errors
def delete_all():
    """Wipes all documents and associated extraction data."""
    if not delete_all_documents():
        raise HTTPException(status_code=500, detail="Could not delete the documents.")
    return {"success": True}
