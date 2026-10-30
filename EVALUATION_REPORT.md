# DocuIntel — Evaluation Report (Task 7)

All figures below were **measured by running the code** on 2026-10-30 against the live stack
(OpenAI `gpt-4o-mini`, Supabase PostgreSQL in EU, run from a developer laptop). Nothing is estimated.
Re-run with `python tests/evaluate_benchmark.py` and `pytest tests` (needs `DATABASE_URL` and `OPENAI_API_KEY`).

## 1. Automated test suite

`pytest tests` → **39 passed, 0 failed** (240 s). Covers: PDF/CSV/XLSX/XLS/image parsing, OCR routing, 1000-page PDF,
corrupt and empty files, AI extraction (incl. LLM-outage behaviour), validation rules, REST endpoints, upload limits,
API-key auth, durable job recovery, database + pgvector search, evaluation benchmark.

## 2. Labeled benchmark (`tests/evaluate_benchmark.py`)

Dataset: **7 documents** generated in code with known ground truth (5 valid commercial documents, 1 non-invoice, 1 corrupt file).

| Metric | Result |
|---|---|
| Document success rate (valid docs reaching `completed`/`needs_review` as labeled) | **100 %** (5/5) |
| Failure/rejection handling (non-invoice rejected, corrupt file failed safely) | **100 %** (2/2) |
| Extraction accuracy, header fields (invoice no., date, supplier, customer, subtotal, tax, total, currency) | **100 %** on the 5 valid documents |
| Failure rate on valid documents | **0 %** |
| Processing time per document | mean **11.8 s**, median 11.9 s, min 3.2 s, max 20.2 s (an earlier run: mean 10.0 s, median 11.0 s) |

Per document (latest run): digital PDF invoice 17.5 s · freight waybill 11.9 s · multi-sheet XLSX 13.9 s · scanned PNG receipt 20.2 s ·
arithmetic-discrepancy invoice 8.8 s → `needs_review` ✔ · non-invoice document 7.3 s → `rejected` ✔ · corrupt PDF 3.2 s → `failed` (Corrupted File Error) ✔.

Latency is dominated by network round-trips (LLM call, per-statement DB writes to a remote Supabase, embedding call), not CPU.

## 3. End-to-end run through the web proxy (manual, 8 mixed files, one batch)

Uploaded via `POST /api/documents` on the Next.js proxy with API-key auth enabled; all 8 settled in 71 s total (3 parallel workers).

| File | Outcome |
|---|---|
| good 3-page PDF invoice | completed; every header field and both line items correct |
| CSV invoice | completed; correct |
| 2-sheet XLSX (header sheet + lines sheet) | completed; correct, line items from second sheet |
| invoice with wrong total (200 + 20 ≠ 999) | `needs_review`, `TOTAL_MISMATCH` |
| invoice missing number/date/customer | `needs_review`, three `MISSING_*` issues, no values invented |
| corrupt PDF / 0-byte CSV | `failed` with specific stage; reprocessable |
| re-upload of an already processed invoice | `needs_review`, `DUPLICATE_INVOICE` |
| **blurry low-resolution receipt (synthetic)** | OCR **mis-read** the receipt number and merchant (`RCP-1234`/`Arena` instead of `RCP-E2E-9`/`Cafe Aroma`). It was caught only because line items did not add up (`LINE_ITEMS_SUBTOTAL_MISMATCH` → `needs_review`) |

Crash recovery: the backend process was killed during processing; on restart it logged `Recovered 1 interrupted job(s)` and the job completed.

## 4. Scale check

A 1000-page digital PDF (0.41 MB) is parsed to text in **0.09 s** (unit test). The LLM receives the first 6 000 + last 4 000 characters
(configurable), OCR covers up to 100 scanned pages, and up to 200 chunks are embedded in batches. A full 1000-page *scanned* PDF was **not** run
(it would exceed the 100-page OCR cap and cost ~1000 vision calls).

## 5. Limitations (read before relying on the numbers)

- The benchmark is **7 synthetic, clean documents**. 100 % on this set is not a claim about real-world accuracy; one wrong field would move a field score by 20 %.
- Line-item accuracy is **not scored** by the benchmark (only header fields).
- Poor-quality images are the weak spot: the model can return plausible but wrong text for unreadable input. The OCR prompt now asks it to emit `[illegible]`
  (such documents are forced to manual review), but the synthetic blurry receipt above was still misread without that marker. Validation, not OCR, caught it.
- Multi-invoice PDFs produce a single result; only the head/tail of very long documents is sent to the LLM.
- No load test was run; only three concurrent workers were exercised.
- Job durability is PostgreSQL-backed recovery on restart, not Redis/BullMQ. A multi-instance deployment would need a lock (e.g. `FOR UPDATE SKIP LOCKED`) before recovery.
