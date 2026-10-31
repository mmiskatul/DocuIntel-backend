"""Automated Pytest for Task 7: Testing and Evaluation."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.evaluate_benchmark import run_benchmark_evaluation


def test_labeled_dataset_evaluation_metrics():
    """Runs end-to-end benchmark on labeled dataset and verifies production SLA thresholds."""
    report = run_benchmark_evaluation(verbose=True)

    # 1. Extraction Accuracy SLA (Must be >= 90%)
    accuracy = report["overall_extraction_accuracy_percent"]
    assert accuracy >= 90.0, f"Extraction accuracy too low: {accuracy}% (expected >= 90.0%)"

    # 2. Document Success Rate SLA (Must be >= 90%)
    success_rate = report["document_success_rate_percent"]
    assert success_rate >= 90.0, f"Success rate too low: {success_rate}% (expected >= 90.0%)"

    # 3. Failure & Rejection Rate SLA (Must be 100%)
    failure_rate = report["failure_handling_rate_percent"]
    assert failure_rate >= 90.0, f"Failure handling too low: {failure_rate}% (expected >= 90.0%)"

    # 4. Processing Time SLA (Average latency must be under 20 seconds per document)
    avg_latency = report["processing_time"]["average_seconds"]
    assert avg_latency < 20.0, f"Processing time too high: {avg_latency}s (expected < 20.0s)"

    # 5. Field-specific checks
    field_accs = report["field_accuracies_percent"]
    assert field_accs["invoice_number"] >= 90.0
    assert field_accs["total"] >= 90.0
    assert field_accs["supplier_name"] >= 90.0
