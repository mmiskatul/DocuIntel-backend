"""RAG (Retrieval-Augmented Generation) & Semantic Vector Search Service."""
import logging
from typing import Any, Dict, List
from app.services.vision_ocr import get_openai_client
from app.models.embedding_repo import save_document_embedding, search_document_embeddings

logger = logging.getLogger("docuintel.services.rag")


def generate_embedding(text: str) -> List[float]:
    """Generates 384-dimensional vector embedding using OpenAI text-embedding-3-small."""
    client = get_openai_client()
    try:
        res = client.embeddings.create(
            input=text[:8000],
            model="text-embedding-3-small",
            dimensions=384,
        )
        return res.data[0].embedding
    except Exception as exc:
        logger.error(f"Embedding error: {exc}")
        return [0.0] * 384


def index_document_chunks(doc_id: str, full_text: str):
    """Splits document text into semantic chunks and stores embeddings."""
    if not full_text or len(full_text.strip()) < 10:
        return

    paragraphs = [p.strip() for p in full_text.split("\n\n") if len(p.strip()) > 20]
    if not paragraphs:
        paragraphs = [full_text[:1000]]

    for chunk in paragraphs[:10]:
        vector = generate_embedding(chunk)
        save_document_embedding(doc_id, chunk, vector)

    logger.info(f"Indexed {len(paragraphs[:10])} vector chunks for document {doc_id}.")


def rag_search_and_answer(query: str, limit: int = 5) -> Dict[str, Any]:
    """Retrieves top semantic matches and synthesizes an answer with citations."""
    query_vector = generate_embedding(query)
    matches = search_document_embeddings(query_vector, limit=limit)

    if not matches:
        return {
            "answer": "No indexed documents found matching your search query. Please upload documents first.",
            "citations": [],
            "hits": [],
        }

    context_blocks = []
    citations = []
    for idx, m in enumerate(matches):
        context_blocks.append(f"[{idx+1}] File: {m['filename']}\nSnippet: {m['chunk_text']}")
        citations.append({
            "document_id": m["document_id"],
            "filename": m["filename"],
            "snippet": m["chunk_text"][:200],
            "similarity": m["similarity"],
        })

    context_str = "\n\n".join(context_blocks)
    client = get_openai_client()

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful Document Intelligence AI Assistant. "
                        "Answer the user query based ONLY on the provided context snippets. "
                        "Cite the source document filenames whenever stating facts."
                    ),
                },
                {"role": "user", "content": f"Context:\n{context_str}\n\nQuestion: {query}"},
            ],
            temperature=0.2,
        )
        answer = response.choices[0].message.content or "No response generated."
    except Exception as exc:
        logger.error(f"RAG synthesis error: {exc}")
        answer = f"Found {len(matches)} matching document(s). Direct synthesis failed: {exc}"

    return {
        "answer": answer,
        "citations": citations,
        "hits": matches,
    }
