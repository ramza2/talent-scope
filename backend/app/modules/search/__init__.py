"""Search index module public exports."""

from app.modules.search.document_builder import (
    build_profile_search_document,
    build_project_search_document,
    build_search_documents_for_person,
    content_hash,
)
from app.modules.search.schemas import SEARCH_DOCUMENT_VERSION, SearchDocument
from app.modules.search.service import SearchIndexService

__all__ = [
    "SEARCH_DOCUMENT_VERSION",
    "SearchDocument",
    "SearchIndexService",
    "build_profile_search_document",
    "build_project_search_document",
    "build_search_documents_for_person",
    "content_hash",
]
