# DocuIntel — Evaluation Report (Task 7)

All figures below were **measured by running the code** on 2026-10-30/31 against the live stack
(OpenAI `gpt-4o-mini`, Supabase PostgreSQL in EU, run from a developer laptop). Nothing is estimated.
> Tests and results were last run on **2026-10-31**. The labelled benchmark in section 2 was measured on 2026-10-30/31; it was run again inside the
> test suite on 2026-10-31 and met its pass thresholds, but the detailed table below is from the earlier measurement and has not been re-measured since the later changes.

## 1. Automated tests and test results

**Backend:** 118 tests. The complete suite was run last on 2026-10-31 after the date guard and the evaluation tests were added: **118 passed, 0 failed, 0 skipped** in 12 min 4 s (JUnit report; no other backend was running on the database).
The 97 tests that call the real database and OpenAI existed earlier that day, and 21 more (`test_eval_scoring.py` 15, `test_date_check.py` 6) need neither. Earlier runs of the 97:
- First full run: **94 passed, 0 failed, 0 skipped** in 11 min 40 s (94 tests existed then).
- Final full run, after 3 more tests were added for a bug found in the first one: **96 passed, 1 failed** in 12 min 31 s. The failure was
  `test_recent_or_owned_jobs_are_not_stolen`: the test plants abandoned-looking jobs on purpose, and a **second backend that was running against the same
  database** (the developer's own server) did what it is designed to do and recovered them. The same test **passes when run on its own** (run twice, 25 s and 28 s).
  The 96 other tests passed. **To get a fully clean run, stop any other backend that uses the same database first.**

**Frontend:** `npm test` → **41 passed, 0 failed** (Vitest, about one second; no backend needed).

*Bug found by the first full run's leftovers:* a progress update from a still-running worker could re-create a document that had just been deleted (a ghost
"document.pdf", 0 bytes). It is fixed in `save_document` (an update may no longer create a row) and covered by `test_deleted_while_processing`.

| Backend test file | Tests | Covers |
|---|---:|---|
| `test_rest_apis` | 6 | REST endpoints: upload, status, results, corrections, approval, reprocessing, deletion |
| `test_queue_security_scale` | 11 | Upload limits, API-key authentication, durable queue, extraction failure and LLM-outage behaviour |
| `test_local_ocr_and_recovery` | 8 | Local OCR and its routing to the vision model, glued-word repair, crash recovery (three concurrent sweeps never claim a job twice) |
| `test_robustness` | 7 | Corrupt, empty and unsupported files, malformed ids, a 1,000-page PDF |
| `test_multi_invoice` | 4 | Splitting a PDF into invoices, per-invoice correction, ledgers |
| `test_cross_verification` | 9 | PDF-vs-PDF and PDF-vs-Excel comparison, tolerance, review flags, duplicate handling |
| `test_validation_rules`, `test_dynamic_reasons`, `test_validation_currency` | 12 | Validation rules, rejection reasons, messages in the invoice's own currency |
| `test_database_and_search`, `test_search_and_payloads` | 9 | Schema, pgvector search, hybrid search, slim list payloads |
| `test_document_filters` | 16 | Supplier, invoice-number, date, total and needs-review filters (all applied to the same invoice), and rejection of bad input |
| `test_cloud_storage`, `test_page_preview`, `test_db_pool_wait` | 10 | Original files kept and deleted on approval, page images, waiting for a free database connection |
| `test_deleted_while_processing` | 3 | A late progress update can no longer bring a deleted document back |
| `test_pipeline`, `test_evaluation` | 2 | The pipeline end to end, and the labelled benchmark (accuracy, success rate, failure handling and time must meet thresholds) |

The suite was run against the live database. Tests create their own documents and delete only those; none of them deletes other documents. Two caveats: the job-recovery tests assume no other backend is using the same database (see above), and a second server that is still running old code can re-create a document a test has just deleted.

| Frontend test file | Tests | Covers |
|---|---:|---|
| `store.test.ts` | 21 | Actions that update the screen at once and are undone when the server refuses, a stale answer not overwriting a newer one, delete with its overlay, uploads, the document cache, saved preferences, notifications |
| `search_verify_confirm.test.ts` | 10 | Searches that start from a clean page, comparing without changing documents, marking for review, the confirmation popup |
| `format.test.ts` | 10 | Money, sizes, durations, file kinds, field parsing, readable error messages |

## 2. Extended evaluation (`tests/evaluate_extended.py`) — 51 documents, measured 2026-10-31

