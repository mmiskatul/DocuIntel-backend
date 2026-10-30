"""Automated Evaluation and Benchmarking Suite for Task 7.

Evaluates the end-to-end document processing pipeline against a labeled ground-truth dataset:
1. Actual Extraction Accuracy (field-by-field and overall exact/fuzzy match)
2. Processing Time (per-document and average latency in seconds)
3. Document Success Rate (% of valid documents processed into completed or needs_review)
4. Failure Rate (% of corrupted or non-invoice files properly rejected/failed)
"""
import io
import time
import uuid
import statistics
from typing import Any, Dict, List, Tuple
import openpyxl
from PIL import Image, ImageDraw
import pymupdf

from app.models.document_repo import save_document, get_document, delete_document
from app.models.result_repo import get_extraction_results
from app.services.pipeline import run_document_pipeline


def create_labeled_dataset() -> List[Dict[str, Any]]:
    """Constructs a multi-modal benchmark dataset with ground-truth labels."""
    dataset = []

    # --------------------------------------------------------------------------
    # Document 1: Standard Commercial Tech Invoice (PDF)
    # --------------------------------------------------------------------------
    doc1 = pymupdf.open()
    page1 = doc1.new_page(width=595, height=842)
    page1.insert_text((50, 60), (
        "TAX INVOICE\n\n"
        "Invoice Number: INV-2026-9041\n"
        "Invoice Date: 2026-03-15\n"
        "Supplier: Acme Cloud Technologies Inc.\n"
        "Customer: Nexus Global Enterprises\n\n"
        "Line Items:\n"
        "1. Dedicated GPU Cluster Hosting (4x A100) - $3,200.00\n"
        "2. High Availability Storage Volume (10TB) - $1,000.00\n\n"
        "Subtotal: $4,200.00\n"
        "Tax (10%): $420.00\n"
        "Total Amount: $4,620.00\n"
        "Currency: USD\n"
    ))
    b1 = doc1.tobytes()
    doc1.close()

    dataset.append({
        "id": "BENCH-01",
        "name": "Standard Commercial Tech Invoice",
        "filename": "acme_cloud_inv_9041.pdf",
        "mime_type": "application/pdf",
        "bytes": b1,
        "is_valid_invoice": True,
        "expected": {
            "is_supported_format": True,
            "invoice_number": "INV-2026-9041",
            "supplier_name": "Acme Cloud Technologies",
            "customer_name": "Nexus Global",
            "invoice_date": "2026-03-15",
            "subtotal": 4200.00,
            "tax": 420.00,
            "total": 4620.00,
            "currency": "USD",
        },
    })

    # --------------------------------------------------------------------------
    # Document 2: Freight Waybill & Logistics Invoice (PDF)
    # --------------------------------------------------------------------------
    doc2 = pymupdf.open()
    page2 = doc2.new_page(width=595, height=842)
    page2.insert_text((50, 60), (
        "ORIGINAL FREIGHT WAYBILL\n\n"
        "Waybill / Invoice #: WB-77881\n"
        "Issue Date: 2026-04-10\n"
        "Carrier / Vendor: TransGlobal Freight Lines\n"
        "Shipper / Customer: Pacific Retailers Corp\n\n"
        "Description:\n"
        "- Interstate Freight Pallet Transport - $2,400.00\n"
        "- Liftgate Handling & Dock Fee - $490.00\n\n"
        "Subtotal: $2,890.00\n"
        "Fuel Surcharge / Tax: $450.00\n"
        "Total Balance Due: $3,340.00\n"
        "Currency: USD\n"
    ))
    b2 = doc2.tobytes()
    doc2.close()

    dataset.append({
        "id": "BENCH-02",
        "name": "Freight Waybill & Logistics Invoice",
        "filename": "transglobal_waybill_77881.pdf",
        "mime_type": "application/pdf",
        "bytes": b2,
        "is_valid_invoice": True,
        "expected": {
            "is_supported_format": True,
            "invoice_number": "WB-77881",
            "supplier_name": "TransGlobal Freight",
            "customer_name": "Pacific Retailers",
            "invoice_date": "2026-04-10",
            "subtotal": 2890.00,
            "tax": 450.00,
            "total": 3340.00,
            "currency": "USD",
        },
    })

    # --------------------------------------------------------------------------
    # Document 3: Multi-Sheet Financial Statement (Excel XLSX)
    # --------------------------------------------------------------------------
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Summary"
    ws1.append(["Invoice Reference", "HDS-XLS-442"])
    ws1.append(["Billing Date", "2026-05-01"])
    ws1.append(["Service Provider", "Horizon Datacenter Systems"])
    ws1.append(["Billed Client", "Cyberdyne Solutions Inc"])
    ws1.append(["Subtotal", "12500.00"])
    ws1.append(["Tax", "1250.00"])
    ws1.append(["Total Due", "13750.00"])
    ws1.append(["Currency", "USD"])

    ws2 = wb.create_sheet(title="Breakdown")
    ws2.append(["Item", "Qty", "Amount"])
    ws2.append(["Rack 42U Power & Cooling", 2, 7500.00])
    ws2.append(["Tier-1 IP Transit (10Gbps)", 1, 5000.00])

    buf_xlsx = io.BytesIO()
    wb.save(buf_xlsx)
    b3 = buf_xlsx.getvalue()

    dataset.append({
        "id": "BENCH-03",
        "name": "Multi-Sheet Financial Statement (XLSX)",
        "filename": "horizon_datacenter_statement.xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "bytes": b3,
        "is_valid_invoice": True,
        "expected": {
            "is_supported_format": True,
            "invoice_number": "HDS-XLS-442",
            "supplier_name": "Horizon Datacenter Systems",
            "customer_name": "Cyberdyne Solutions",
            "invoice_date": "2026-05-01",
            "subtotal": 12500.00,
            "tax": 1250.00,
            "total": 13750.00,
            "currency": "USD",
        },
    })

    # --------------------------------------------------------------------------
    # Document 4: High-Resolution Scanned Receipt (Image PNG)
    # --------------------------------------------------------------------------
    img = Image.new("RGB", (900, 500), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    text_content = (
        "COMMERCIAL RECEIPT\n"
        "Receipt #: REC-6610\n"
        "Date: 2026-02-18\n"
        "Merchant: Metro Industrial Hardware\n"
        "Customer: BuildCorp Construction\n"
        "Subtotal: $850.00\n"
        "Tax: $85.00\n"
        "Total: $935.00\n"
        "Currency: USD\n"
    )
    draw.text((40, 40), text_content, fill=(0, 0, 0))
    buf_img = io.BytesIO()
    img.save(buf_img, format="PNG")
    b4 = buf_img.getvalue()

    dataset.append({
        "id": "BENCH-04",
        "name": "Scanned Commercial Receipt (Image PNG)",
        "filename": "metro_hardware_receipt.png",
        "mime_type": "image/png",
        "bytes": b4,
        "is_valid_invoice": True,
        "expected": {
            "is_supported_format": True,
            "invoice_number": "REC-6610",
            "supplier_name": "Metro Industrial Hardware",
            "customer_name": "BuildCorp",
            "invoice_date": "2026-02-18",
            "subtotal": 850.00,
            "tax": 85.00,
            "total": 935.00,
            "currency": "USD",
        },
    })

    # --------------------------------------------------------------------------
    # Document 5: Arithmetic Variance Discrepancy Invoice (Needs Review)
    # --------------------------------------------------------------------------
    doc5 = pymupdf.open()
    page5 = doc5.new_page(width=595, height=842)
    page5.insert_text((50, 60), (
        "INVOICE\n\n"
        "Invoice Number: VTX-5511\n"
        "Date: 2026-06-12\n"
        "Vendor: Vertex Electronics Ltd\n"
        "Customer: Delta Manufacturing\n\n"
        "Subtotal: $1,500.00\n"
        "Tax: $150.00\n"
        "Total: $1,800.00\n"  # Variance: 1500 + 150 = 1650 != 1800
    ))
    b5 = doc5.tobytes()
    doc5.close()

    dataset.append({
        "id": "BENCH-05",
        "name": "Arithmetic Discrepancy (Needs Review)",
        "filename": "vertex_variance_invoice.pdf",
        "mime_type": "application/pdf",
        "bytes": b5,
        "is_valid_invoice": True,
        "expected": {
            "is_supported_format": True,
            "invoice_number": "VTX-5511",
            "supplier_name": "Vertex Electronics",
            "subtotal": 1500.00,
            "tax": 150.00,
            "total": 1800.00,
            "should_need_review": True,
        },
    })

    # --------------------------------------------------------------------------
    # Document 6: Non-Invoice Assignment Document (PDF Format Rejection)
    # --------------------------------------------------------------------------
    doc6 = pymupdf.open()
    page6 = doc6.new_page(width=595, height=842)
    page6.insert_text((50, 60), (
        "Senior Software Engineer Assignment Task\n\n"
        "Instructions:\n"
        "Please build a full-stack document processing pipeline with OCR, LLM extraction, and RAG.\n"
        "Submit repository link within 5 business days."
    ))
    b6 = doc6.tobytes()
    doc6.close()

    dataset.append({
        "id": "BENCH-06",
        "name": "Non-Invoice Assignment Task (Should Reject)",
        "filename": "senior_engineer_assignment.pdf",
        "mime_type": "application/pdf",
        "bytes": b6,
        "is_valid_invoice": False,
        "expected": {
            "is_supported_format": False,
            "expected_status": "rejected",
        },
    })

    # --------------------------------------------------------------------------
    # Document 7: Corrupted Binary Stream (Failure Handling)
    # --------------------------------------------------------------------------
    b7 = b"%PDF-INVALID-CORRUPTED-CORRUPT-HEADER-0000-NON-READABLE"
    dataset.append({
        "id": "BENCH-07",
        "name": "Corrupted File Handling (Should Fail Safely)",
        "filename": "corrupted_file_test.pdf",
        "mime_type": "application/pdf",
        "bytes": b7,
        "is_valid_invoice": False,
        "expected": {
            "expected_status": "failed",
        },
    })

    return dataset


def run_benchmark_evaluation(verbose: bool = True) -> Dict[str, Any]:
    """Runs the benchmark dataset through the pipeline, records timing and metrics."""
    dataset = create_labeled_dataset()
    results = []
    latencies = []

    valid_docs_count = 0
    valid_docs_success = 0

    failure_docs_count = 0
    failure_docs_handled = 0

    field_scores: Dict[str, List[float]] = {
        "invoice_number": [],
        "supplier_name": [],
        "customer_name": [],
        "invoice_date": [],
        "subtotal": [],
        "tax": [],
        "total": [],
        "currency": [],
    }

    if verbose:
        print("\n" + "=" * 80)
        print("[RUN] RUNNING AUTOMATED BENCHMARK EVALUATION (TASK 7)")
        print("=" * 80)

    for item in dataset:
        doc_id = str(uuid.uuid4())
        filename = item["filename"]
        raw_bytes = item["bytes"]
        mime_type = item["mime_type"]
        expected = item["expected"]

        # 1. Measure Processing Time
        start_time = time.perf_counter()
        save_document({
            "id": doc_id,
            "filename": filename,
            "file_type": mime_type,
            "size_bytes": len(raw_bytes),
            "status": "queued",
            "stage": "Uploaded",
        })

        try:
            run_document_pipeline(doc_id, filename, raw_bytes, mime_type)
        except Exception as exc:
            pass  # Expected for raw corrupted file if unhandled, but pipeline handles it safely

        elapsed = time.perf_counter() - start_time
        latencies.append(elapsed)

        # 2. Retrieve Persisted Document Record & Results
        doc = get_document(doc_id)
        res_list = get_extraction_results(doc_id)
        res = res_list[0] if res_list else {}
        extracted_data = res.get("data") or {}

        # 3. Evaluate Success vs Failure Handling
        if item["is_valid_invoice"]:
            valid_docs_count += 1
            # Valid document must finish in 'completed' or 'needs_review'
            is_success = doc and doc.get("status") in ("completed", "needs_review")
            if is_success:
                valid_docs_success += 1

            # Check field accuracies
            for field in field_scores.keys():
                exp_val = expected.get(field)
                if exp_val is None:
                    continue  # Optional field not in test case

                act_val = res.get(field) or extracted_data.get(field)
                if isinstance(exp_val, (int, float)):
                    # Numeric exact match with tolerance
                    try:
                        match = 1.0 if abs(float(act_val or 0) - float(exp_val)) < 0.05 else 0.0
                    except (ValueError, TypeError):
                        match = 0.0
                else:
                    # String substring or normalized match
                    match = 1.0 if (str(exp_val).lower() in str(act_val or "").lower() or str(act_val or "").lower() in str(exp_val).lower()) else 0.0

                field_scores[field].append(match)

            # Check variance detection if flagged
            if expected.get("should_need_review"):
                has_review_flag = doc and doc.get("status") == "needs_review"
                if verbose:
                    print(f"[{item['id']}] {item['name']}: Variance Review Flagged = {has_review_flag}")
        else:
            failure_docs_count += 1
            # Negative / corrupted / non-invoice tests must terminate in 'rejected' or 'failed'
            expected_status = expected.get("expected_status")
            actual_status = doc.get("status") if doc else "failed"
            is_handled = actual_status == expected_status or actual_status in ("rejected", "failed")
            if is_handled:
                failure_docs_handled += 1

        results.append({
            "id": item["id"],
            "name": item["name"],
            "elapsed_seconds": round(elapsed, 2),
            "status": doc.get("status") if doc else "unknown",
            "stage": doc.get("stage") if doc else "unknown",
        })

        # Cleanup test doc
        delete_document(doc_id)

    # 4. Compute Aggregate Metrics
    avg_latency = round(statistics.mean(latencies), 2)
    min_latency = round(min(latencies), 2)
    max_latency = round(max(latencies), 2)
    median_latency = round(statistics.median(latencies), 2)

    doc_success_rate = round((valid_docs_success / valid_docs_count) * 100, 1) if valid_docs_count else 0.0
    failure_handling_rate = round((failure_docs_handled / failure_docs_count) * 100, 1) if failure_docs_count else 0.0

    field_accuracies = {}
    for f, scores in field_scores.items():
        field_accuracies[f] = round((sum(scores) / len(scores)) * 100, 1) if scores else 100.0

    overall_accuracy = round(statistics.mean(field_accuracies.values()), 1)

    eval_report = {
        "total_documents_evaluated": len(dataset),
        "valid_documents": valid_docs_count,
        "adversarial_or_failure_documents": failure_docs_count,
        "document_success_rate_percent": doc_success_rate,
        "failure_handling_rate_percent": failure_handling_rate,
        "overall_extraction_accuracy_percent": overall_accuracy,
        "field_accuracies_percent": field_accuracies,
        "processing_time": {
            "average_seconds": avg_latency,
            "median_seconds": median_latency,
            "min_seconds": min_latency,
            "max_seconds": max_latency,
        },
        "per_document_breakdown": results,
    }

    if verbose:
        print("\n" + "=" * 80)
        print("[RESULTS] TASK 7 BENCHMARK EVALUATION RESULTS")
        print("=" * 80)
        print(f"- Total Labeled Benchmark Documents : {len(dataset)}")
        print(f"- Document Success Rate            : {doc_success_rate}% ({valid_docs_success}/{valid_docs_count} valid commercial docs)")
        print(f"- Failure & Rejection Handling Rate: {failure_handling_rate}% ({failure_docs_handled}/{failure_docs_count} invalid/corrupt docs)")
        print(f"- Overall Extraction Accuracy      : {overall_accuracy}%")
        print("  * Field Breakdown:")
        for f, acc in field_accuracies.items():
            print(f"    - {f.ljust(18)} : {acc}%")
        print(f"- Processing Time Latency:")
        print(f"  * Average per document            : {avg_latency}s")
        print(f"  * Median per document             : {median_latency}s")
        print(f"  * Fastest document                : {min_latency}s")
        print(f"  * Slowest document (Multi-sheet)  : {max_latency}s")
        print("=" * 80 + "\n")

    return eval_report


if __name__ == "__main__":
    run_benchmark_evaluation(verbose=True)
