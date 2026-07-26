"""Backward-compatible Celery entry point.

Task implementations live under :mod:`ai_management.workers.tasks`.  This
module intentionally re-exports the legacy public and test-facing names so
existing imports and ``celery -A ai_management.tasks.celery_app`` keep working.
"""

from .workers.celery_app import celery_app, settings
from .workers.tasks.cleaning import (
    _finish_batch,
    _knowledge_client,
    _queue_document_cleanup,
    _remove_or_disable_document,
    _set_file_state,
    process_cleaning_file,
)
from .workers.tasks.maintenance import (
    _cleanup_files_and_stale_tasks,
    _cleanup_history,
    _cleanup_system_event_logs,
    _flush_clicks,
    _queue_deletion,
    _retry_external_deletions,
    nightly_maintenance,
)
from .workers.tasks.summary import (
    CONFIRMED_PROFILE_FIELDS,
    _merge_profile_payload,
    _summarize_sync,
    _summary_payload,
    summarize_conversation,
)

__all__ = [
    "CONFIRMED_PROFILE_FIELDS",
    "_cleanup_files_and_stale_tasks",
    "_cleanup_history",
    "_cleanup_system_event_logs",
    "_finish_batch",
    "_flush_clicks",
    "_knowledge_client",
    "_merge_profile_payload",
    "_queue_deletion",
    "_queue_document_cleanup",
    "_remove_or_disable_document",
    "_retry_external_deletions",
    "_set_file_state",
    "_summarize_sync",
    "_summary_payload",
    "celery_app",
    "nightly_maintenance",
    "process_cleaning_file",
    "settings",
    "summarize_conversation",
]
