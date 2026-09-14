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
    # Dispatcher only publishes PENDING jobs; SearchIndexJob DB remains SoT.
    # Beat interval ~15s keeps Confirm→index lag short without Confirm TX .delay().
    # Dispatcher atomically reserves PENDING→PROCESSING then publishes.
    # Stale PROCESSING recovery returns stuck reservations to PENDING (~60s).
    # SearchIndexJob DB remains SoT; Confirm TX never calls .delay().
    beat_schedule={
        "dispatch-pending-search-index-jobs": {
            "task": "app.tasks.index_tasks.dispatch_pending_search_index_jobs",
            "schedule": 15.0,
            "kwargs": {"limit": 50},
        },
        "recover-stale-search-index-jobs": {
            "task": "app.tasks.index_tasks.recover_stale_search_index_jobs",
            "schedule": 60.0,
            "kwargs": {"limit": 100},
        },
        "enqueue-missing-search-embeddings": {
            "task": "app.tasks.index_tasks.enqueue_missing_search_embeddings",
            "schedule": 60.0,
            "kwargs": {"limit": 100},
        },
        "enqueue-missing-document-chunk-sync-jobs": {
            "task": "app.tasks.index_tasks.enqueue_missing_document_chunk_sync_jobs",
            "schedule": 60.0,
            "kwargs": {"limit": 100},
        },
    },
)
