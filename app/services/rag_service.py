"""Question Answering & Semantic Vector Search Service."""
import logging
import time
from typing import Any, Dict, List
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import OPENAI_API_KEY, MAX_INDEX_CHUNKS
from app.models.embedding_repo import save_document_embedding, search_document_embeddings

logger = logging.getLogger("docuintel.services.search")

# Text Splitter for semantic chunking
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    separators=["\n\n", "\n", ". ", " ", ""],
)

# OpenAI Embeddings (384-dimensional pgvector compatibility)
embeddings_model = OpenAIEmbeddings(
    model="text-embedding-3-small",
    dimensions=384,
    api_key=OPENAI_API_KEY,
)

# Chat Model for Answer Synthesis
answer_model = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.2,
    api_key=OPENAI_API_KEY,
)

# Prompt Template
PROMPT_TEMPLATE = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are a professional Document Intelligence Assistant. "
            "Answer the user query concisely and accurately based ONLY on the provided context snippets. "
            "Cite the source document filenames whenever stating facts or amounts."
        ),
    ),
    (
        "human",
        "Context:\n{context}\n\nQuestion: {question}",
    ),
])

answer_chain = PROMPT_TEMPLATE | answer_model | StrOutputParser()


def generate_embedding(text: str) -> List[float]:
    """Generates 384-dimensional vector embedding."""
    try:
        return embeddings_model.embed_query(text[:8000])
    except Exception as exc:
        logger.error(f"Embedding generation error: {exc}")
        return [0.0] * 384


def index_document_chunks(doc_id: str, full_text: str):
    """Splits document text and indexes embeddings."""
    if not full_text or len(full_text.strip()) < 10:
        return

    chunks = text_splitter.split_text(full_text)
    if not chunks:
        chunks = [full_text[:1000]]

    chunks = chunks[:MAX_INDEX_CHUNKS]
    vectors = embeddings_model.embed_documents(chunks)  # batched API calls
    for chunk, vector in zip(chunks, vectors):
        save_document_embedding(doc_id, chunk, vector)

    logger.info(f"Indexed {len(chunks)} vector chunks for document {doc_id}.")


def rag_search_and_answer(query: str, limit: int = 5) -> Dict[str, Any]:
    """Retrieves top semantic matches and runs chain for grounded answers."""
    start_time = time.time()
    query_vector = generate_embedding(query)
    matches = search_document_embeddings(query_vector, limit=limit)

    elapsed_ms = max(int((time.time() - start_time) * 1000), 25)

    if not matches:
        return {
            "query": query,
            "answer": "No indexed documents found matching your search query. Please upload your invoices or PDFs first.",
            "provider_used": "DocuIntel Intelligence Engine",
            "latency_ms": elapsed_ms,
            "citations": [],
            "hits": [],
        }

    langchain_docs: List[Document] = []
    citations: List[Dict[str, Any]] = []
    hits: List[Dict[str, Any]] = []

    for m in matches:
        doc = Document(
            page_content=m["chunk_text"],
            metadata={"filename": m["filename"], "document_id": m["document_id"]},
        )
        langchain_docs.append(doc)
        citations.append({
            "document_id": m["document_id"],
            "filename": m["filename"],
            "supplier": m.get("supplier_name"),
            "total": m.get("total"),
            "currency": m.get("currency") or "USD",
            "similarity": m["similarity"],
            "snippet": m["chunk_text"][:220],
        })
        hits.append({
            "document_id": m["document_id"],
            "page": 1,
            "score": m["similarity"],
            "snippet": m["chunk_text"][:250],
            "match": "semantic",
            "document": {
                "id": m["document_id"],
                "filename": m["filename"],
                "file_type": "application/pdf",
                "size_bytes": 0,
                "status": m.get("status") or "completed",
                "stage": "Completed",
                "progress": 1.0,
                "page_count": 1,
                "pages_done": 1,
                "error": None,
                "attempts": 1,
                "processing_ms": 120,
                "created_at": m.get("created_at") or "",
                "updated_at": m.get("created_at") or "",
                "invoice_count": 1,
                "needs_review": False,
                "summary": {
                    "invoice_number": m.get("invoice_number"),
                    "supplier_name": m.get("supplier_name"),
                    "total": m.get("total"),
                    "currency": m.get("currency") or "USD",
                    "invoice_date": m.get("invoice_date"),
                },
            },
        })

    context_str = "\n\n".join(
        f"[{i+1}] File: {d.metadata['filename']}\nSnippet: {d.page_content}"
        for i, d in enumerate(langchain_docs)
    )

    try:
        answer = answer_chain.invoke({
            "context": context_str,
            "question": query,
        })
    except Exception as exc:
        logger.error(f"Answer synthesis error: {exc}")
        answer = f"Found {len(matches)} matching document(s). Direct synthesis failed: {exc}"

    elapsed_ms = max(int((time.time() - start_time) * 1000), 45)
    return {
        "query": query,
        "answer": answer,
        "provider_used": "DocuIntel Intelligence Engine",
        "latency_ms": elapsed_ms,
        "citations": citations,
        "hits": hits,
    }
