"""Document intake and processing pipeline orchestrator."""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from app.config import EXTRACTION_PARALLELISM, MAX_INVOICES_PER_DOCUMENT
from app.models.document_repo import save_document, add_history
from app.models.result_repo import delete_results_for_document, save_extraction_result
from app.services.segmenter import split_invoice_segments
from app.services.extractor import extract_raw_text, extract_structured_fields
from app.services.validator import validate_document_data
from app.services.rag_service import index_document_chunks

logger = logging.getLogger("docuintel.services.pipeline")


def _run_multi_invoice(doc_id: str, filename: str, text: str, segments: List[Dict[str, Any]], page_count: int):
    """Processes a bundle PDF: one extraction + validation + result row per detected invoice."""
    n = len(segments)
    delete_results_for_document(doc_id)
    add_history(doc_id, "Multiple Invoices Detected", f"Document split into {n} invoice segment(s) by invoice number.")

    def _extract(seg):
        return extract_structured_fields(seg["text"], f"{filename} (pages {seg['start_page']}-{seg['end_page']})")

    extracted_list = []
    with ThreadPoolExecutor(max_workers=max(1, EXTRACTION_PARALLELISM)) as pool:
        for i, extracted in enumerate(pool.map(_extract, segments)):
            extracted_list.append(extracted)
            save_document({"id": doc_id, "status": "processing", "stage": f"Information Extraction ({i + 1}/{n})",
                           "progress": round(0.6 + 0.25 * (i + 1) / n, 2)})

    seen = set()
    any_review, saved = False, 0
    skipped = []
    first_reason = None
    for seg, extracted in zip(segments, extracted_list):
        issues, needs_review, _ = validate_document_data(extracted=extracted, doc_id=doc_id, filename=filename, check_duplicates=True)
        rejected = (not extracted.get("is_supported_format", True)) or any(
            i.get("code") == "FORMAT_REJECTED" or i.get("severity") == "fatal" for i in issues)
        if rejected:
            skipped.append(f"pages {seg['start_page']}-{seg['end_page']}")
            first_reason = first_reason or extracted.get("rejection_reason")
            continue

        key = (str(extracted.get("invoice_number") or "").strip().lower(), str(extracted.get("supplier_name") or "").strip().lower())
        if key[0] and key in seen:
            issues.append({"code": "DUPLICATE_INVOICE", "severity": "error", "field": "invoice_number",
                           "message": f"Invoice #{extracted.get('invoice_number')} appears more than once in this document.", "details": {}})
            needs_review = True
        seen.add(key)
        if "[illegible]" in seg["text"].lower():
            issues.append({"code": "LOW_OCR_QUALITY", "severity": "error", "field": "document",
                           "message": "Parts of this invoice were illegible to OCR; verify against the original.", "details": {}})
            needs_review = True

        extracted["issues"] = issues
        extracted["page_range"] = [seg["start_page"], seg["end_page"]]
        extracted["segment_index"] = saved + 1
        conf = 0.82 if needs_review else 0.98
        save_extraction_result({
            "document_id": doc_id,
            "invoice_number": extracted.get("invoice_number"), "invoice_date": extracted.get("invoice_date"),
            "supplier_name": extracted.get("supplier_name"), "customer_name": extracted.get("customer_name"),
            "subtotal": extracted.get("subtotal"), "tax": extracted.get("tax"), "total": extracted.get("total"),
            "currency": extracted.get("currency", "USD"), "data": extracted, "confidence": {"overall": conf},
            "overall_confidence": conf, "needs_review": needs_review, "reviewed": False, "validation_errors": issues,
        }, insert_new=True)
        saved += 1
        any_review = any_review or needs_review

    if saved == 0:
        reason = first_reason or "Document does not match supported business invoice/waybill format."
        save_document({"id": doc_id, "status": "rejected", "stage": "Format Rejected", "error": reason,
                       "progress": 1.0, "page_count": page_count, "pages_done": page_count})
        add_history(doc_id, "Document Rejected (Format Mismatch)", reason)
        return

    if skipped:
        add_history(doc_id, "Segments Skipped", f"Non-invoice segment(s) ignored: {', '.join(skipped)}.")

    if any_review:
        stage = "Needs Review (Validation Flagged)"
        add_history(doc_id, "Flagged for Manual Review", f"{saved} invoice(s) extracted; at least one needs review. Vector indexing held.")
    else:
        save_document({"id": doc_id, "status": "processing", "stage": "Vector Indexing", "progress": 0.9})
        index_document_chunks(doc_id, text)
        stage = "Completed & Vectorized"
        add_history(doc_id, "Processing Completed & Vectorized", f"{saved} invoice(s) extracted and validated cleanly.")

    save_document({"id": doc_id, "status": "needs_review" if any_review else "completed", "stage": stage,
                   "progress": 1.0, "page_count": page_count, "pages_done": page_count})


