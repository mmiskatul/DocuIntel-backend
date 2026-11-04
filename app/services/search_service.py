"""Document search: keyword, semantic (pgvector) and hybrid (both, merged with reciprocal-rank fusion).

Every hit carries the real document record (file type, size, pages, status, processing time, extracted summary),
not placeholder values.
"""
import re
from typing import Any, Dict, List, Optional

from app.models.document_repo import get_document, list_documents
from app.models.embedding_repo import search_document_embeddings
from app.models.exact_lookup import FIELD_ALIASES, find_exact
from app.services.rag_service import EmbeddingUnavailable, generate_embedding

_FIELD_TOKEN = re.compile(r'(?<!\S)([A-Za-z_]+):("([^"]+)"|\S+)')
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def parse_query(q: str):
    """Splits `supplier:"Acme Ltd" total:1200 rent` into ({'supplier': 'Acme Ltd', 'total': '1200'}, 'rent').
    Only known field names count as fields; anything else (a time such as 10:30) stays free text."""
    fields: Dict[str, str] = {}

    def take(m):
        if m.group(1).lower() in FIELD_ALIASES:
            fields[m.group(1).lower()] = (m.group(3) or m.group(2)).strip('"')
            return " "
        return m.group(0)

    rest = _FIELD_TOKEN.sub(take, q)
    return fields, " ".join(rest.split())


def is_identifier(q: str) -> bool:
    """True when the query names one specific thing: a UUID, an invoice-style code (has a digit, no spaces), a number, or a quoted phrase."""
    q = q.strip()
    if len(q) >= 2 and q[0] == q[-1] == '"':
        return True
    if _UUID.match(q):
        return True
    return " " not in q and any(c.isdigit() for c in q) and len(q) >= 3

RRF_K = 60  # standard reciprocal-rank-fusion constant


