"""Models and repositories package."""
from app.models.document_repo import (
    save_document,
    get_document,
    list_documents,
    delete_document,
    delete_all_documents,
    add_history,
    get_history,
    get_stats,
)
from app.models.result_repo import (
    save_extraction_result,
    get_extraction_results,
    update_result_correction,
)
from app.models.embedding_repo import (
    save_document_embedding,
    search_document_embeddings,
)