Run against the real pipeline, the real database and the real model (nothing mocked); every document it creates is deleted afterwards. The dataset
(`tests/eval_dataset.py`) is generated in code with known answers: **51 files, 57 invoices, 164 line items, 12 categories**, including clean and degraded
scans (JPEG compression, blur, noise, rotation, fading, a bad scan), EU number formats (`2.390,52 €`), multi-invoice bundles, a ledger, spreadsheets, wrong
arithmetic, missing fields, a duplicate, non-invoices and broken files. The scoring rules are written at the top of `tests/eval_scoring.py` and are unit
tested (`test_eval_scoring.py`). Raw per-document results of the final run: `tests/eval_results.json`. Reproduce with `python tests/evaluate_extended.py --out results.json`.

The evaluation was run **twice**: run 1 before the date-order guard (`app/services/date_check.py`) was added, run 2 after it. Run 2 is the current system.

| Metric | Run 1 (before date guard) | **Run 2 (current)** |
|---|---:|---:|
| Valid documents processed to `completed` / `needs_review` | 100 % (43/43) | **100 % (43/43)**, 0 pipeline crashes |
| Broken or non-invoice files refused with a reason | 87.5 % (7/8) | **87.5 % (7/8)** |
| Final status exactly as labelled | 74.5 % (38/51) | **72.5 % (37/51)** |
| Header fields correct (8 fields x every invoice) | 96.5 % (440/456) | **96.7 % (441/456)** |
| Documents with every field and every line item correct | 74.4 % (32/43) | **72.1 % (31/43)** |
| Line items precision / recall / F1 | 93.1 / 90.2 / 91.6 % (148 of 164) | **87.4 / 84.8 / 86.1 %** (139 of 164) |
| Wrong values stored as `completed` with no flag (silent errors) | 2 of 29 (EV-D04, EV-D07) | **2 of 28 (7.1 %)** (EV-I01, EV-I03) |
| Values invented for a field that is not on the page | 0 | **0** (3 documents had one missing) |
| Time per document | mean 11.4 s, p95 17.1 s | **mean 11.3 s**, median 11.6 s, p95 16.8 s, max 18.6 s |

Run 2 per-field accuracy: supplier 100 %, customer 98.2 %, currency 98.2 %, invoice date 96.5 %, total 96.5 %, invoice number / subtotal / tax 94.7 % each.

| Category (run 2) | Docs | Status as labelled | Header field accuracy | Perfect |
|---|---:|---:|---:|---:|
| digital_pdf | 12 | 11/12 | 99.0 % | 11/12 |
| multi_page_pdf | 3 | 3/3 | 100 % | 3/3 |
| bundle_pdf (2-3 invoices per file) | 2 | 2/2 | 100 % | 2/2 |
| ledger | 2 | 2/2 | 100 % | 2/2 |
| spreadsheet | 3 | 2/3 | 87.5 % | 2/3 |
| scanned_image | 10 | 4/10 | 87.5 % | 2/10 |
| scanned_pdf | 2 | 0/2 | 93.8 % | 0/2 |
| wrong_arithmetic | 4 | 3/4 | 100 % | 4/4 |
| missing_field | 3 | 1/3 | 100 % | 3/3 |
| duplicate | 2 | 2/2 | 100 % | 2/2 |
| not_an_invoice | 4 | 4/4 | - | - |
| corrupt_or_empty | 4 | 3/4 | - | - |

### What the date guard changed (run 1 to run 2)

Run 1 found two silent errors, EV-D04 and EV-D07: digital PDFs with dates printed `10/01/2026` and `08/01/2026` (day first) were stored with a different
date, marked `completed`, and not flagged. The guard looks at the invoice's own text. If another date in the document can only be read one way, or the
numbers use European formatting, the order is settled and a wrong date is corrected; otherwise the invoice is kept but flagged `AMBIGUOUS_DATE` for review.
In run 2 EV-D07 (European number format) is read correctly and EV-D04 (a US-style invoice that is truly ambiguous) is sent to review with the reason shown.
Both are now handled; 6 unit tests cover the rule (`test_date_check.py`).

### What went wrong in run 2, document by document

- **Scanned line items are the main weakness.** Header fields on scans are mostly right, but the table is often misread: the local OCR returns a table row
  with its columns shifted or merged, and the model then puts numbers in the wrong fields (for example the line total in `unit_price` and the quantity empty).
  Two of the 12 scans (EV-I01, a clean scan, and EV-I03, a JPEG-compressed one) came back `completed` with wrong line items and no flag. Both documents' header
  values were correct, so the totals still matched, which is why the validator did not object. When reproduced, EV-I01 and EV-I08 each returned 0 of 4 correct
  items. The same scans scored differently in run 1 (EV-I01 4/4, EV-I08 4/4), so scan line items also **vary from run to run**; the two runs differ only in the
  date guard, which does not touch line items.
