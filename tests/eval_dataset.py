"""A labelled, reproducible dataset for the extended evaluation (Task 7).

Every document is generated in code from a seeded random generator, together with the true values it contains, so the labels cannot
drift from the files. The set covers digital PDFs in three layouts and several currencies (including European number formats),
multi-page invoices, multi-invoice bundles, spreadsheets and ledgers, scanned images at graded quality, scanned PDFs, invoices whose
arithmetic is wrong, invoices with missing fields, a duplicate, documents that are not invoices, and corrupt or empty files.

It is still synthetic: the text is rendered by this code, so it says nothing about the variety of real-world paper.
"""
import csv
import io
import random
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import openpyxl
import pymupdf
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

SEED = 20261031

SUPPLIERS = ["Harbor Light Supplies", "Brenner Industrial GmbH", "Kestrel Office Goods", "Alder & Finch Ltd", "Nordic Pulse AB", "Meridian Tools Inc.",
             "Saffron Catering Co", "Bluewater Logistics", "Quartz Software Ltd", "Orchard Lane Press", "Ironbridge Steel plc", "Lumen Electric Co"]
CUSTOMERS = ["Delta Retail Group", "Fernhill Clinics", "Gable & Sons", "Helix Biolabs", "Ivory Coast Trading", "Juniper Schools", "Kite Aviation Ltd", "Lakeside Hotels"]
ITEMS = ["Laptop stand (aluminium)", "USB-C docking station", "Ergonomic office chair", "Whiteboard markers (box of 12)", "Cloud hosting - monthly plan",
         "On-site installation service", "Printer toner cartridge", "Standing desk frame", "Network switch 24-port", "Safety gloves (pair)",
         "Pallet transport Hamburg to Lyon", "Annual software licence", "Cold storage handling fee", "Consulting hours", "Packaging materials"]
