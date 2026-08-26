from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from ..config import get_settings
from ..services.observability.logging_config import configure_logging

settings = get_settings()
configure_logging()

celery_app = Celery(
    "ai_management",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "ai_management.workers.tasks.summary",
        "ai_management.workers.tasks.cleaning",
        "ai_management.workers.tasks.ocr",
        "ai_management.workers.tasks.model_cleaning",
        "ai_management.workers.tasks.maintenance",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_default_queue="ai_default",
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    task_routes={
        "ai_management.process_ocr_cleaning_file": {"queue": "ai_ocr"},
    },
    beat_schedule={
        "nightly-maintenance": {
            "task": "ai_management.nightly_maintenance",
            "schedule": crontab(hour=settings.cleanup_hour, minute=settings.cleanup_minute),
        },
        "cleanup-tool-conversations": {
            "task": "ai_management.cleanup_tool_conversations",
            "schedule": crontab(hour=0, minute=0),
        },
        "cleaning-reconcile": {
            "task": "ai_management.reconcile_cleaning_pipeline",
            "schedule": 300,
        },
    },
)

__all__ = ["celery_app"]