def _snippet(text: Optional[str], query: str, width: int = 220) -> str:
    """A window of text around the first occurrence of the query (or of its first word)."""
    if not text:
        return ""
    low = text.lower()
    for needle in [query.lower()] + [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]:
        i = low.find(needle)
        if i >= 0:
            start = max(0, i - width // 3)
            return ("…" if start else "") + text[start:start + width].replace("\n", " ").strip()
    return text[:width].replace("\n", " ").strip()


def _hit(doc: Dict[str, Any], score: float, snippet: str, match: str) -> Dict[str, Any]:
    return {"document_id": str(doc["id"]), "page": 1, "score": round(float(score), 4), "snippet": snippet, "match": match, "document": doc}


def keyword_hits(q: str, limit: int) -> List[Dict[str, Any]]:
    """Filename / invoice number / supplier / customer / extracted-text match (case-insensitive substring)."""
    ql = q.lower().strip()
    out = []
    for d in list_documents(q=q, limit=limit):
        s = d.get("summary") or {}
        fields = [d.get("filename"), s.get("invoice_number"), s.get("supplier_name"), s.get("customer_name")]
        terms = ql.split() or [ql]
        in_fields = sum(any(t in str(f).lower() for f in fields if f) for t in terms)
        score = 0.6 + 0.4 * in_fields / len(terms)  # 1.0 when every word appears in the document's own fields
        snippet = " | ".join(str(x) for x in (d.get("filename"), s.get("invoice_number"), s.get("supplier_name"), s.get("total")) if x not in (None, ""))
        out.append(_hit(d, score, snippet, "keyword"))
    return out


def semantic_hits(q: str, limit: int) -> List[Dict[str, Any]]:
    """Nearest chunks by cosine similarity, best chunk per document."""
    matches = search_document_embeddings(generate_embedding(q, strict=True), limit=max(limit * 3, 10))
    best: Dict[str, Dict[str, Any]] = {}
    for m in matches:
        k = str(m["document_id"])
        if k not in best or m["similarity"] > best[k]["similarity"]:
            best[k] = m
    out = []
    for k, m in sorted(best.items(), key=lambda kv: -kv[1]["similarity"])[:limit]:
        doc = get_document(k)
        if doc:
            out.append(_hit(doc, m["similarity"], m["chunk_text"][:250].replace("\n", " "), "semantic"))
    return out


def hybrid_hits(q: str, limit: int) -> List[Dict[str, Any]]:
    """Keyword and semantic rankings merged with reciprocal-rank fusion; a document found by both ranks highest."""
    kw = keyword_hits(q, limit * 2)
    try:
        sem = semantic_hits(q, limit * 2)
    except EmbeddingUnavailable:  # the combined search still works on words alone
        sem = []
    fused: Dict[str, Dict[str, Any]] = {}
    for source, hits in (("keyword", kw), ("semantic", sem)):
        for rank, h in enumerate(hits):
            entry = fused.setdefault(h["document_id"], {"hit": h, "rrf": 0.0, "by": []})
            entry["rrf"] += 1.0 / (RRF_K + rank + 1)
            entry["by"].append(source)
            if source == "semantic":  # the matching passage is the most informative snippet
                entry["hit"] = {**h, "snippet": h["snippet"]}
    max_score = 2.0 / (RRF_K + 1)
    out = []
    for e in sorted(fused.values(), key=lambda e: -e["rrf"])[:limit]:
        h = dict(e["hit"])
        h["score"] = round(e["rrf"] / max_score, 4)
        h["match"] = "hybrid"
        h["matched_by"] = e["by"]
        out.append(h)
    return out


def exact_hits(q: str, limit: int) -> List[Dict[str, Any]]:
    """Documents matching the specific data typed: id, invoice number, file name, supplier, amount, or `field:value` filters."""
    fields, rest = parse_query(q)
    if fields:
        matches = find_exact(fields=fields, limit=limit)
        if not matches:  # no whole-value match: fall back to partial matching on supplier / invoice number
            matches = _partial_field_matches(fields, limit)
    else:
        matches = find_exact(any_value=q.strip().strip('"'), limit=limit)
    out = []
    for m in matches:
        doc = get_document(str(m["id"]))
        if not doc:
            continue
        if fields and rest:  # free words next to the filters must also appear in the document
            blob = " ".join(str(v) for v in [doc.get("filename"), *(doc.get("summary") or {}).values()] if v).lower()
            if not all(w.lower() in blob for w in rest.split()):
                continue
        out.append(_hit(doc, 1.0, f"{m['matched_field'].replace('_', ' ')}: {m['matched_value']}", "exact"))
    return out


def _partial_field_matches(fields: Dict[str, str], limit: int) -> List[Dict[str, Any]]:
    kw = {}
    for key, value in fields.items():
        col = FIELD_ALIASES.get(key)
        if col == "supplier_name":
            kw["supplier"] = value
        elif col == "invoice_number":
            kw["invoice_number"] = value
        else:
            return []  # other fields have no partial form
    label = ", ".join(f"{k}={v}" for k, v in fields.items())
    return [{"id": d["id"], "matched_field": "fields", "matched_value": label} for d in list_documents(limit=limit, **kw)]


def _pin_exact(q: str, others: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Exact matches first. When the query names one specific thing and it was found, loosely related results are dropped (noise)."""
    by_doc = {h["document_id"]: h for h in others}
    # A document the normal ranking also found keeps that hit (its match type and matched_by); it is only moved to the top.
    exact = [{**by_doc[h["document_id"]], "matched_exact": True} if h["document_id"] in by_doc else h for h in exact_hits(q, limit)]
    fields, _ = parse_query(q)
    if exact and (fields or is_identifier(q)):
        return exact[:limit]
    seen = {h["document_id"] for h in exact}
    return (exact + [h for h in others if h["document_id"] not in seen])[:limit]


def search(q: str, mode: str, limit: int) -> List[Dict[str, Any]]:
    if not q.strip():
        return []
    if mode == "exact":
        return exact_hits(q, limit)
    fields, rest = parse_query(q)
    if fields and not rest:  # a purely field-scoped query needs no semantic guessing when it matched
        found = exact_hits(q, limit)
        if found:
            return found
    text = rest or q
    if mode == "semantic":
        return _pin_exact(q, semantic_hits(text, limit), limit)
    if mode == "hybrid":
        return _pin_exact(q, hybrid_hits(text, limit), limit)
    return _pin_exact(q, keyword_hits(text, limit), limit)
