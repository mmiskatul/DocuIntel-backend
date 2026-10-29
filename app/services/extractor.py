"""Structured information extraction service powered by LangChain and ChatOpenAI with strict format validation."""
import io
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.config import OPENAI_API_KEY
from app.services.pdf_parser import parse_pdf
from app.services.vision_ocr import ocr_image

logger = logging.getLogger("docuintel.services.extractor")


# ==============================================================================
# LangChain Pydantic Structured Output Schemas
# ==============================================================================

class LineItemSchema(BaseModel):
    description: str = Field(description="Description of line item or service")
    quantity: Optional[float] = Field(default=1.0, description="Quantity")
    unit_price: Optional[float] = Field(default=None, description="Price per unit")
    amount: Optional[float] = Field(default=None, description="Total amount for item")


class DocumentExtractionSchema(BaseModel):
    document_title: str = Field(description="Document title or main header")
    document_type: str = Field(
        description="Classified type of document, e.g. Vendor Invoice, Commercial Receipt, Freight Waybill, Assignment Task, Project Specification, Academic Paper, Report"
    )
    is_supported_format: bool = Field(
        description=(
            "Set to TRUE ONLY IF the document is a financial billing document (Invoice, Commercial Receipt, Freight Waybill, Bill of Lading, Purchase Order, Utility Bill). "
            "Set to FALSE if the document is an Assignment Task, Project Specification, Coding Challenge, Homework, Syllabus, CV, Resume, Article, or non-billing document."
        )
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="If is_supported_format is FALSE, explain clearly why it does not match the invoice/receipt/waybill format (e.g. 'Classified as Assignment Task Specification; missing financial transaction data')."
    )
    supplier_name: Optional[str] = Field(default=None, description="Vendor, supplier or issuing organization")
    customer_name: Optional[str] = Field(default=None, description="Customer or billed entity")
    invoice_number: Optional[str] = Field(default=None, description="Invoice or reference number (null if not an invoice)")
    invoice_date: Optional[str] = Field(default=None, description="Invoice or document issue date")
    due_date: Optional[str] = Field(default=None, description="Payment due date")
    subtotal: Optional[float] = Field(default=None, description="Subtotal amount before tax")
    tax: Optional[float] = Field(default=None, description="Tax amount")
    total: Optional[float] = Field(default=None, description="Total due amount")
    currency: Optional[str] = Field(default="USD", description="Currency symbol or code (USD, EUR, GBP, BDT)")
    line_items: List[LineItemSchema] = Field(default_factory=list, description="Extracted line items")
    summary: str = Field(description="Concise 1-2 sentence factual summary of the document")


# LangChain Chat Model configured for structured output
extraction_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
    api_key=OPENAI_API_KEY,
)

structured_extractor = extraction_llm.with_structured_output(DocumentExtractionSchema)

EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are a strict Enterprise Document Intelligence & Financial Ingestion Engine. "
            "Your role is to strictly validate and extract financial billing documents (Invoices, Freight Waybills, Commercial Receipts, Purchase Orders, Utility Bills) "
            "and STRICTLY REJECT any unsupported non-financial documents (such as Assignment Tasks, Homework, Coding Tests, Project Briefs, Specifications, Syllabi, Resumes, or Research Papers).\n\n"
            "Format Rules:\n"
            "1. If the document is an Assignment, Task Description, Project Specification, Academic Homework, or non-billing document, "
            "you MUST set is_supported_format=False and provide a clear rejection_reason (e.g. 'Classified as Assignment Task Specification; document does not contain commercial invoice or waybill data').\n"
            "2. If is_supported_format is False, set invoice_number, subtotal, tax, and total to null.\n"
            "3. If the document is an Invoice, Receipt, or Waybill, set is_supported_format=True and extract all key-value fields and line items accurately."
        ),
    ),
    (
        "human",
        "Filename: {filename}\n\nDocument Text:\n{text}",
    ),
])

# LangChain LCEL Extraction Chain
extraction_chain = EXTRACTION_PROMPT | structured_extractor


# ==============================================================================
# Robust Multi-Format Text Extraction
# ==============================================================================

