"""Document intake and processing pipeline orchestrator."""
import logging
from app.models.document_repo import save_document, add_history
from app.models.result_repo import save_extraction_result
from app.services.extractor import extract_raw_text, extract_structured_fields
from app.services.rag_service import index_document_chunks

logger = logging.getLogger("docuintel.services.pipeline")


def run_document_pipeline(doc_id: str, filename: str, file_bytes: bytes, mime_type: str):
    """Executes end-to-end extraction, validation, and vector indexing."""
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

        # Step 2: Information Extraction
        save_document({
            "id": doc_id,
            "status": "processing",
            "stage": "Information Extraction",
            "progress": 0.6,
            "page_count": page_count,
            "pages_done": page_count,
        })
        extracted = extract_structured_fields(text, filename)
        issues = extracted.get("issues", [])
        has_errors = any(i.get("severity") == "error" for i in issues)
        needs_review = has_errors

        add_history(
            doc_id,
            "Extraction Completed",
            f"Extracted structured fields. Title/Vendor: {extracted.get('supplier_name') or extracted.get('document_title') or 'Document'}.",
        )

        # Step 3: Persist Extraction Results
        save_extraction_result({
            "document_id": doc_id,
            "invoice_number": extracted.get("invoice_number"),
            "invoice_date": extracted.get("invoice_date"),
            "supplier_name": extracted.get("supplier_name") or extracted.get("document_title"),
            "customer_name": extracted.get("customer_name"),
            "subtotal": extracted.get("subtotal"),
            "tax": extracted.get("tax"),
            "total": extracted.get("total"),
            "currency": extracted.get("currency", "USD"),
            "data": extracted,
            "confidence": {"overall": 0.96},
            "overall_confidence": 0.85 if needs_review else 0.96,
            "needs_review": needs_review,
            "reviewed": False,
        })

        # Step 4: Semantic Vector Indexing (Only if all okay; deferred if needs review)
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
            stage_name = "Needs Review (Variance Detected)"
            logger.info(f"Document {doc_id} has discrepancies (needs_review=True). Holding vector indexing until reviewer approval.")

        # Step 5: Mark Completed
        final_status = "needs_review" if needs_review else "completed"
        save_document({
            "id": doc_id,
            "status": final_status,
            "stage": stage_name,
            "progress": 1.0,
            "page_count": page_count,
            "pages_done": page_count,
        })
        add_history(
            doc_id,
            "Flagged for Review" if needs_review else "Processing Completed & Vectorized",
            f"Pipeline finished. Status: {final_status}. Vector DB: {'Stored' if not needs_review else 'Pending approval'}.",
        )
        logger.info(f"Document {doc_id} processed ({final_status}).")

    except Exception as exc:
        logger.error(f"Pipeline error for document {doc_id}: {exc}")
        save_document({
            "id": doc_id,
            "status": "failed",
            "stage": "Failed",
            "error": str(exc),
            "progress": 1.0,
        })
        add_history(
            doc_id,
            "Processing Failed",
            f"Error: {exc}",
        )
