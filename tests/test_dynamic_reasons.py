"""Automated tests verifying dynamic failure and rejection reasons across different document types."""
import io
import uuid
import pytest
import pymupdf

from app.models.document_repo import save_document, get_document, delete_document
from app.services.pipeline import run_document_pipeline
from app.services.extractor import extract_structured_fields


def test_dynamic_rejection_for_resume_cv():
    """Verify a Resume/CV is dynamically classified as a Resume with a specific career history rejection reason."""
    resume_text = (
        "JOHN DOE - SENIOR CLOUD ARCHITECT\n"
        "Email: john.doe@email.com | Phone: (555) 123-4567\n\n"
        "PROFESSIONAL SUMMARY\n"
        "Experienced cloud solutions architect with 8+ years designing scalable AWS infrastructure.\n\n"
        "WORK EXPERIENCE\n"
        "Lead Cloud Engineer - Tech Corp (2020 - Present)\n"
        "- Migrated 50+ microservices to Kubernetes.\n"
        "- Reduced cloud spend by 30%.\n\n"
        "EDUCATION\n"
        "Bachelor of Science in Computer Science - University of California (2016)\n\n"
        "TECHNICAL SKILLS & TOOLS\n"
        "Python, Go, Docker, Kubernetes, Terraform, AWS, GCP"
    )
    res = extract_structured_fields(resume_text, "john_doe_resume.pdf")
    assert res["is_supported_format"] is False
    assert "Resume" in res["document_type"] or "CV" in res["document_type"] or "Curriculum" in res["document_type"]
    assert "resume" in res["rejection_reason"].lower() or "career" in res["rejection_reason"].lower() or "qualifications" in res["rejection_reason"].lower()
    assert "assignment" not in res["rejection_reason"].lower()


def test_dynamic_rejection_for_legal_contract():
    """Verify an NDA/Contract is dynamically classified as a Legal Contract with a specific contractual rejection reason."""
    contract_text = (
        "MUTUAL NON-DISCLOSURE AND CONFIDENTIALITY AGREEMENT\n\n"
        "This Agreement is entered into on this 1st day of October, 2026, by and between Alpha Corp and Beta Inc.\n\n"
        "1. Purpose:\n"
        "The parties agree that all proprietary technical discussions shall remain confidential.\n\n"
        "2. Governing Law:\n"
        "This Agreement shall be construed in accordance with the laws of the State of Delaware.\n\n"
        "IN WITNESS WHEREOF, the authorized representatives have executed this contract."
    )
    res = extract_structured_fields(contract_text, "alpha_beta_nda_agreement.pdf")
    assert res["is_supported_format"] is False
    assert "Contract" in res["document_type"] or "Agreement" in res["document_type"] or "Legal" in res["document_type"]
    assert "contract" in res["rejection_reason"].lower() or "agreement" in res["rejection_reason"].lower() or "legal" in res["rejection_reason"].lower()
    assert "assignment" not in res["rejection_reason"].lower()


def test_dynamic_rejection_for_academic_paper():
    """Verify a scientific/academic paper is dynamically classified as an Academic Paper with a literature rejection reason."""
    paper_text = (
        "Deep Learning for Multi-Modal Document Representation\n\n"
        "ABSTRACT\n"
        "We present a transformer-based vision-language architecture for parsing semi-structured documents.\n\n"
        "1. Introduction\n"
        "Document intelligence requires grounded spatial and semantic representations.\n\n"
        "2. Methodology & Experiments\n"
        "We evaluate on the FUNSD benchmark dataset.\n\n"
        "REFERENCES\n"
        "[1] Vaswani et al. Attention Is All You Need. NeurIPS 2017.\n"
        "DOI: 10.1145/3308558"
    )
    res = extract_structured_fields(paper_text, "deep_learning_research_paper.pdf")
    assert res["is_supported_format"] is False
    assert "Academic" in res["document_type"] or "Paper" in res["document_type"] or "Research" in res["document_type"]
    assert "research" in res["rejection_reason"].lower() or "paper" in res["rejection_reason"].lower() or "scholarly" in res["rejection_reason"].lower()
    assert "assignment" not in res["rejection_reason"].lower()


def test_dynamic_pipeline_failure_reasons():
    """Verify corrupted files and empty files produce dynamic, descriptive failure reasons in pipeline."""
    # 1. Corrupted file test
    doc_id_1 = str(uuid.uuid4())
    save_document({
        "id": doc_id_1,
        "filename": "damaged_stream.pdf",
        "file_type": "application/pdf",
        "size_bytes": 100,
        "status": "queued",
    })
    run_document_pipeline(doc_id_1, "damaged_stream.pdf", b"BAD_BINARY_HEADER", "application/pdf")
    doc_1 = get_document(doc_id_1)
    assert doc_1["status"] == "failed"
    assert "Corrupted File Error" in doc_1["stage"]
    assert "damaged or unreadable" in doc_1["error"]
    delete_document(doc_id_1)

    # 2. Empty file test
    doc_id_2 = str(uuid.uuid4())
    save_document({
        "id": doc_id_2,
        "filename": "empty_document.pdf",
        "file_type": "application/pdf",
        "size_bytes": 0,
        "status": "queued",
    })
    run_document_pipeline(doc_id_2, "empty_document.pdf", b"", "application/pdf")
    doc_2 = get_document(doc_id_2)
    assert doc_2["status"] == "failed"
    assert "Empty File Error" in doc_2["stage"]
    assert "0 bytes" in doc_2["error"]
    delete_document(doc_id_2)