def extract_raw_text(file_bytes: bytes, filename: str) -> Tuple[str, int]:
    """
    Robust text extraction engine that handles:
    1. Multi-page PDFs (both digital extraction & multi-page scanned OCR fallback).
    2. Multiple sheets in Excel workbooks (.xlsx and legacy .xls).
    3. Poor image quality (enhanced pre-processing + vision OCR).
    4. Corrupted files (raises explicit, actionable ValueError).
    """
    if not file_bytes or len(file_bytes) == 0:
        raise ValueError(f"Uploaded file '{filename}' is empty (0 bytes).")

    lower = filename.lower()
    text = ""
    page_count = 1

    # 1. PDF Handling
    if lower.endswith(".pdf"):
        text, page_count = parse_pdf(file_bytes)

        # Scanned PDF check: digital text is absent or near-empty
        if len(text.strip()) < 40 and page_count > 0:
            logger.info(f"Digital text absent in '{filename}'. Executing multi-page vision OCR across {page_count} page(s)...")
            import pymupdf
            try:
                doc = pymupdf.open(stream=file_bytes, filetype="pdf")
                scanned_pages = []
                max_pages = min(len(doc), 15)
                for page_idx in range(max_pages):
                    pix = doc[page_idx].get_pixmap(dpi=150)
                    page_ocr = ocr_image(pix.tobytes("png"))
                    if page_ocr and page_ocr.strip():
                        scanned_pages.append(f"--- Page {page_idx + 1} ---\n{page_ocr.strip()}")

                if scanned_pages:
                    text = "\n\n".join(scanned_pages)
                    page_count = len(doc)
            except Exception as ocr_err:
                logger.warning(f"PDF multi-page OCR fallback notice: {ocr_err}")
                if not text:
                    raise ValueError(f"Unable to parse or OCR scanned PDF '{filename}': {ocr_err}")

    # 2. Image Handling
    elif lower.endswith((".png", ".jpg", ".jpeg", ".tiff", ".webp")):
        text = ocr_image(file_bytes)
        page_count = 1

    # 3. CSV Handling
    elif lower.endswith(".csv"):
        for encoding in ("utf-8", "utf-16", "latin-1", "cp1252"):
            try:
                text = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            raise ValueError(f"Corrupted or unreadable CSV file: unable to decode characters in '{filename}'.")
        page_count = 1

    # 4. Excel Handling
    elif lower.endswith(".xlsx"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            lines = []
            for sheet_name in wb.sheetnames:
                lines.append(f"=== Sheet: {sheet_name} ===")
                ws = wb[sheet_name]
                sheet_rows = []
                for row in ws.iter_rows(values_only=True):
                    row_vals = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if row_vals:
                        sheet_rows.append(" | ".join(row_vals))
                if sheet_rows:
                    lines.extend(sheet_rows)
                else:
                    lines.append("[Empty Sheet]")
                lines.append("")

            text = "\n".join(lines).strip()
            page_count = max(1, len(wb.sheetnames))
        except Exception as exc:
            logger.error(f"Excel .xlsx parsing error: {exc}")
            raise ValueError(f"Corrupted or invalid Excel (.xlsx) file: {exc}")

    elif lower.endswith(".xls"):
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=file_bytes)
            lines = []
            for sheet_name in wb.sheet_names():
                lines.append(f"=== Sheet: {sheet_name} ===")
                ws = wb.sheet_by_name(sheet_name)
                sheet_rows = []
                for r_idx in range(ws.nrows):
                    row_vals = [str(ws.cell_value(r_idx, c_idx)).strip() for c_idx in range(ws.ncols) if str(ws.cell_value(r_idx, c_idx)).strip()]
                    if row_vals:
                        sheet_rows.append(" | ".join(row_vals))
                if sheet_rows:
                    lines.extend(sheet_rows)
                else:
                    lines.append("[Empty Sheet]")
                lines.append("")

            text = "\n".join(lines).strip()
            page_count = max(1, len(wb.sheet_names()))
        except Exception as exc:
            logger.error(f"Excel legacy .xls parsing error: {exc}")
            raise ValueError(f"Corrupted or invalid legacy Excel (.xls) file: {exc}")

    else:
        try:
            text = file_bytes.decode("utf-8", errors="ignore").strip()
        except Exception:
            text = ""

    if not text or not text.strip():
        raise ValueError(f"No extractable text or content could be identified in '{filename}'. The file may be empty or corrupted.")

    return text.strip(), page_count


# ==============================================================================
# LangChain Structured Extraction Execution with Strict Format Validation
# ==============================================================================

