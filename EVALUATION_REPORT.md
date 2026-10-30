# DocuIntel — Evaluation Report (Task 7)

All figures below were **measured by running the code** on 2026-10-30/31 against the live stack
(OpenAI `gpt-4o-mini`, Supabase PostgreSQL in EU, run from a developer laptop). Nothing is estimated.
Re-run with `python tests/evaluate_benchmark.py` and `pytest tests` (needs `DATABASE_URL` and `OPENAI_API_KEY`).

## 1. Automated test suite

`pytest tests` → **60 passed, 0 failed** (about 6 minutes; the tests call the real database and OpenAI).
Covers: PDF/CSV/XLSX/XLS/image parsing, local + LLM OCR routing, 1000-page PDF, corrupt and empty files, AI extraction
(incl. LLM-outage behaviour), validation rules, REST endpoints, upload limits, API-key auth, durable job recovery
(including three concurrent recovery sweeps that never claim a job twice), multi-invoice splitting, per-invoice corrections,
spreadsheet ledgers, **PDF-vs-PDF and PDF-vs-Excel cross-verification**, database + pgvector search, evaluation benchmark.

## 2. Labeled benchmark (`tests/evaluate_benchmark.py`)

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

## 6. Scale check

A 1000-page digital PDF (0.41 MB) is parsed to text in **0.09 s**. A 4-page, 3-invoice bundle was split into 3 results (continuation page attached to the right invoice).
The LLM receives the first 6 000 + last 4 000 characters of each invoice (configurable), OCR covers up to 100 scanned pages, up to 200 chunks are embedded in batches.
A full 1000-page *scanned* PDF was **not** run.

## 7. Limitations (read before relying on the numbers)

- The benchmark is **7 synthetic, clean documents**. 100 % on this set is not a claim about real-world accuracy; one wrong field would move a field score by 20 %.
  An earlier run of the same benchmark scored 80 % on supplier name (the glued-word OCR defect above), so expect run-to-run and engine-to-engine variation.
- Line-item accuracy is **not scored** by the benchmark (only header fields).
- Poor-quality images remain the weak spot; the system now detects unreliable reads and routes them to review rather than trusting them, but it cannot recover unreadable text.
- Multi-invoice splitting relies on a recognisable "Invoice No/Number/#" label on each invoice's first page; bundles without one stay a single result.
- Cross-verification matches on invoice number only; invoices without a number cannot be matched. Ambiguous `dd/mm` vs `mm/dd` dates are read day-first.
- No load test was run; only three concurrent workers were exercised.
- Job durability and multi-instance safety use PostgreSQL (`FOR UPDATE SKIP LOCKED`, heartbeats, stale-job reclaim after `STALE_JOB_SECONDS`, default 120 s) instead of Redis/BullMQ; this was verified with concurrent sweeps in tests, not with two real server processes.