def run_document_pipeline(doc_id: str, filename: str, file_bytes: bytes, mime_type: str):
    """Executes end-to-end extraction, validation rules, and conditional vector indexing."""
    try:
        # Step 1: Parsing & Text Extraction
        save_document({
            "id": doc_id,
            "status": "processing",
            "stage": "OCR & Parsing",
            "progress": 0.3,
        })
        text, page_count = extract_raw_text(file_bytes, filename)
        add_history(
            doc_id,
            "OCR Parsing Completed",
            f"Extracted {len(text)} characters across {page_count} page(s).",
        )

        # Step 2: Information Extraction & Format Classification
        save_document({
            "id": doc_id,
            "status": "processing",
            "stage": "Information Extraction",
            "progress": 0.6,
            "page_count": page_count,
            "pages_done": page_count,
            "extracted_text": text,
        })
        delete_results_for_document(doc_id)  # drop stale rows from earlier runs (re-processing)
        segments = split_invoice_segments(text, MAX_INVOICES_PER_DOCUMENT)
        if len(segments) > 1:
            _run_multi_invoice(doc_id, filename, text, segments, page_count)
            return

        extracted = extract_structured_fields(text, filename)

        # Step 3: Run Full Enterprise Validation Rules
        # (1. Missing fields, 2. Incorrect numeric values, 3. Duplicate invoices, 4. Inconsistencies)
        issues, needs_review, review_stage = validate_document_data(
            extracted=extracted,
            doc_id=doc_id,
            filename=filename,
            check_duplicates=True,
        )
        if "[illegible]" in text.lower():
            issues.append({
                "code": "LOW_OCR_QUALITY",
                "severity": "error",
                "field": "document",
                "message": "Parts of the document were illegible to OCR; extracted values must be verified against the original.",
                "details": {},
            })
            if not needs_review:
                needs_review, review_stage = True, "Needs Review (Low OCR Quality)"
        extracted["issues"] = issues

        is_supported = extracted.get("is_supported_format", True)
        format_issues = [i for i in issues if i.get("code") == "FORMAT_REJECTED" or i.get("severity") == "fatal"]

        # If format validation fails (e.g. Assignment task, specification, non-billing document): REJECT!
        if not is_supported or format_issues:
            rejection_msg = extracted.get("rejection_reason") or (format_issues[0]["message"] if format_issues else "Document does not match supported business invoice/waybill format.")
            logger.warning(f"Document {doc_id} ('{filename}') REJECTED: {rejection_msg}")

            save_document({
                "id": doc_id,
                "status": "rejected",
                "stage": "Format Rejected",
                "error": rejection_msg,
                "progress": 1.0,
                "page_count": page_count,
                "pages_done": page_count,
            })
            save_extraction_result({
                "document_id": doc_id,
                "invoice_number": None,
                "invoice_date": None,
                "supplier_name": None,
                "customer_name": None,
                "subtotal": None,
                "tax": None,
                "total": None,
                "currency": "USD",
                "data": extracted,
                "confidence": {"overall": 0.0},
                "overall_confidence": 0.0,
                "needs_review": False,
                "reviewed": False,
                "validation_errors": issues,
            })
            add_history(
                doc_id,
                "Document Rejected (Format Mismatch)",
                f"Automated validation failed: {rejection_msg}. Document will NOT be stored in vector database.",
            )
            return

        add_history(
            doc_id,
            "Extraction Completed",
            f"Extracted structured fields. Vendor: {extracted.get('supplier_name') or 'Not specified'}.",
        )

        # Step 4: Persist Extraction Results
        overall_conf = 0.82 if needs_review else 0.98
        save_extraction_result({
            "document_id": doc_id,
            "invoice_number": extracted.get("invoice_number"),
            "invoice_date": extracted.get("invoice_date"),
            "supplier_name": extracted.get("supplier_name"),
            "customer_name": extracted.get("customer_name"),
            "subtotal": extracted.get("subtotal"),
            "tax": extracted.get("tax"),
            "total": extracted.get("total"),
            "currency": extracted.get("currency", "USD"),
            "data": extracted,
            "confidence": {"overall": overall_conf},
            "overall_confidence": overall_conf,
            "needs_review": needs_review,
            "reviewed": False,
            "validation_errors": issues,
        })

        # Step 5: Semantic Vector Indexing (Only if all checks pass; held if needs review)
        if not needs_review:
            save_document({
                "id": doc_id,
                "status": "processing",
                "stage": "Vector Indexing",
                "progress": 0.85,
            })
            index_document_chunks(doc_id, text)
            stage_name = "Completed & Vectorized"
        else:
            stage_name = review_stage or "Needs Review (Validation Flagged)"
            logger.info(f"Document {doc_id} requires manual review ({stage_name}). Holding vector indexing until reviewer approval.")

        # Step 6: Mark Final Status
        final_status = "needs_review" if needs_review else "completed"
        save_document({
            "id": doc_id,
            "status": final_status,
            "stage": stage_name,
            "progress": 1.0,
            "page_count": page_count,
            "pages_done": page_count,
        })

        # Record audit log
        if needs_review:
            error_codes = [i["code"] for i in issues if i.get("severity") == "error"]
            add_history(
                doc_id,
                "Flagged for Manual Review",
                f"Validation rules flagged: {', '.join(error_codes)}. Vector DB storage held pending approval.",
            )
        else:
            add_history(
                doc_id,
                "Processing Completed & Vectorized",
                "All validation rules passed cleanly (0 errors). Chunks stored in vector database.",
            )

        logger.info(f"Document {doc_id} processed ({final_status}).")

    except Exception as exc:
        err_msg = str(exc)
        logger.error(f"Pipeline error for document {doc_id}: {err_msg}")

        # Categorize dynamic failure reason based on actual error cause
        lower_err = err_msg.lower()
        if "empty" in lower_err or "0 bytes" in lower_err:
            failed_stage = "Empty File Error"
            failed_detail = f"Processing Failed: Uploaded file '{filename}' contains 0 bytes. File is completely empty."
        elif any(k in lower_err for k in ["corrupt", "damaged", "stream", "cannot open", "not a valid", "filedataerror"]):
            failed_stage = "Corrupted File Error"
            failed_detail = f"Processing Failed: File structure of '{filename}' is damaged or unreadable by document parser ({err_msg})."
        elif "password" in lower_err or "encrypted" in lower_err:
            failed_stage = "Encrypted Document Error"
            failed_detail = f"Processing Failed: Document '{filename}' is password-protected or encrypted."
        elif "unsupported" in lower_err:
            failed_stage = "Unsupported Format Error"
            failed_detail = f"Processing Failed: File format of '{filename}' is unsupported."
        else:
            failed_stage = "Processing Error"
            failed_detail = f"Processing Failed: {err_msg}"

        save_document({
            "id": doc_id,
            "status": "failed",
            "stage": failed_stage,
            "error": failed_detail,
            "progress": 1.0,
        })
        add_history(
            doc_id,
            "Pipeline Failed",
            failed_detail,
            stage=failed_stage,
        )
