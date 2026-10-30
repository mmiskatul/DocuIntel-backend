"""Document intake and processing pipeline orchestrator."""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from app.config import EXTRACTION_PARALLELISM, MAX_INVOICES_PER_DOCUMENT
from app.models.document_repo import save_document, add_history
from app.models.result_repo import delete_results_for_document, save_extraction_result
from app.services.segmenter import split_invoice_segments
from app.services.vision_ocr import get_ocr_meta, reset_ocr_meta
from app.services.extractor import extract_raw_text, extract_structured_fields
from app.services.validator import validate_document_data
from app.services.rag_service import index_document_chunks

logger = logging.getLogger("docuintel.services.pipeline")


KEY_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "total")


def compute_confidence(extracted: Dict[str, Any], needs_review: bool, ocr_conf=None) -> float:
    """Heuristic 0..1 score: share of key fields found, capped by real OCR confidence, reduced when flagged.
    (Not a model probability; it is derived from measurable signals only.)"""
    found = sum(1 for k in KEY_FIELDS if extracted.get(k) not in (None, ""))
    score = 0.5 + 0.5 * found / len(KEY_FIELDS)
    if ocr_conf is not None:
        score = min(score, float(ocr_conf))
    if needs_review:
        score *= 0.85
    return round(max(0.0, min(score, 1.0)), 2)


def _ocr_issue():
    return {"code": "LOW_OCR_QUALITY", "severity": "error", "field": "document",
            "message": "The scan could not be read reliably (low OCR confidence or illegible text); verify the extracted values against the original.",
            "details": {}}


def _run_multi_invoice(doc_id: str, filename: str, text: str, segments: List[Dict[str, Any]], page_count: int, ocr: Dict[str, Any] = None):
    ocr = ocr or {}
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

    items = [{"extracted": e, "text": seg["text"], "page_range": [seg["start_page"], seg["end_page"]]}
             for seg, e in zip(segments, extracted_list)]
    _persist_invoices(doc_id, filename, text, items, page_count, ocr)


def _persist_invoices(doc_id: str, filename: str, text: str, items: List[Dict[str, Any]], page_count: int, ocr: Dict[str, Any]):
    """Validates and stores one result row per invoice, then sets the document's final status."""
    n = len(items)
    seen = set()
    any_review, saved = False, 0
    skipped = []
    first_reason = None
    for item in items:
        extracted, seg_text = item["extracted"], item["text"]
        issues, needs_review, _ = validate_document_data(extracted=extracted, doc_id=doc_id, filename=filename, check_duplicates=item.get("source") != "ledger")
        rejected = (not extracted.get("is_supported_format", True)) or any(
            i.get("code") == "FORMAT_REJECTED" or i.get("severity") == "fatal" for i in issues)
        if rejected:
            skipped.append(f"pages {item['page_range'][0]}-{item['page_range'][1]}" if item.get("page_range") else f"row {item.get('row')}")
            first_reason = first_reason or extracted.get("rejection_reason")
            continue

        key = (str(extracted.get("invoice_number") or "").strip().lower(), str(extracted.get("supplier_name") or "").strip().lower())
        if key[0] and key in seen:
            issues.append({"code": "DUPLICATE_INVOICE", "severity": "error", "field": "invoice_number",
                           "message": f"Invoice #{extracted.get('invoice_number')} appears more than once in this document.", "details": {}})
            needs_review = True
        seen.add(key)
        if "[illegible]" in seg_text.lower() or ocr.get("low_confidence"):
            issues.append(_ocr_issue())
            needs_review = True

        extracted["issues"] = issues
        if item.get("page_range"):
            extracted["page_range"] = item["page_range"]
        if item.get("source"):
            extracted["source"] = item["source"]
        extracted["segment_index"] = saved + 1
        conf = compute_confidence(extracted, needs_review, ocr.get("confidence"))
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



def _run_ledger(doc_id: str, filename: str, text: str, records: List[Dict[str, Any]], page_count: int):
    """A spreadsheet whose rows are invoices: structured straight from the cells (no LLM), then validated like any invoice."""
    delete_results_for_document(doc_id)
    add_history(doc_id, "Invoice Ledger Detected", f"Spreadsheet parsed as an invoice table with {len(records)} row(s); extracted deterministically.")
    items = []
    for rec in records:
        extracted = {
            "document_title": filename, "document_type": "Invoice Ledger Row", "is_supported_format": True, "rejection_reason": None,
            "invoice_number": rec.get("invoice_number"), "invoice_date": rec.get("invoice_date"), "supplier_name": rec.get("supplier_name"),
            "customer_name": rec.get("customer_name"), "subtotal": rec.get("subtotal"), "tax": rec.get("tax"), "total": rec.get("total"),
            "currency": rec.get("currency"), "line_items": [], "due_date": None,
            "summary": f"Ledger row {rec.get('row')} of sheet '{rec.get('sheet')}'.",
        }
        items.append({"extracted": extracted, "text": "", "row": rec.get("row"), "source": "ledger"})
    _persist_invoices(doc_id, filename, text, items, page_count, {})


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
        reset_ocr_meta()
        text, page_count = extract_raw_text(file_bytes, filename)
        ocr = get_ocr_meta()
        if filename.lower().endswith((".xlsx", ".xls", ".csv")):
            from app.services.spreadsheet import detect_ledger, read_sheets
            records = detect_ledger(read_sheets(file_bytes, filename))
            if records:
                save_document({"id": doc_id, "status": "processing", "stage": "Validating Ledger Rows", "progress": 0.6,
                               "page_count": page_count, "pages_done": page_count, "extracted_text": text})
                add_history(doc_id, "OCR Parsing Completed", f"Extracted {len(text)} characters across {page_count} sheet(s).")
                _run_ledger(doc_id, filename, text, records, page_count)
                return
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
            _run_multi_invoice(doc_id, filename, text, segments, page_count, ocr)
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
        if "[illegible]" in text.lower() or ocr.get("low_confidence"):
            issues.append(_ocr_issue())
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
        overall_conf = compute_confidence(extracted, needs_review, ocr.get("confidence"))
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