- **Scans that were flagged.** Eight of the 12 scans ended in `needs_review` where the label expected `completed`, because the arithmetic check noticed the
  line items did not add up (EV-I05, I07, I08, P01 and others). These flags are mostly justified; the effect is that most degraded scans need a human look.
  The heavy blur (EV-I06) lost the invoice number, date, customer and totals and was flagged with five issue codes, including low OCR quality. Local OCR handled
  9 scans (mean confidence 0.82) and the vision model 3 whose local confidence was under 0.80.
- **Flagged needlessly: 10 of the 35 documents labelled clean** (review-flag precision 33.3 %), nearly all scans. The flag errs on the side of caution.
- **CSV invoice with a header block (EV-S03).** A CSV laid out like an invoice (key/value lines at the top, then an item table) was read with 0 of its 5 items
  and no totals. It was flagged (`MISSING_TOTAL`, `needs_review`), so it was not silent, but the parser does not handle this layout.
- **Wrong arithmetic and missing fields: flagged, but the status stays `completed`.** EV-X03 (one line amount wrong), EV-N01 (no date) and EV-N02 (no customer)
  were labelled `needs_review` but ended `completed`. The correct issue was raised on each (`LINE_ITEM_ARITHMETIC_ERROR`, `MISSING_INVOICE_DATE`,
  `MISSING_CUSTOMER_NAME`); those issues are warnings or information in the validator and do not change the status. The 3 "missed" flags in the review-flagging
  count are these three. Whether a warning should force review is a design choice rather than an extraction error, but it is reported here as measured against the label.
- **Truncated PDF (EV-F02)** was expected to fail but produced a result with no items, missing customer and total, and `needs_review`. It is not silent, but it
  is not a clean refusal either. The empty file, the corrupt Excel file and the corrupt PDF failed with a clear reason in about 2.4 s.
- **Non-invoice documents** (4 of 4) were rejected. No value was invented for any of the 3 documents that had a field truly missing.

### What this does and does not show

The documents are synthetic (generated by code), so the numbers measure the pipeline on controlled layouts, not on real-world invoices. The sample is small:
one document is 2 % of the status score. The model and the OCR engines are not deterministic, as the difference between the two runs shows, so a single run should
be read as one sample rather than a fixed score. The dataset has no handwriting, no complex real-world layouts and no 1000-page scanned file.

## 2b. Earlier 7-document smoke test (`tests/evaluate_benchmark.py`, also run as `test_evaluation`)

Dataset: **7 documents** generated in code with known ground truth (5 valid commercial documents, 1 non-invoice, 1 corrupt file).

| Metric | Result |
|---|---|
| Document success rate (valid docs reaching `completed`/`needs_review` as labeled) | **100 %** (5/5) |
| Failure/rejection handling (non-invoice rejected, corrupt file failed safely) | **100 %** (2/2) |
| Extraction accuracy, header fields (invoice no., date, supplier, customer, subtotal, tax, total, currency) | **100 %** on the 5 valid documents |
| Failure rate on valid documents | **0 %** |
| Processing time per document | mean **10.9 s**, median 12.1 s, min 3.1 s, max 18.7 s |

Per document: digital PDF invoice 18.7 s · freight waybill 12.3 s · multi-sheet XLSX 12.1 s · scanned PNG receipt **12.4 s** (was 20.2 s when OCR was LLM-only) ·
arithmetic-discrepancy invoice 9.4 s → `needs_review` ✔ · non-invoice document 8.0 s → `rejected` ✔ · corrupt PDF 3.1 s → `failed` (Corrupted File Error) ✔.

Latency is dominated by network round-trips (LLM extraction call, per-statement writes to a remote database, embedding call), not CPU.
Local OCR itself takes about 0.5–0.7 s per page.

## 3. OCR: local first, LLM fallback

`OCR_ENGINE=auto` (default): RapidOCR (PaddleOCR models on ONNX Runtime) reads the image on the server; only when its mean confidence is below 0.80
does the vision LLM re-read it, and that document is then flagged `LOW_OCR_QUALITY` for manual review. Measured:

| Input | Result |
|---|---|
| Clean synthetic receipt | read locally in ~0.7 s, confidence 0.87, invoice number and total exact, **no data sent to OpenAI for OCR** |
| Benchmark receipt PNG | read locally (confidence 0.88); one defect found and fixed: the engine glued two words (`MetroIndustrial`), now repaired by a guarded post-processing rule |
| Blurry, low-resolution receipt | local confidence ~0.55 → LLM fallback → document flagged `LOW_OCR_QUALITY`, confidence 0.51, sent to review (the LLM alone had returned a confident but wrong invoice number and supplier) |

