"""Automated test verifying document extraction, database persistence, and LangChain RAG search."""
import os
import sys
import uuid

# Ensure backend root is in Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.models.document_repo import save_document, get_document, delete_document
from app.models.result_repo import get_extraction_results
from app.services.pipeline import run_document_pipeline
from app.services.rag_service import rag_search_and_answer


def test_extraction_pipeline():
    doc_id = str(uuid.uuid4())
    sample_text = b"%PDF-1.4\n1 0 obj\n<< /Title (LangChain Invoice Test) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"

    print(f"Testing LangChain pipeline on document {doc_id}...")

    # 1. Save initial document
    save_document({
        "id": doc_id,
        "filename": "sample_invoice.pdf",
        "file_type": "application/pdf",
        "size_bytes": len(sample_text),
        "status": "queued",
        "stage": "Uploaded",
    })

    # 2. Run LangChain processing pipeline
    run_document_pipeline(doc_id, "sample_invoice.pdf", sample_text, "application/pdf")

    # 3. Verify status
    doc = get_document(doc_id)
    assert doc is not None
    assert doc["status"] in ("completed", "needs_review")
    print("Pipeline completed with status:", doc["status"])

    # 4. Verify LangChain RAG Query
    rag_result = rag_search_and_answer("What is this invoice about?")
    assert "answer" in rag_result
    print("LangChain RAG Query successful! Answer preview:")
    print(rag_result["answer"][:120])

    # 5. Clean up
    delete_document(doc_id)
    print("Test passed and test document cleaned up cleanly!")


if __name__ == "__main__":
    test_extraction_pipeline()