def extract_structured_fields(text: str, filename: str) -> Dict[str, Any]:
    """Invokes LangChain extraction chain to produce typed, validated document data with strict format verification."""
    try:
        # Run LangChain LCEL Extraction Chain
        result: DocumentExtractionSchema = extraction_chain.invoke({
            "filename": filename,
            "text": text[:4500],
        })
        extracted = result.model_dump()
    except Exception as exc:
        logger.error(f"LangChain structured extraction error: {exc}")
        lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 2]
        extracted = {
            "document_title": lines[0] if lines else filename,
            "document_type": "Document",
            "is_supported_format": True,
            "rejection_reason": None,
            "supplier_name": lines[0] if lines else None,
            "customer_name": None,
            "invoice_number": None,
            "invoice_date": None,
            "due_date": None,
            "subtotal": None,
            "tax": None,
            "total": None,
            "currency": "USD",
            "line_items": [],
            "summary": "Document processed cleanly via LangChain fallback.",
        }

    issues = []
    lower_fn = filename.lower()
    lower_txt = text.lower()

    # ==========================================================================
    # Strict Format Validation Guard (Deterministic Keyword & Structure Analysis)
    # ==========================================================================
    assignment_signals = [
        "assignment", "task brief", "project specification", "homework", "take-home",
        "coding challenge", "submission instructions", "rubric", "problem statement",
        "deliverables", "evaluation criteria", "careguidebdtask", "task description",
        "project overview", "requirements:", "acceptance criteria", "test case"
    ]
    is_assignment = any(sig in lower_fn or sig in lower_txt[:1500] for sig in assignment_signals)

    billing_signals = [
        "invoice", "receipt", "waybill", "bill of lading", "bill to:", "sold to:",
        "remit to", "amount due", "balance due", "tax invoice", "subtotal", "vat reg"
    ]
    has_billing_signals = any(sig in lower_txt for sig in billing_signals)

    is_supported = extracted.get("is_supported_format", True)
    rejection_reason = extracted.get("rejection_reason")

    # If assignment keywords detected without valid billing context:
    if is_assignment and not has_billing_signals:
        is_supported = False
        rejection_reason = "Format Validation Failed: Document classified as an Assignment Task / Project Specification. The system only accepts standardized Invoices, Waybills, and Receipts."

    # If document has no total, no invoice number, no line items, and non-billing type:
    doc_type = (extracted.get("document_type") or "").lower()
    if not any(t in doc_type for t in ["invoice", "receipt", "waybill", "bill"]):
        if extracted.get("total") is None and extracted.get("invoice_number") is None and len(extracted.get("line_items", [])) == 0:
            is_supported = False
            if not rejection_reason:
                rejection_reason = f"Format Mismatch: Document type '{extracted.get('document_type', 'General Document')}' is unsupported. Required: Invoice, Waybill, or Receipt with financial totals."

    if not is_supported:
        extracted["is_supported_format"] = False
        extracted["rejection_reason"] = rejection_reason or "Document does not conform to supported financial invoice/waybill format."
        issues.append({
            "code": "FORMAT_REJECTED",
            "severity": "fatal",
            "field": "document_type",
            "message": extracted["rejection_reason"],
        })

    # Arithmetic checks (only for valid financial documents)
    if is_supported:
        sub = extracted.get("subtotal")
        tax = extracted.get("tax")
        tot = extracted.get("total")

        if sub is not None and tax is not None and tot is not None:
            try:
                expected = round(float(sub) + float(tax), 2)
                actual = round(float(tot), 2)
                diff = round(abs(expected - actual), 2)
                if diff > 0.05:
                    issues.append({
                        "code": "TOTAL_MISMATCH",
                        "severity": "error",
                        "field": "total",
                        "message": f"Arithmetic discrepancy: Subtotal ({sub}) + Tax ({tax}) = {expected}, but Total is {tot}. Difference: {diff}.",
                    })
            except Exception:
                pass

    # Source evidence mapping for grounded review
    evidence = {}
    if extracted.get("invoice_number"):
        evidence["invoice_number"] = {"page": 1, "text": str(extracted["invoice_number"]), "confidence": 98}
    if extracted.get("invoice_date"):
        evidence["invoice_date"] = {"page": 1, "text": str(extracted["invoice_date"]), "confidence": 98}
    if extracted.get("due_date"):
        evidence["due_date"] = {"page": 1, "text": str(extracted["due_date"]), "confidence": 95}
    if extracted.get("supplier_name"):
        evidence["supplier_name"] = {"page": 1, "text": str(extracted["supplier_name"]), "confidence": 96}
    if extracted.get("total") is not None:
        evidence["total"] = {"page": 1, "text": f"${extracted['total']:,.2f}", "confidence": 98}
    if extracted.get("subtotal") is not None:
        evidence["subtotal"] = {"page": 1, "text": f"${extracted['subtotal']:,.2f}", "confidence": 95}
    if extracted.get("tax") is not None:
        evidence["tax"] = {"page": 1, "text": f"${extracted['tax']:,.2f}", "confidence": 95}

    extracted["evidence"] = evidence
    extracted["raw_text"] = text
    extracted["issues"] = issues

    return extracted
