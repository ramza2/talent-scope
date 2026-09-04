"""Index queue task skeletons — not implemented yet."""

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.index_tasks.placeholder_index_task")
def placeholder_index_task(person_id: str) -> dict[str, str]:
    """Skeleton only. Search index / embedding rebuild will be implemented later."""
    return {"status": "not_implemented", "person_id": person_id}
