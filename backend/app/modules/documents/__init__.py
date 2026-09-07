"""Documents module public exports."""

from app.modules.documents.router import (
    document_groups_router,
    documents_router,
    person_documents_router,
    upload_sessions_router,
)

__all__ = [
    "upload_sessions_router",
    "documents_router",
    "document_groups_router",
    "person_documents_router",
]