Known limitation: the glued-word repair (`lowercase{3+}Uppercase{4+}` inside one token) will also split a genuine CamelCase name such as `BrightWater`. Set the engine to `vision` if that matters.
Stored confidence scores are now derived (share of key fields found, capped by real OCR confidence, reduced when flagged) instead of fixed constants; they are a heuristic, not a model probability.

## 4. Cross-verification (PDF ↔ PDF, PDF ↔ Excel/CSV ledger)

`POST /api/documents/cross-verify {left_document_id, right_document_id}` matches invoices by normalised invoice number and compares supplier, customer,
date, currency, subtotal, tax and total (amount tolerance 0.01, fuzzy company names, date normalisation). It reports matched, mismatched (with per-field differences)
and invoices present in only one document, and can flag mismatches for review in both documents.

Spreadsheets whose rows are invoices are detected as ledgers and parsed with pandas, without any LLM call; each row is validated like an invoice.
Verified end to end (live DB + LLM): a 3-row ledger vs. PDFs where one PDF total differs by 20.00 → exactly one mismatch reported (`total`, difference −20.00), both
documents flagged `CROSS_VERIFY_MISMATCH`; a PDF that agrees is reported matched; two PDFs carrying the same invoice number with different totals → mismatch;
the ledger/PDF pair is correctly **not** treated as a duplicate.

## 5. End-to-end run through the web proxy (manual, 8 mixed files, one batch)

Uploaded via `POST /api/documents` on the Next.js proxy with API-key auth enabled; all 8 settled in 71 s (3 parallel workers).
Good PDF/CSV/XLSX invoices extracted correctly; wrong-total and missing-field invoices flagged without inventing values; corrupt and empty files failed with specific stages;
a re-uploaded invoice flagged as duplicate. Crash recovery: the server was killed mid-job and the job completed after restart.

## 5b. Full walkthrough through the web proxy (34 scripted checks, 34 passed)

Run against a local backend with API-key auth on and the real database/OpenAI, via the Next.js `/api` proxy (the same path the browser uses):
upload of 11 mixed files in one request (digital PDF, wrong-arithmetic PDF, missing-field PDF, 3-invoice bundle, CSV ledger, 2-sheet XLSX, clean scan, blurry scan, corrupt PDF, empty CSV, non-invoice résumé),
status/progress, per-type extraction, validation flags, per-invoice correction, single and bulk reprocessing, original-file download, history, stats, malformed-id handling,
keyword / semantic / hybrid search, AI answer with citations, PDF-vs-ledger and PDF-vs-PDF cross-verification, refusal to cross-verify unprocessed documents, and cleanup.
The list endpoint returned 33 documents in 21 KB (it previously returned every stored file and full text).

## 6. Scale check

A 1000-page digital PDF (0.41 MB) is parsed to text in **0.09 s**. A 4-page, 3-invoice bundle was split into 3 results (continuation page attached to the right invoice).
The LLM receives the first 6 000 + last 4 000 characters of each invoice (configurable), OCR covers up to 100 scanned pages, up to 200 chunks are embedded in batches.
A full 1000-page *scanned* PDF was **not** run.

## 7. Limitations (read before relying on the numbers)

- The 7-document smoke test (section 2b) is **synthetic and clean**; its 100 % is not a claim about real-world accuracy. The 51-document run (section 2) is larger and includes degraded scans, but it is also synthetic.
  An earlier run of the same benchmark scored 80 % on supplier name (the glued-word OCR defect above), so expect run-to-run and engine-to-engine variation.
- Line-item accuracy is scored in the 51-document run (F1 91.6 %), not in the 7-document smoke test.
- Poor-quality images remain the weak spot; the system now detects unreliable reads and routes them to review rather than trusting them, but it cannot recover unreadable text.
- Multi-invoice splitting relies on a recognisable "Invoice No/Number/#" label on each invoice's first page; bundles without one stay a single result.
- Cross-verification matches on invoice number only; invoices without a number cannot be matched. Ambiguous `dd/mm` vs `mm/dd` dates are **not** reliably read day-first: in the first 51-document run two documents got a different date with no flag; a guard added afterwards corrects the order when the document shows it and flags the invoice when it does not (section 2).
- No load test was run; only three concurrent workers were exercised.
- Job durability and multi-instance safety use PostgreSQL (`FOR UPDATE SKIP LOCKED`, heartbeats, stale-job reclaim after `STALE_JOB_SECONDS`, default 120 s) instead of Redis/BullMQ; this was verified with concurrent sweeps in tests, not with two real server processes.
