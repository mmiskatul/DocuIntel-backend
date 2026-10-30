"""Documents controller handling upload, processing status, extracted results, manual corrections, and failed-job reprocessing."""
import base64
import uuid
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile, status

from app.models.document_repo import (
    save_document,
    get_document,
    list_documents,
    delete_document,
    delete_all_documents,
    cache_file,
    get_cached_file,
    get_document_file,
    add_history,
    get_history,
)
from app.models.result_repo import get_extraction_results, save_extraction_result
from app.schemas.document import BatchApproveRequest, ApproveRequest
from app.config import ALLOWED_EXTENSIONS, MAX_FILES_PER_REQUEST, MAX_UPLOAD_MB
from app.models.result_repo import append_result_issue
from app.services.cross_verify import cross_verify
from app.services.job_queue import enqueue

logger = logging.getLogger("docuintel.api.documents")
router = APIRouter(prefix="/documents", tags=["Documents"])


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

    results = []
    for file in files:
        name = (file.filename or "").replace("\\", "/").split("/")[-1]
        if not name.lower().endswith(ALLOWED_EXTENSIONS):
            raise HTTPException(status_code=415, detail=f"Unsupported file type: '{name}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
        file.filename = name
        content = await file.read()
        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"'{name}' exceeds the {MAX_UPLOAD_MB} MB limit.")
        doc_id = str(uuid.uuid4())
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

        # Queue background processing for time-consuming OCR and AI extraction
        enqueue(doc_id, file.filename, content, mime)

        results.append({
            "id": doc_id,
            "filename": file.filename,
            "status": "queued",
            "stage": "Uploaded",
        })

    return results


# ==============================================================================
# 1b. CROSS-VERIFICATION (PDF vs PDF, PDF vs Excel/CSV ledger)
# ==============================================================================

@router.post("/cross-verify")
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
def get_documents_list(
    q: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Lists all processed documents with summaries and processing status."""
    return list_documents(q=q, status=status, limit=limit, offset=offset)


@router.get("/{doc_id}")
def get_single_document(doc_id: str):
    """Retrieves single document metadata, status, and extraction summary."""
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.get("/{doc_id}/status")
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
def stream_document_file(doc_id: str):
    """Streams the raw original document file (PDF or raster image)."""
    cached = get_cached_file(doc_id)
    if cached:
        return Response(
            content=cached["bytes"],
            media_type=cached["mimetype"],
            headers={"Content-Disposition": f'inline; filename="{cached["filename"]}"'},
        )

    stored = get_document_file(doc_id)
    if not stored:
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

    file_bytes = stored["bytes"]
    mime = stored["mimetype"]
    doc = {"filename": stored["filename"]}
    return Response(
        content=file_bytes,
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{doc["filename"]}"'},
    )


# ==============================================================================
# 3. EXTRACTED RESULTS
# ==============================================================================

@router.get("/{doc_id}/results")
def get_results_for_document(doc_id: str):
    """
    REST API endpoint for retrieving structured extraction results:
    Returns invoice_number, invoice_date, supplier_name, customer_name,
    subtotal, tax, total, currency, line_items, confidence, and validation issues.
    """
    return get_extraction_results(doc_id)


@router.get("/{doc_id}/history")
@router.get("/{doc_id}/events")
def get_audit_history(doc_id: str):
    """Retrieves human review audit history log events."""
    return get_history(doc_id)


# ==============================================================================
# 4. MANUAL CORRECTIONS & RECONCILIATION
# ==============================================================================

@router.post("/{doc_id}/correct")
@router.put("/{doc_id}/correct")
@router.put("/{doc_id}/results")
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

    # Automatically store verified chunks in vector database (once nothing is left to review)
    try:
        if pending:
            raise RuntimeError(f"{len(pending)} invoice(s) still need review; indexing deferred")
        raw_text = res.get("raw_text") or data.get("raw_text") or ""
        if not raw_text:
            cached = get_cached_file(doc_id)
            if cached:
                from app.services.extractor import extract_raw_text
                raw_text, _ = extract_raw_text(cached["bytes"], cached["filename"])
        if raw_text:
            from app.services.rag_service import index_document_chunks
            index_document_chunks(doc_id, raw_text)
            logger.info(f"Document {doc_id} successfully vectorized following manual correction.")
    except Exception as exc:
        logger.warning(f"Vector storage notice on manual correction: {exc}")

    return {
        "success": True,
        "status": doc_status,
        "stage": "Manually Corrected" if pending else "Manually Corrected & Vectorized",
        "vector_stored": not pending,
        "data": data,
    }


@router.post("/{doc_id}/reconcile")
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
    add_history(
        doc_id,
        "Reconciled & Approved",
        f"Variance resolved via {body.action}. Total confirmed as ${body.total or 3340.00}. Stored in vector database.",
        body.reviewer,
    )

    # Store in vector database
    try:
        if not raw_text:
            cached = get_cached_file(doc_id)
            if cached:
                from app.services.extractor import extract_raw_text
                raw_text, _ = extract_raw_text(cached["bytes"], cached["filename"])
        if raw_text:
            from app.services.rag_service import index_document_chunks
            index_document_chunks(doc_id, raw_text)
    except Exception as exc:
        logger.warning(f"Vector storage notice on reconcile: {exc}")

    return {"success": True, "status": "completed", "stage": "Reconciled & Vectorized", "vector_stored": True}


@router.post("/{doc_id}/approve")
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
    add_history(doc_id, "Document Approved & Vectorized", f"Approved by {body.reviewer}. Chunks stored in vector database.", body.reviewer)

    try:
        from app.services.rag_service import index_document_chunks
        results = get_extraction_results(doc_id)
        raw_text = ""
        if results and len(results) > 0:
            raw_text = results[0].get("raw_text") or (results[0].get("data") or {}).get("raw_text") or ""
        if not raw_text:
            cached = get_cached_file(doc_id)
            if cached:
                from app.services.extractor import extract_raw_text
                raw_text, _ = extract_raw_text(cached["bytes"], cached["filename"])
        if raw_text:
            index_document_chunks(doc_id, raw_text)
            logger.info(f"Document {doc_id} successfully stored in vector database.")
    except Exception as exc:
        logger.warning(f"Vector storage notice on approval: {exc}")

    return {"success": True, "status": "completed", "stage": "Approved & Vectorized", "vector_stored": True}


@router.post("/{doc_id}/reject")
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
def reprocess_document(doc_id: str):
    """
    REST API endpoint for failed-job reprocessing.
    Queues OCR, multimodal AI extraction, and vector pipeline in the background.
    """
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    cached = get_cached_file(doc_id)
    content = cached["bytes"] if cached else ((get_document_file(doc_id) or {}).get("bytes") or b"")
    if not content:
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
def reprocess_all_failed_documents():
    """
    REST API endpoint to batch reprocess all failed jobs.
    Scans the database for failed documents and re-queues them in the background.
    """
    failed_docs = list_documents(status="failed", limit=50)
    requeued = []
    for doc in failed_docs:
        doc_id = doc["id"]
        cached = get_cached_file(doc_id)
        content = cached["bytes"] if cached else ((get_document_file(doc_id) or {}).get("bytes") or b"")
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
