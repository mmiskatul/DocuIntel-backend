"""Document search: keyword, semantic (pgvector) and hybrid (both, merged with reciprocal-rank fusion).

Every hit carries the real document record (file type, size, pages, status, processing time, extracted summary),
not placeholder values.
"""
import re
from typing import Any, Dict, List, Optional

from app.models.document_repo import get_document, list_documents
from app.models.embedding_repo import search_document_embeddings
from app.services.rag_service import generate_embedding

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
    matches = search_document_embeddings(generate_embedding(q), limit=max(limit * 3, 10))
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
    kw, sem = keyword_hits(q, limit * 2), semantic_hits(q, limit * 2)
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


def search(q: str, mode: str, limit: int) -> List[Dict[str, Any]]:
    if not q.strip():
        return []
    if mode == "semantic":
        return semantic_hits(q, limit)
    if mode == "hybrid":
        return hybrid_hits(q, limit)
    return keyword_hits(q, limit)