CURRENCIES = {"USD": "$", "EUR": "€", "GBP": "£"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency")


# ------------------------------------------------------------------ building an invoice
def make_invoice(rng: random.Random, number: str, currency: str = "USD", n_items: Optional[int] = None, tax_rate: Optional[float] = None) -> Dict[str, Any]:
    items = []
    for desc in rng.sample(ITEMS, n_items or rng.randint(2, 5)):
        qty = rng.randint(1, 8)
        price = round(rng.uniform(8, 650), 2)
        items.append({"description": desc, "quantity": qty, "unit_price": price, "amount": round(qty * price, 2)})
    rate = tax_rate if tax_rate is not None else rng.choice([0.05, 0.08, 0.10, 0.15, 0.19, 0.20])
    subtotal = round(sum(i["amount"] for i in items), 2)
    tax = round(subtotal * rate, 2)
    d = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    return {"invoice_number": number, "invoice_date": d.isoformat(), "supplier_name": rng.choice(SUPPLIERS), "customer_name": rng.choice(CUSTOMERS),
            "subtotal": subtotal, "tax": tax, "total": round(subtotal + tax, 2), "currency": currency, "line_items": items, "_rate": rate}


def truth(inv: Dict[str, Any]) -> Dict[str, Any]:
    """The labelled values only (no helper keys)."""
    return {**{f: inv.get(f) for f in FIELDS}, "line_items": [dict(i) for i in inv.get("line_items", [])]}


def date_text(iso: str, style: str) -> str:
    d = date.fromisoformat(iso)
    return {"iso": iso, "long": f"{d.day} {MONTHS[d.month - 1]} {d.year}", "us": f"{MONTHS[d.month - 1]} {d.day}, {d.year}",
            "dmy": f"{d.day:02d}/{d.month:02d}/{d.year}"}[style]


def money(amount: float, currency: str, style: str = "en") -> str:
    s = f"{amount:,.2f}"
    if style == "eu":                                    # 1.234,56 EUR
        return s.replace(",", "X").replace(".", ",").replace("X", ".") + " " + CURRENCIES[currency]
    return f"{CURRENCIES[currency]}{s}"


def invoice_lines(inv: Dict[str, Any], variant: str, dstyle: str = "iso", nstyle: str = "en", hide: tuple = ()) -> List[str]:
    """The printed lines of one invoice. `hide` removes a field from the page (the label then says it is not present)."""
    c = inv["currency"]
    m = lambda a: money(a, c, nstyle)
    dt = date_text(inv["invoice_date"], dstyle)
    rows = [f"{i['description'][:34]:<34} {i['quantity']:>3}  {m(i['unit_price']):>12}  {m(i['amount']):>13}" for i in inv["line_items"]]
    head = f"{'Description':<34} {'Qty':>3}  {'Unit price':>12}  {'Amount':>13}"
    L: List[Optional[str]] = []
    if variant == "A":
        L += ["INVOICE", "",
              None if "invoice_number" in hide else f"Invoice Number: {inv['invoice_number']}",
              None if "invoice_date" in hide else f"Invoice Date: {dt}",
              None if "supplier_name" in hide else f"Supplier: {inv['supplier_name']}",
              None if "customer_name" in hide else f"Customer: {inv['customer_name']}", "", head, *rows, "",
              f"Subtotal: {m(inv['subtotal'])}", f"Tax ({round(inv['_rate'] * 100)}%): {m(inv['tax'])}", f"Total: {m(inv['total'])}", f"Currency: {c}"]
    elif variant == "B":
        L += [None if "supplier_name" in hide else inv["supplier_name"].upper(), "",
              None if "invoice_number" in hide else f"INVOICE No. {inv['invoice_number']}",
              None if "invoice_date" in hide else f"Date: {dt}",
              None if "customer_name" in hide else f"Bill To: {inv['customer_name']}", "", head, *rows, "",
              f"{'Subtotal':>50} {m(inv['subtotal'])}", f"{'VAT (' + str(round(inv['_rate'] * 100)) + '%)':>50} {m(inv['tax'])}",
              f"{'Amount Due':>50} {m(inv['total'])}", "", f"All amounts in {c}"]
    else:   # C: different labels, and the currency is shown only by its symbol
        L += ["Tax Invoice", "",
              None if "supplier_name" in hide else f"Seller: {inv['supplier_name']}",
              None if "customer_name" in hide else f"Buyer: {inv['customer_name']}",
              None if "invoice_number" in hide else f"Document no.: {inv['invoice_number']}",
              None if "invoice_date" in hide else f"Issued: {dt}", "", head, *rows, "",
              f"Net total        {m(inv['subtotal'])}", f"VAT {round(inv['_rate'] * 100)}%           {m(inv['tax'])}", f"Gross total      {m(inv['total'])}"]
    return [x for x in L if x is not None]


# ------------------------------------------------------------------ rendering to files
def pdf_bytes(pages: List[List[str]]) -> bytes:
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=595, height=842)
        y = 56
        for line in lines:
            page.insert_text((40, y), line, fontsize=8.6, fontname="cour")
            y += 14
    out = doc.tobytes()
    doc.close()
    return out


