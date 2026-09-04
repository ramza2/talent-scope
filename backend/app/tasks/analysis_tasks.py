"""Analysis queue task skeletons — not implemented yet."""

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.analysis_tasks.placeholder_analysis_task")
def placeholder_analysis_task(analysis_run_id: str) -> dict[str, str]:
    """Skeleton only. VLM/LLM/diff pipeline will be implemented later."""
    return {"status": "not_implemented", "analysis_run_id": analysis_run_id}
