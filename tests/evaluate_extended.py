"""Extended evaluation (Task 7): runs a labelled dataset through the real pipeline and reports measured metrics.

    python tests/evaluate_extended.py                      # the full dataset, results printed
    python tests/evaluate_extended.py --out results.json --md tables.md
    python tests/evaluate_extended.py --category scanned_image --limit 3

It needs DATABASE_URL and OPENAI_API_KEY (the real database and the real model are used; nothing is mocked). Every document it creates
is deleted afterwards, also when the run fails half way. See eval_dataset.py for what the dataset contains and eval_scoring.py for the
exact scoring rules.
"""
import argparse
import json
import os
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.document_repo import delete_document, get_document, save_document      # noqa: E402
from app.models.result_repo import get_extraction_results                                # noqa: E402
from app.services.pipeline import run_document_pipeline                                  # noqa: E402
from app.services.vision_ocr import get_ocr_meta                                         # noqa: E402
from tests.eval_dataset import FIELDS, build_dataset                                     # noqa: E402
from tests.eval_scoring import HEADER_FIELDS, hallucinated_fields, pair_invoices, percentile, prf, score_header, score_line_items   # noqa: E402

VALID = ("completed", "needs_review")      # statuses a real invoice may end in
REFUSED = ("rejected", "failed")


def _actual_invoice(row: Dict[str, Any]) -> Dict[str, Any]:
    data = row.get("data") or {}
    inv = {f: (row.get(f) if row.get(f) not in (None, "") else data.get(f)) for f in FIELDS}
    inv["line_items"] = data.get("line_items") or []
    inv["codes"] = [i.get("code") for i in (data.get("issues") or row.get("validation_errors") or []) if isinstance(i, dict)]
    return inv


def score_case(case: Dict[str, Any], doc: Dict[str, Any], rows: List[Dict[str, Any]], seconds: float, ocr: Dict[str, Any], crashed: str = "") -> Dict[str, Any]:
    """Compares what the system stored with the labels for one document. Pure (no I/O), so it can be tested."""
    expected_status = case["expected_status"]
    actual_status = (doc or {}).get("status", "missing")
    actual = [_actual_invoice(r) for r in rows]
    rec: Dict[str, Any] = {
        "id": case["id"], "category": case["category"], "quality": case.get("quality"), "filename": case["filename"], "note": case.get("note", ""),
        "expected_status": expected_status, "actual_status": actual_status, "status_correct": actual_status == expected_status,
        "seconds": round(seconds, 2), "crashed": crashed, "error": ((doc or {}).get("error") or "")[:160],
        "ocr_engine": ocr.get("engine"), "ocr_confidence": ocr.get("confidence"),
        "expected_invoices": len(case["invoices"]), "actual_invoices": len(actual),
        "wrong_fields": [], "invented_fields": [], "field_checks": 0, "field_correct": 0,
        "items_matched": 0, "items_expected": 0, "items_actual": 0, "fully_correct": None, "silent_error": False,
        "codes": sorted({c for a in actual for c in a["codes"] if c}),
    }
    if expected_status in VALID:
        rec["processed_ok"] = actual_status in VALID
        for e, a in pair_invoices(case["invoices"], actual):
            a = a or {}
            checks = score_header(e, a)
            rec["field_checks"] += len(checks)
            rec["field_correct"] += sum(checks.values())
            rec["wrong_fields"] += [f"{e.get('invoice_number') or '?'}:{f}" for f, ok in checks.items() if not ok]
            rec["invented_fields"] += hallucinated_fields(e, a)
            if e.get("line_items"):
                m, en, an = score_line_items(e["line_items"], a.get("line_items") or [])
                rec["items_matched"] += m
                rec["items_expected"] += en
                rec["items_actual"] += an
        extra = max(0, len(actual) - len(case["invoices"]))
        rec["extra_invoices"] = extra
        perfect_items = rec["items_matched"] == rec["items_expected"] and (rec["items_actual"] == rec["items_expected"] or rec["items_expected"] == 0)
        rec["fully_correct"] = bool(rec["field_checks"] and rec["field_correct"] == rec["field_checks"] and perfect_items and not extra and not rec["invented_fields"])
        rec["silent_error"] = actual_status == "completed" and not rec["fully_correct"]
        if case["expect_codes"]:
            rec["expected_flag_found"] = any(c in rec["codes"] for c in case["expect_codes"])
    else:
        rec["handled"] = actual_status in REFUSED and not crashed
        rec["has_reason"] = bool(rec["error"])
    return rec


