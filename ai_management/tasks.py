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
    _refresh_file_state,
    _remove_or_disable_document,
    _set_document_state,
    _set_file_state,
    process_cleaning_document,
    process_cleaning_file,
)
from .workers.tasks.maintenance import (
    _cleanup_files_and_stale_tasks,
    _cleanup_history,
    _cleanup_system_event_logs,
    _cleanup_tool_conversations,
    _flush_clicks,
    _queue_deletion,
    _retry_external_deletions,
    nightly_maintenance,
)
from .workers.tasks.model_cleaning import (
    maybe_plan_model_task,
    poll_batch_model_task,
    process_realtime_model_task,
    publish_dify_shard,
    submit_batch_model_task,
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
    "_refresh_file_state",
    "_remove_or_disable_document",
    "_set_document_state",
    "_retry_external_deletions",
    "_set_file_state",
    "_summarize_sync",
    "_summary_payload",
    "celery_app",
    "nightly_maintenance",
    "_cleanup_tool_conversations",
    "maybe_plan_model_task",
    "poll_batch_model_task",
    "process_cleaning_document",
    "process_cleaning_file",
    "process_realtime_model_task",
    "publish_dify_shard",
    "settings",
    "summarize_conversation",
    "submit_batch_model_task",
]
