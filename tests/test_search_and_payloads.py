"""Hybrid search, real search metadata, slim list payloads and removal of unused options."""
import io
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.document_repo import delete_document, get_document, list_documents, save_document
from app.services.pipeline import run_document_pipeline
from app.services.search_service import _snippet

client = TestClient(app)


def _invoice_csv(tag):
    return (f"INVOICE\nInvoice Number: SRCH-{tag}\nInvoice Date: 2026-04-01\nSupplier: Zephyrion Cryogenics {tag}\nCustomer: Orion Retail\n"
            "Liquid nitrogen dewar flasks 4 x 50.00 = 200.00\nSubtotal: 200.00\nTax: 20.00\nTotal: 220.00\nCurrency: USD\n").encode()


def _upload(doc_id, tag):
    save_document({"id": doc_id, "filename": f"zephyr_{tag}.txt", "file_type": "text/plain", "status": "queued"})
    run_document_pipeline(doc_id, f"zephyr_{tag}.txt", _invoice_csv(tag), "text/plain")


def test_snippet_centres_on_the_match():
    text = "a" * 300 + " needle " + "b" * 300
    s = _snippet(text, "needle")
    assert "needle" in s and len(s) <= 230
    assert _snippet(None, "x") == ""


def test_hybrid_semantic_and_keyword_search_return_real_metadata():
    tag = uuid.uuid4().hex[:6].upper()
    doc_id = str(uuid.uuid4())
    try:
        _upload(doc_id, tag)
        assert get_document(doc_id)["status"] == "completed"

        # keyword: exact supplier text
        kw = client.get("/api/v1/search", params={"q": f"Zephyrion Cryogenics {tag}", "mode": "keyword"}).json()
        hit = next(h for h in kw if h["document_id"] == doc_id)
        assert hit["match"] == "keyword" and hit["score"] == 1.0

        # semantic: a paraphrase that shares no exact words with the supplier name
        sem = client.get("/api/v1/search", params={"q": "liquid nitrogen storage flasks purchase", "mode": "semantic"}).json()
        assert any(h["document_id"] == doc_id for h in sem), [h["document"]["filename"] for h in sem]

        # hybrid: found by both rankings, so it scores at the top and says how it was matched
        hy = client.get("/api/v1/search", params={"q": f"Zephyrion Cryogenics {tag}", "mode": "hybrid"}).json()
        top = next(h for h in hy if h["document_id"] == doc_id)
        assert top["match"] == "hybrid" and set(top["matched_by"]) == {"keyword", "semantic"}
        assert top["score"] >= hy[-1]["score"]

        # metadata is the real document record, not placeholders
        d = top["document"]
        assert d["file_type"] == "text/plain"
        assert d["filename"] == f"zephyr_{tag}.txt" and d["summary"]["invoice_number"] == f"SRCH-{tag}"
        assert d["processing_ms"] is not None and d["processing_ms"] > 0
        assert "file_base64" not in d
    finally:
        delete_document(doc_id)


def test_search_validates_mode_and_empty_query():
    assert client.get("/api/v1/search", params={"q": "x", "mode": "bogus"}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "", "mode": "hybrid"}).json() == []
    assert client.get("/api/v1/search", params={"q": "x", "limit": 0}).status_code == 422


def test_rag_endpoint_ignores_nothing_and_reports_real_model():
    r = client.get("/api/v1/search/rag", params={"q": "which supplier sells flasks?"}).json()
    assert set(r) >= {"answer", "citations", "hits", "provider_used"}
    assert r["provider_used"] == "gpt-4o-mini"
    for h in r["hits"]:
        assert "file_base64" not in h["document"]


def test_list_and_detail_payloads_do_not_ship_files_or_full_text():
    doc_id = str(uuid.uuid4())
    save_document({"id": doc_id, "filename": "p.csv", "file_type": "text/csv", "status": "completed",
                   "file_base64": "QUJD" * 1000, "extracted_text": "hello " * 1000})
    try:
        listed = next(d for d in list_documents(limit=500) if d["id"] == doc_id)
        detail = get_document(doc_id)
        for d in (listed, detail):
            assert "file_base64" not in d and "extracted_text" not in d
        body = client.get(f"/api/v1/documents/{doc_id}/file")
        assert body.status_code == 200 and len(body.content) == 3000  # the original is still downloadable on its own endpoint
        assert len(client.get("/api/v1/documents", params={"limit": 5}).content) < 20_000
    finally:
        delete_document(doc_id)


def test_unused_provider_option_and_keys_are_gone():
    import app.config as cfg
    assert not hasattr(cfg, "ANTHROPIC_API_KEY") and not hasattr(cfg, "DEEPSEEK_API_KEY") and not hasattr(cfg, "XAI_API_KEY")
    # the old `provider` query parameter is no longer part of the API
    spec = client.get("/openapi.json").json()
    params = [p["name"] for p in spec["paths"]["/api/v1/search/rag"]["get"]["parameters"]]
    assert "q" in params and "provider" not in params
