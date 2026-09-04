"""Document queue task skeletons — not implemented yet."""

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.document_tasks.placeholder_document_task")
def placeholder_document_task(document_id: str) -> dict[str, str]:
    """Skeleton only. Document convert/extract/chunk will be implemented later."""
    return {"status": "not_implemented", "document_id": document_id}
