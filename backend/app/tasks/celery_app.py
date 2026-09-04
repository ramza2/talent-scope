"""Celery application skeleton.

Queues: document, analysis, index.
Redis is used as broker only — not as business-state source of truth.
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "talentscope",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.document_tasks",
        "app.tasks.analysis_tasks",
        "app.tasks.index_tasks",
    ],
)

celery_app.conf.update(
    task_default_queue="document",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Results are operational diagnostics only — never business SoT.
    result_expires=3600,
    task_routes={
        "app.tasks.document_tasks.*": {"queue": "document"},
        "app.tasks.analysis_tasks.*": {"queue": "analysis"},
        "app.tasks.index_tasks.*": {"queue": "index"},
    },
)