def _font(size: int):
    for name in ("cour.ttf", "consola.ttf", "DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_image(lines: List[str], size: int = 25) -> Image.Image:
    f = _font(size)
    w = 1250
    h = 90 + int(size * 1.55) * (len(lines) + 1)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = 40
    for line in lines:
        d.text((50, y), line, fill=(20, 20, 20), font=f)
        y += int(size * 1.55)
    return img


def _noise(img: Image.Image, rng: random.Random, sigma: float) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    gen = np.random.default_rng(rng.randint(0, 2**31))
    return Image.fromarray(np.clip(arr + gen.normal(0, sigma, arr.shape), 0, 255).astype(np.uint8))


def degrade(img: Image.Image, quality: str, rng: random.Random) -> (bytes, str, str):
    """Returns (file bytes, file extension, mime) for a given scan quality."""
    fmt, ext, mime, kw = "PNG", "png", "image/png", {}
    if quality == "jpeg_q35":
        fmt, ext, mime, kw = "JPEG", "jpg", "image/jpeg", {"quality": 35}
    elif quality == "tiff":
        fmt, ext, mime = "TIFF", "tiff", "image/tiff"
    elif quality == "lowres":
        img = img.resize((int(img.width * 0.42), int(img.height * 0.42)), Image.LANCZOS)
    elif quality == "blur_mild":
        img = img.filter(ImageFilter.GaussianBlur(1.3))
    elif quality == "blur_heavy":
        img = img.filter(ImageFilter.GaussianBlur(3.2))
    elif quality == "noise":
        img = _noise(img, rng, 30)
    elif quality == "rotate":
        img = img.rotate(2.8, expand=True, fillcolor="white", resample=Image.BICUBIC)
    elif quality == "faded":
        img = ImageEnhance.Brightness(ImageEnhance.Contrast(img).enhance(0.38)).enhance(1.3)
    elif quality == "bad_scan":
        img = img.rotate(1.8, expand=True, fillcolor="white", resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(1.1))
        img = _noise(ImageEnhance.Contrast(img).enhance(0.7), rng, 22)
        fmt, ext, mime, kw = "JPEG", "jpg", "image/jpeg", {"quality": 40}
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue(), ext, mime


def image_pdf(img_bytes: bytes) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(20, 20, 575, 822), stream=img_bytes, keep_proportion=True)
    out = doc.tobytes()
    doc.close()
    return out


def _kv_rows(inv, hide=()):
    rows = [["Invoice Number", inv["invoice_number"]], ["Invoice Date", inv["invoice_date"]], ["Supplier", inv["supplier_name"]],
            ["Customer", inv["customer_name"]], ["Currency", inv["currency"]]]
    return [r for r in rows if r[0] not in hide]


def _item_rows(inv):
    return [["Description", "Qty", "Unit price", "Amount"], *[[i["description"], i["quantity"], i["unit_price"], i["amount"]] for i in inv["line_items"]]]


def xlsx_single(inv) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"
    for r in _kv_rows(inv):
        ws.append(r)
    ws.append([])
    for r in _item_rows(inv):
        ws.append(r)
    ws.append([])
    ws.append(["Subtotal", "", "", inv["subtotal"]])
    ws.append([f"Tax ({round(inv['_rate'] * 100)}%)", "", "", inv["tax"]])
    ws.append(["Total", "", "", inv["total"]])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def xlsx_multisheet(inv) -> bytes:
    wb = openpyxl.Workbook()
    d = wb.active
    d.title = "Details"
    for r in _kv_rows(inv):
        d.append(r)
    lines = wb.create_sheet("Lines")
    for r in _item_rows(inv):
        lines.append(r)
    t = wb.create_sheet("Totals")
    t.append(["Subtotal", inv["subtotal"]])
    t.append(["Tax", inv["tax"]])
    t.append(["Total", inv["total"]])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def csv_invoice(inv) -> bytes:
    s = io.StringIO()
    w = csv.writer(s)
    for r in _kv_rows(inv):
        w.writerow(r)
    w.writerow([])
    for r in _item_rows(inv):
        w.writerow(r)
    w.writerow([])
    w.writerow(["Subtotal", "", "", inv["subtotal"]])
    w.writerow(["Tax", "", "", inv["tax"]])
    w.writerow(["Total", "", "", inv["total"]])
    return s.getvalue().encode("utf-8")


LEDGER_HEAD = ["Invoice Number", "Invoice Date", "Supplier", "Customer", "Subtotal", "Tax", "Total", "Currency"]


def ledger_rows(invs):
    return [[i["invoice_number"], i["invoice_date"], i["supplier_name"], i["customer_name"], i["subtotal"], i["tax"], i["total"], i["currency"]] for i in invs]


