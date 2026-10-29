"""RAG (Retrieval-Augmented Generation) & Semantic Vector Search Service."""
import logging
from typing import Any, Dict, List
from config import OPENAI_API_KEY
from services.extractor import get_openai_client
import database

logger = logging.getLogger("docuintel.rag")


def generate_embedding(text: str) -> List[float]:
    """Generates 384-dim vector embedding using OpenAI text-embedding-3-small."""
    client = get_openai_client()
    try:
        res = client.embeddings.create(
            input=text[:8000],
            model="text-embedding-3-small",
            dimensions=384,
        )
        return res.data[0].embedding
    except Exception as exc:
        logger.error(f"OpenAI embedding generation failed: {exc}")
        return [0.0] * 384


def chunk_and_index_document(doc_id: str, full_text: str):
    """Splits document text into chunks and stores dense vector embeddings."""
    if not full_text or len(full_text.strip()) < 10:
        return

    # Chunk by paragraphs or ~800 char blocks
    paragraphs = [p.strip() for p in full_text.split("\n\n") if len(p.strip()) > 20]
    if not paragraphs:
        paragraphs = [full_text[:1000]]

    for chunk in paragraphs[:10]:  # Limit to top 10 chunks
        embedding = generate_embedding(chunk)
        database.save_document_embedding(doc_id, chunk, embedding)

    logger.info(f"Indexed {len(paragraphs[:10])} chunks for document {doc_id}.")


def rag_query(query: str, limit: int = 5) -> Dict[str, Any]:
    """Performs semantic vector search and answers query with grounded citations."""
    query_vector = generate_embedding(query)
    matches = database.search_document_embeddings(query_vector, limit=limit)

    if not matches:
        return {
            "answer": "No indexed documents found matching your search query. Please upload documents first.",
            "citations": [],
            "hits": [],
        }

    # Build context from retrieved chunks
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
                {
                    "role": "user",
                    "content": f"Context:\n{context_str}\n\nQuestion: {query}",
                },
            ],
            temperature=0.2,
        )
        answer = response.choices[0].message.content or "No response generated."
    except Exception as exc:
        logger.error(f"RAG LLM synthesis failed: {exc}")
        answer = f"Found {len(matches)} matching document(s). Direct synthesis failed: {exc}"

    return {
        "answer": answer,
        "citations": citations,
        "hits": matches,
    }