def run_case(case: Dict[str, Any], created: List[str]) -> Dict[str, Any]:
    doc_id = str(uuid.uuid4())
    created.append(doc_id)
    save_document({"id": doc_id, "filename": case["filename"], "file_type": case["mime"], "size_bytes": len(case["bytes"]), "status": "queued", "stage": "Uploaded"})
    crashed = ""
    t0 = time.perf_counter()
    try:
        run_document_pipeline(doc_id, case["filename"], case["bytes"], case["mime"])
    except Exception as exc:                                             # the pipeline should never raise; if it does, that is a finding
        crashed = f"{type(exc).__name__}: {exc}"[:200]
    seconds = time.perf_counter() - t0
    ocr = get_ocr_meta()
    return score_case(case, get_document(doc_id) or {}, get_extraction_results(doc_id), seconds, ocr, crashed)


def _pct(n: float, d: float) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [r for r in records if r["expected_status"] in VALID]
    refused = [r for r in records if r["expected_status"] in REFUSED]
    checks = sum(r["field_checks"] for r in valid)
    correct = sum(r["field_correct"] for r in valid)
    m, en, an = (sum(r[k] for r in valid) for k in ("items_matched", "items_expected", "items_actual"))
    p, rc, f1 = prf(m, en, an)
    n_invoices = sum(r["expected_invoices"] for r in valid)
    wrong_by_field = Counter(w.rsplit(":", 1)[1] for r in valid for w in r["wrong_fields"])
    per_field = {f: _pct(n_invoices - wrong_by_field.get(f, 0), n_invoices) for f in HEADER_FIELDS}
    times = [r["seconds"] for r in records if not r["crashed"]]
    nr_expected = [r for r in valid if r["expected_status"] == "needs_review"]
    ok_expected = [r for r in valid if r["expected_status"] == "completed"]
    tp = sum(1 for r in nr_expected if r["actual_status"] == "needs_review")
    fn = sum(1 for r in nr_expected if r["actual_status"] == "completed")
    fp = sum(1 for r in ok_expected if r["actual_status"] == "needs_review")
    completed = [r for r in valid if r["actual_status"] == "completed"]
    silent = [r for r in completed if r["silent_error"]]
    invented = sum(len(r["invented_fields"]) for r in valid)
    missing_truths = sum(1 for r in valid if r["category"] == "missing_field")
    cats: Dict[str, Dict[str, Any]] = {}
    for cat in sorted({r["category"] for r in records}):
        rs = [r for r in records if r["category"] == cat]
        v = [r for r in rs if r["expected_status"] in VALID]
        cats[cat] = {"documents": len(rs), "status_correct": sum(r["status_correct"] for r in rs),
                     "field_accuracy": _pct(sum(r["field_correct"] for r in v), sum(r["field_checks"] for r in v)) if v else None,
                     "perfect": f"{sum(1 for r in v if r['fully_correct'])}/{len(v)}" if v else None,
                     "mean_seconds": round(statistics.mean(r["seconds"] for r in rs), 1)}
    img = [r for r in records if r["category"] in ("scanned_image", "scanned_pdf")]
    return {
        "documents": len(records), "valid_documents": len(valid), "refused_documents": len(refused),
        "pipeline_crashes": sum(1 for r in records if r["crashed"]),
        "success_rate": {"processed_ok": sum(1 for r in valid if r["processed_ok"]), "of": len(valid), "percent": _pct(sum(1 for r in valid if r["processed_ok"]), len(valid))},
        "failure_handling": {"handled": sum(1 for r in refused if r["handled"]), "of": len(refused), "percent": _pct(sum(1 for r in refused if r["handled"]), len(refused)),
                             "with_reason": sum(1 for r in refused if r["has_reason"])},
        "status_exactly_as_expected": {"n": sum(r["status_correct"] for r in records), "of": len(records), "percent": _pct(sum(r["status_correct"] for r in records), len(records))},
        "header_field_accuracy": {"correct": correct, "of": checks, "percent": _pct(correct, checks)},
        "perfect_documents": {"n": sum(1 for r in valid if r["fully_correct"]), "of": len(valid), "percent": _pct(sum(1 for r in valid if r["fully_correct"]), len(valid))},
        "per_field_accuracy": per_field,
        "line_items": {"matched": m, "expected": en, "returned": an, "precision": round(p * 100, 1), "recall": round(rc * 100, 1), "f1": round(f1 * 100, 1)},
        "review_flagging": {"flagged_correctly": tp, "missed": fn, "flagged_needlessly": fp, "expected_flagged": len(nr_expected), "expected_clean": len(ok_expected),
                            "recall": _pct(tp, tp + fn), "precision": _pct(tp, tp + fp)},
        "silent_errors": {"completed_documents": len(completed), "wrong_but_unflagged": len(silent), "percent_of_completed": _pct(len(silent), len(completed)), "ids": [r["id"] for r in silent]},
        "invented_values": {"in_truly_missing_fields": invented, "documents_with_a_missing_field": missing_truths},
        "time_seconds": {"mean": round(statistics.mean(times), 1), "median": round(statistics.median(times), 1), "p95": round(percentile(times, 95), 1), "max": round(max(times), 1), "total": round(sum(times), 1)},
        "ocr": {"documents": len(img), "engines": dict(Counter(r["ocr_engine"] or "n/a" for r in img)),
                "mean_local_confidence": round(statistics.mean([r["ocr_confidence"] for r in img if r["ocr_confidence"] is not None] or [0]), 2)},
        "by_category": cats,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None, help="only the first N documents (after --category)")
    ap.add_argument("--category", default=None)
    ap.add_argument("--out", default=None, help="write the full results as JSON")
    ap.add_argument("--md", default=None, help="write the summary tables as markdown")
    args = ap.parse_args()

    kwargs = {"run_tag": uuid.uuid4().hex[:4].upper()}
    if args.seed is not None:
        kwargs["seed"] = args.seed
    cases = build_dataset(**kwargs)
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[: args.limit]

    created: List[str] = []
    records: List[Dict[str, Any]] = []
    print(f"Running {len(cases)} documents through the real pipeline...\n")
    try:
        for i, case in enumerate(cases, 1):
            rec = run_case(case, created)
            records.append(rec)
            flag = "ok " if rec.get("fully_correct") or rec.get("handled") else "!! "
            print(f"{flag}[{i:>2}/{len(cases)}] {case['id']:<8} {case['category']:<16} expected {case['expected_status']:<12} got {rec['actual_status']:<12} {rec['seconds']:>5.1f}s"
                  + (f"  wrong: {','.join(rec['wrong_fields'][:4])}" if rec["wrong_fields"] else "") + (f"  CRASH {rec['crashed']}" if rec["crashed"] else ""))
    finally:
        for doc_id in created:                                                # always clean up, even after a crash or Ctrl+C
            try:
                delete_document(doc_id)
            except Exception as exc:
                print(f"could not delete {doc_id}: {exc}")
    if not records:
        return 1
    summary = summarize(records)
    print("\n" + json.dumps(summary, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "documents": records}, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