def ledger_xlsx(invs) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ledger"
    ws.append(LEDGER_HEAD)
    for r in ledger_rows(invs):
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def ledger_csv(invs) -> bytes:
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(LEDGER_HEAD)
    for r in ledger_rows(invs):
        w.writerow(r)
    return s.getvalue().encode("utf-8")


# ------------------------------------------------------------------ the dataset
def build_dataset(seed: int = SEED, run_tag: str = "0000") -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    cases: List[Dict[str, Any]] = []
    counter = [0]

    def number(style: int = 0) -> str:
        counter[0] += 1
        n = counter[0]
        return [f"EV{run_tag}-{n:04d}", f"INV/{run_tag}/{n:03d}", f"{run_tag}-EV-{n:04d}"][style % 3]

    def add(cid, category, name, ext, mime, data, invoices, status="completed", codes=(), quality=None, note=""):
        cases.append({"id": cid, "category": category, "quality": quality, "filename": f"{cid.lower()}_{name}.{ext}", "mime": mime, "bytes": data,
                      "expected_status": status, "invoices": [truth(i) if "_rate" in i else i for i in invoices], "expect_codes": list(codes), "note": note})

    # A. digital PDFs: three layouts, three currencies, four date styles, two number styles
    layouts = [("A", "iso", "en", "USD"), ("A", "long", "en", "EUR"), ("A", "us", "en", "GBP"), ("A", "dmy", "en", "USD"),
               ("B", "iso", "en", "GBP"), ("B", "long", "en", "USD"), ("B", "dmy", "eu", "EUR"), ("B", "us", "eu", "EUR"),
               ("C", "iso", "en", "USD"), ("C", "long", "en", "EUR"), ("C", "dmy", "en", "GBP"), ("C", "us", "en", "USD")]
    for k, (variant, ds, ns, cur) in enumerate(layouts, 1):
        inv = make_invoice(rng, number(k), cur)
        note = f"layout {variant}, dates '{ds}', numbers '{ns}'" + (", currency shown only as a symbol" if variant == "C" else "")
        add(f"EV-D{k:02d}", "digital_pdf", f"digital_{variant}_{cur.lower()}", "pdf", "application/pdf", pdf_bytes([invoice_lines(inv, variant, ds, ns)]), [inv], note=note)

    # B. multi-page invoices (items continue over the pages, totals on the last one)
    for k, n_items in enumerate((7, 9, 12), 1):
        inv = make_invoice(rng, number(k), rng.choice(list(CURRENCIES)), n_items=n_items)
        lines = invoice_lines(inv, "A")
        cut_a, cut_b = 9, 9 + 11
        pages = [lines[:cut_a + 3], ["(continued)"] + lines[cut_a + 3:cut_b + 3], ["(continued)"] + lines[cut_b + 3:]] if n_items >= 10 else [lines[:cut_a + 1], ["(continued)"] + lines[cut_a + 1:]]
        add(f"EV-M{k:02d}", "multi_page_pdf", f"multipage_{n_items}_items", "pdf", "application/pdf", pdf_bytes(pages), [inv], note=f"{n_items} line items over {len(pages)} pages")

    # C. several invoices in one PDF (one per page)
    for k in (1, 2):
        invs = [make_invoice(rng, number(k + j), rng.choice(list(CURRENCIES)), n_items=rng.randint(2, 3)) for j in range(3)]
        add(f"EV-B{k:02d}", "bundle_pdf", "bundle_of_3", "pdf", "application/pdf", pdf_bytes([invoice_lines(i, "A") for i in invs]), invs, note="three invoices, one per page")

    # D. spreadsheets: one sheet, three sheets, CSV
    for k, (name, fn, ext, mime) in enumerate((("single_sheet", xlsx_single, "xlsx", XLSX), ("three_sheets", xlsx_multisheet, "xlsx", XLSX), ("csv", csv_invoice, "csv", "text/csv")), 1):
        inv = make_invoice(rng, number(k), rng.choice(list(CURRENCIES)))
        add(f"EV-S{k:02d}", "spreadsheet", name, ext, mime, fn(inv), [inv])

    # E. ledgers (one invoice per row, no line items)
    for k, (fn, ext, mime) in enumerate(((ledger_xlsx, "xlsx", XLSX), (ledger_csv, "csv", "text/csv")), 1):
        invs = []
        for j in range(6):
            i = make_invoice(rng, number(j), rng.choice(list(CURRENCIES)))
            i["line_items"] = []
            invs.append(i)
        add(f"EV-L{k:02d}", "ledger", f"ledger_6_rows_{ext}", ext, mime, fn(invs), invs, note="rows are invoices; read without the language model")

    # F. scanned images at graded quality (all show the same layout, so only the image quality changes)
    for k, q in enumerate(("clean", "tiff", "jpeg_q35", "lowres", "blur_mild", "blur_heavy", "noise", "rotate", "faded", "bad_scan"), 1):
        inv = make_invoice(rng, number(k), rng.choice(list(CURRENCIES)))
        data, ext, mime = degrade(render_image(invoice_lines(inv, "A")), q, rng)
        add(f"EV-I{k:02d}", "scanned_image", f"scan_{q}", ext, mime, data, [inv], quality=q, note=f"scan quality: {q}")

    # G. scanned PDFs (a picture of the page inside a PDF)
    for k, q in enumerate(("clean", "jpeg_q35"), 1):
        inv = make_invoice(rng, number(k), rng.choice(list(CURRENCIES)))
        data, _, _ = degrade(render_image(invoice_lines(inv, "A")), q, rng)
        add(f"EV-P{k:02d}", "scanned_pdf", f"scanned_pdf_{q}", "pdf", "application/pdf", image_pdf(data), [inv], quality=q)

    # H. arithmetic that does not add up: the page shows the wrong figures, so the right answer is "as printed, and flagged"
    inv = make_invoice(rng, number(0), "USD")
    inv["total"] = round(inv["total"] + 50, 2)
    add("EV-X01", "wrong_arithmetic", "total_too_high", "pdf", "application/pdf", pdf_bytes([invoice_lines(inv, "A")]), [inv], "needs_review", ["TOTAL_MISMATCH"], note="printed total is 50.00 too high")
    inv = make_invoice(rng, number(1), "EUR")
    inv["tax"] = round(inv["tax"] + 17.5, 2)
    add("EV-X02", "wrong_arithmetic", "tax_misstated", "pdf", "application/pdf", pdf_bytes([invoice_lines(inv, "B")]), [inv], "needs_review", ["TOTAL_MISMATCH"], note="printed tax does not fit the total")
    inv = make_invoice(rng, number(2), "GBP", n_items=3)
    inv["line_items"][1]["amount"] = round(inv["line_items"][1]["amount"] + 25, 2)
    inv["subtotal"] = round(sum(i["amount"] for i in inv["line_items"]), 2)
    inv["tax"] = round(inv["subtotal"] * inv["_rate"], 2)
    inv["total"] = round(inv["subtotal"] + inv["tax"], 2)
    add("EV-X03", "wrong_arithmetic", "line_amount_wrong", "pdf", "application/pdf", pdf_bytes([invoice_lines(inv, "A")]), [inv], "needs_review", ["LINE_ITEM_ARITHMETIC_ERROR"], note="one line amount is not quantity x price")
    inv = make_invoice(rng, number(0), "USD", n_items=3)
    inv["subtotal"] = round(inv["subtotal"] + 40, 2)
    inv["tax"] = round(inv["subtotal"] * inv["_rate"], 2)
    inv["total"] = round(inv["subtotal"] + inv["tax"], 2)
    add("EV-X04", "wrong_arithmetic", "subtotal_not_sum_of_lines", "pdf", "application/pdf", pdf_bytes([invoice_lines(inv, "A")]), [inv], "needs_review", ["LINE_ITEMS_SUBTOTAL_MISMATCH"], note="subtotal is 40.00 more than the lines")

    # I. fields that are not on the page: the right answer is null (never an invented value) and a review flag
    for k, (hide, code) in enumerate((("invoice_date", "MISSING_INVOICE_DATE"), ("customer_name", "MISSING_CUSTOMER_NAME"), ("invoice_number", "MISSING_INVOICE_NUMBER")), 1):
        inv = make_invoice(rng, number(k), "USD")
        printed = invoice_lines(inv, "A", hide=(hide,))
        shown = {**inv, hide: None}
        add(f"EV-N{k:02d}", "missing_field", f"no_{hide}", "pdf", "application/pdf", pdf_bytes([printed]), [shown], "needs_review", [code], note=f"{hide} is not printed")

    # J. a duplicate: same invoice number and supplier, another layout (the first must pass, the second must be flagged)
    inv = make_invoice(rng, number(0), "EUR")
    add("EV-U01", "duplicate", "original", "pdf", "application/pdf", pdf_bytes([invoice_lines(inv, "A")]), [inv], note="first copy")
    add("EV-U02", "duplicate", "second_copy", "pdf", "application/pdf", pdf_bytes([invoice_lines(inv, "B", "long")]), [inv], "needs_review", ["DUPLICATE_INVOICE"], note="same number and supplier as EV-U01")

    # K. documents that are not invoices
    texts = {
        "resume": ["CURRICULUM VITAE", "Jordan Ellis - Senior Backend Engineer", "", "Experience", "2021-2026  Staff engineer, Orbit Systems", "2017-2021  Developer, Pixel Forge", "", "Education", "BSc Computer Science, 2017"],
        "contract": ["SERVICE AGREEMENT", "", "1. Term. This agreement starts on 1 January 2026 and continues for twelve months.", "2. Confidentiality. Each party shall keep the other's information secret.",
                     "3. Termination. Either party may terminate with thirty days written notice.", "", "Signed for the Client: ____________   Signed for the Supplier: ____________"],
        "article": ["Effects of Sleep Duration on Memory Consolidation", "", "Abstract. We studied 120 adults over six weeks. Participants who slept more than seven hours showed",
                    "a 14% improvement on recall tasks. We discuss implications for study schedules.", "", "Keywords: sleep, memory, cognition"],
    }
    for k, (name, lines) in enumerate(texts.items(), 1):
        add(f"EV-R{k:02d}", "not_an_invoice", name, "pdf", "application/pdf", pdf_bytes([lines]), [], "rejected", note="not a billing document")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Staff"
    ws.append(["Name", "Department", "Start date", "Office"])
    for n, d, s, o in (("A. Rivera", "Sales", "2022-03-01", "Berlin"), ("T. Osei", "Support", "2023-07-15", "Accra"), ("M. Novak", "Finance", "2021-11-02", "Prague")):
        ws.append([n, d, s, o])
    b = io.BytesIO()
    wb.save(b)
    add("EV-R04", "not_an_invoice", "staff_roster", "xlsx", XLSX, b.getvalue(), [], "rejected", note="a spreadsheet that is not an invoice")

    # L. corrupt and empty files
    good = pdf_bytes([invoice_lines(make_invoice(rng, number(0), "USD"), "A")])
    add("EV-F01", "corrupt_or_empty", "garbage_bytes", "pdf", "application/pdf", bytes(rng.getrandbits(8) for _ in range(900)), [], "failed", note="random bytes named .pdf")
    add("EV-F02", "corrupt_or_empty", "truncated_pdf", "pdf", "application/pdf", good[: int(len(good) * 0.4)], [], "failed", note="a real PDF cut off at 40%")
    add("EV-F03", "corrupt_or_empty", "empty_file", "pdf", "application/pdf", b"", [], "failed", note="0 bytes")
    add("EV-F04", "corrupt_or_empty", "broken_xlsx", "xlsx", XLSX, b"PK\x03\x04" + bytes(rng.getrandbits(8) for _ in range(400)), [], "failed", note="a damaged Excel file")
    return cases
