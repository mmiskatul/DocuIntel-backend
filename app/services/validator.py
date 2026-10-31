"""Enterprise Validation Engine for financial document intake.

Implements strict validation rules to:
1. Identify missing fields (mandatory and secondary).
2. Identify incorrect numeric values (negatives, zero with items, NaN/Inf).
3. Identify duplicate invoices (by invoice_number and supplier_name in database).
4. Identify inconsistencies between subtotal, tax, line items, and total amount.
5. Flag records requiring manual review and prevent premature vector indexing.
"""
import math
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.models.result_repo import find_duplicate_invoice

logger = logging.getLogger("docuintel.services.validator")


class ValidationIssue:
    def __init__(self, code: str, severity: str, field: str, message: str, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.severity = severity  # "error" (triggers manual review), "warning", or "info"
        self.field = field
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
            "message": self.message,
            "details": self.details,
        }


def validate_document_data(
    extracted: Dict[str, Any],
    doc_id: str,
    filename: str,
    check_duplicates: bool = True,
) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    """
    Runs full multi-rule validation against extracted document data.

    Returns:
        issues: List of structured validation issue dictionaries.
        needs_review: Boolean indicating whether manual review is required.
        review_stage: A descriptive stage label (e.g. 'Needs Review (Arithmetic Variance)').
    """
    issues: List[ValidationIssue] = []

    # Amounts in messages carry the document's own currency code (not an assumed "$")
    _cur = str(extracted.get("currency") or "").strip().upper()
    def _m(x: float) -> str:
        return f"{_cur} {x:,.2f}" if _cur else f"{x:,.2f}"

    # Format check: if already marked format rejected, return immediately
    if not extracted.get("is_supported_format", True):
        rejection_reason = extracted.get("rejection_reason") or "Document format rejected."
        return [{
            "code": "FORMAT_REJECTED",
            "severity": "fatal",
            "field": "document_type",
            "message": rejection_reason,
            "details": {},
        }], False, "Format Rejected"

    inv_num = extracted.get("invoice_number")
    inv_date = extracted.get("invoice_date")
    supplier = extracted.get("supplier_name")
    customer = extracted.get("customer_name")
    subtotal = extracted.get("subtotal")
    tax = extracted.get("tax")
    total = extracted.get("total")
    line_items = extracted.get("line_items") or []

    # ==========================================================================
    # RULE 1: IDENTIFY MISSING FIELDS
    # ==========================================================================
    if not inv_num or not str(inv_num).strip():
        issues.append(ValidationIssue(
            code="MISSING_INVOICE_NUMBER",
            severity="error",
            field="invoice_number",
            message="Invoice number is missing. Invoices must have an explicit identifier for tracking.",
        ))

    if not supplier or not str(supplier).strip():
        issues.append(ValidationIssue(
            code="MISSING_SUPPLIER_NAME",
            severity="error",
            field="supplier_name",
            message="Supplier/Vendor entity name is missing or could not be determined.",
        ))

    if total is None:
        issues.append(ValidationIssue(
            code="MISSING_TOTAL",
            severity="error",
            field="total",
            message="Total payable amount is missing or uncertain in document text.",
        ))

    if not inv_date or not str(inv_date).strip():
        issues.append(ValidationIssue(
            code="MISSING_INVOICE_DATE",
            severity="warning",
            field="invoice_date",
            message="Document issue date is missing or ambiguous.",
        ))

    if not customer or not str(customer).strip():
        issues.append(ValidationIssue(
            code="MISSING_CUSTOMER_NAME",
            severity="info",
            field="customer_name",
            message="Customer or Billed entity ('Bill To') not explicitly specified.",
        ))

    if subtotal is None and len(line_items) > 0 and total is not None:
        issues.append(ValidationIssue(
            code="MISSING_SUBTOTAL",
            severity="warning",
            field="subtotal",
            message="Subtotal before tax is not explicitly broken down in document.",
        ))

    # ==========================================================================
    # RULE 2: IDENTIFY INCORRECT NUMERIC VALUES
    # ==========================================================================
    def check_float_validity(val: Any, field_name: str) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                issues.append(ValidationIssue(
                    code="INVALID_NUMERIC_VALUE",
                    severity="error",
                    field=field_name,
                    message=f"Field '{field_name}' contains an invalid NaN or Infinite numeric value.",
                ))
                return None
            return f
        except (ValueError, TypeError):
            issues.append(ValidationIssue(
                code="INVALID_NUMERIC_VALUE",
                severity="error",
                field=field_name,
                message=f"Field '{field_name}' contains non-numeric characters: '{val}'.",
            ))
            return None

    sub_f = check_float_validity(subtotal, "subtotal")
    tax_f = check_float_validity(tax, "tax")
    tot_f = check_float_validity(total, "total")

    # Negative value checks
    if tot_f is not None and tot_f < 0:
        issues.append(ValidationIssue(
            code="NEGATIVE_TOTAL",
            severity="error",
            field="total",
            message=f"Extracted Total amount is negative ({_m(tot_f)}). Standard invoices cannot have a negative balance due.",
            details={"value": tot_f},
        ))

    if sub_f is not None and sub_f < 0:
        issues.append(ValidationIssue(
            code="NEGATIVE_SUBTOTAL",
            severity="error",
            field="subtotal",
            message=f"Extracted Subtotal amount is negative ({_m(sub_f)}).",
            details={"value": sub_f},
        ))

    if tax_f is not None and tax_f < 0:
        issues.append(ValidationIssue(
            code="NEGATIVE_TAX",
            severity="error",
            field="tax",
            message=f"Extracted Tax/Surcharge amount is negative ({_m(tax_f)}).",
            details={"value": tax_f},
        ))

    # Zero total check when non-zero line items exist
    if tot_f is not None and tot_f == 0 and len(line_items) > 0:
        has_positive_items = any((i.get("amount") or 0) > 0 for i in line_items)
        if has_positive_items:
            issues.append(ValidationIssue(
                code="ZERO_TOTAL_WITH_ITEMS",
                severity="error",
                field="total",
                message=f"Extracted Total is {_m(0)} despite multiple billable line items being present.",
            ))

    # Line item numeric sanity checks
    for idx, item in enumerate(line_items):
        desc = item.get("description") or f"Item #{idx+1}"
        amt = item.get("amount")
        qty = item.get("quantity")
        rate = item.get("unit_price")

        if amt is not None and amt < 0:
            issues.append(ValidationIssue(
                code="NEGATIVE_LINE_AMOUNT",
                severity="warning",
                field=f"line_items[{idx}].amount",
                message=f"Line item '{desc}' has negative amount ({_m(amt)}).",
            ))
        if rate is not None and rate < 0:
            issues.append(ValidationIssue(
                code="NEGATIVE_UNIT_PRICE",
                severity="warning",
                field=f"line_items[{idx}].unit_price",
                message=f"Line item '{desc}' has negative unit rate ({_m(rate)}).",
            ))
        if qty is not None and qty <= 0:
            issues.append(ValidationIssue(
                code="INVALID_QUANTITY",
                severity="warning",
                field=f"line_items[{idx}].quantity",
                message=f"Line item '{desc}' has zero or negative quantity ({qty}).",
            ))

    # ==========================================================================
    # RULE 3: IDENTIFY DUPLICATE INVOICES
    # ==========================================================================
    if check_duplicates and inv_num:
        try:
            duplicate = find_duplicate_invoice(doc_id, str(inv_num), supplier)
            if duplicate:
                issues.append(ValidationIssue(
                    code="DUPLICATE_INVOICE",
                    severity="error",
                    field="invoice_number",
                    message=(
                        f"Duplicate invoice detected! Invoice #{inv_num} from supplier '{supplier or duplicate.get('supplier_name') or 'same vendor'}' "
                        f"already exists in document record {duplicate.get('document_id')} (file: '{duplicate.get('filename')}')."
                    ),
                    details={"existing_document_id": duplicate.get("document_id"), "existing_filename": duplicate.get("filename")},
                ))
        except Exception as dup_err:
            logger.warning(f"Duplicate invoice check notice: {dup_err}")

    # ==========================================================================
    # RULE 4: INCONSISTENCIES BETWEEN SUBTOTAL, TAX, LINE ITEMS, AND TOTAL AMOUNT
    # ==========================================================================
    sum_line_items = 0.0
    items_with_amount = 0
    for idx, item in enumerate(line_items):
        amt = item.get("amount")
        qty = item.get("quantity")
        rate = item.get("unit_price")

        if amt is not None:
            sum_line_items += float(amt)
            items_with_amount += 1

        # Check internal line item arithmetic: qty * rate == amount
        if qty is not None and rate is not None and amt is not None:
            expected_amt = round(float(qty) * float(rate), 2)
            actual_amt = round(float(amt), 2)
            if abs(expected_amt - actual_amt) > 0.05:
                issues.append(ValidationIssue(
                    code="LINE_ITEM_ARITHMETIC_ERROR",
                    severity="warning",
                    field=f"line_items[{idx}]",
                    message=f"Line item '{item.get('description', f'Item #{idx+1}')}': Qty ({qty}) x Rate ({_m(rate)}) = {_m(expected_amt)}, but stated amount is {_m(actual_amt)}.",
                    details={"expected": expected_amt, "actual": actual_amt, "diff": round(abs(expected_amt - actual_amt), 2)},
                ))

    # 4A. Inconsistency: Line items sum vs Stated Subtotal
    if sub_f is not None and items_with_amount > 0:
        diff_sub = round(abs(sum_line_items - sub_f), 2)
        if diff_sub > 0.05:
            issues.append(ValidationIssue(
                code="LINE_ITEMS_SUBTOTAL_MISMATCH",
                severity="error",
                field="subtotal",
                message=f"Sum of line items ({_m(sum_line_items)}) does not match stated Subtotal ({_m(sub_f)}). Discrepancy: {_m(diff_sub)}.",
                details={"line_items_sum": sum_line_items, "stated_subtotal": sub_f, "diff": diff_sub},
            ))

    # 4B. Inconsistency: Subtotal + Tax vs Stated Total
    if sub_f is not None and tax_f is not None and tot_f is not None:
        expected_total = round(sub_f + tax_f, 2)
        diff_total = round(abs(expected_total - tot_f), 2)
        if diff_total > 0.05:
            issues.append(ValidationIssue(
                code="TOTAL_MISMATCH",
                severity="error",
                field="total",
                message=f"Arithmetic discrepancy: Subtotal ({_m(sub_f)}) + Tax ({_m(tax_f)}) = {_m(expected_total)}, but Total is stated as {_m(tot_f)}. Difference: {_m(diff_total)}.",
                details={"expected": expected_total, "actual": tot_f, "diff": diff_total},
            ))

    # 4C. Inconsistency: Line items sum + Tax vs Stated Total (when Subtotal is missing)
    elif sub_f is None and items_with_amount > 0 and tot_f is not None:
        tax_add = tax_f if tax_f is not None else 0.0
        expected_total = round(sum_line_items + tax_add, 2)
        diff_total = round(abs(expected_total - tot_f), 2)
        if diff_total > 0.05:
            issues.append(ValidationIssue(
                code="LINE_ITEMS_TOTAL_MISMATCH",
                severity="error",
                field="total",
                message=f"Sum of line items ({_m(sum_line_items)}) + Tax ({_m(tax_add)}) = {_m(expected_total)}, but Total is stated as {_m(tot_f)}. Difference: {_m(diff_total)}.",
                details={"expected": expected_total, "actual": tot_f, "diff": diff_total},
            ))

    # ==========================================================================
    # 5. FLAG RECORDS REQUIRING MANUAL REVIEW
    # ==========================================================================
    issue_dicts = [i.to_dict() for i in issues]
    error_issues = [i for i in issues if i.severity == "error"]
    needs_review = len(error_issues) > 0

    review_stage = None
    if needs_review:
        codes = [i.code for i in error_issues]
        if "DUPLICATE_INVOICE" in codes:
            review_stage = "Needs Review (Duplicate Invoice)"
        elif any(c in codes for c in ["TOTAL_MISMATCH", "LINE_ITEMS_SUBTOTAL_MISMATCH", "LINE_ITEMS_TOTAL_MISMATCH"]):
            review_stage = "Needs Review (Arithmetic Inconsistency)"
        elif any(c in codes for c in ["NEGATIVE_TOTAL", "NEGATIVE_SUBTOTAL", "NEGATIVE_TAX", "ZERO_TOTAL_WITH_ITEMS", "INVALID_NUMERIC_VALUE"]):
            review_stage = "Needs Review (Incorrect Numeric Value)"
        elif any(c in codes for c in ["MISSING_INVOICE_NUMBER", "MISSING_SUPPLIER_NAME", "MISSING_TOTAL"]):
            review_stage = "Needs Review (Missing Required Fields)"
        else:
            review_stage = "Needs Review (Validation Flagged)"

    return issue_dicts, needs_review, review_stage

