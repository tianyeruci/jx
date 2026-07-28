from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from knowledge_cleaner.dify import DifyKnowledgeClient

from ...cache import cache
from ...config import get_settings
from ...context import messages_after_summary
from ...db import SessionLocal
from ...dify import DifyAppClient
from ...models import (
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    CleaningBatch,
    CleaningFile,
    Course,
    CourseClickDaily,
    ExternalDeletionTask,
    SystemEventLog,
)
from ...services.admin_settings import get_history_retention_days
from ...services.observability.events import record_event
from ..celery_app import celery_app
from .cleaning import _finish_batch, _remove_or_disable_document, process_cleaning_file
from .summary import _summarize_sync

logger = logging.getLogger(__name__)
settings = get_settings()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _queue_deletion(
    session,
    *,
    external_id: str,
    anonymous_user: str,
    last_error: str = "",
) -> None:
    session.add(
        ExternalDeletionTask(
            kind="dify_conversation",
            external_id=external_id,
            payload={"user": anonymous_user},
            attempts=0,
            last_error=last_error[:1000],
        )
    )


def _cleanup_history() -> None:
    inactive_ids: list[str] = []
    active_with_old: list[str] = []
    skipped_ids: list[str] = []
    with SessionLocal() as session:
        retention_days = get_history_retention_days(session)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        inactive_ids = list(
            session.scalars(
                select(BusinessConversation.id).where(
                    BusinessConversation.last_active_at <= cutoff
                )
            )
        )
        active_with_old = list(
            session.scalars(
                select(BusinessConversation.id)
                .join(BusinessMessage)
                .where(
                    BusinessConversation.last_active_at > cutoff,
                    BusinessMessage.created_at <= cutoff,
                )
                .distinct()
            )
        )

    for conversation_id in active_with_old:
        with cache.lock(f"ai:conversation-lock:{conversation_id}", ttl=600) as acquired:
            if not acquired:
                continue
            summary_ok = False
            try:
                summary_ok = _summarize_sync(conversation_id)
            except Exception:
                logger.exception("final summary before retention cleanup failed")
            if not summary_ok:
                with SessionLocal() as session:
                    conversation = session.get(BusinessConversation, conversation_id)
                    if conversation:
                        conversation.summary_gap = True
                        session.commit()
                continue
            with SessionLocal() as session:
                conversation = session.get(BusinessConversation, conversation_id)
                if conversation is None:
                    continue
                remaining_old = any(
                    _utc(message.created_at) <= cutoff
                    for message in messages_after_summary(session, conversation, limit=1)
                )
                if remaining_old:
                    conversation.summary_gap = True
                    session.commit()
                    continue
                session.execute(
                    delete(BusinessMessage).where(
                        BusinessMessage.conversation_id == conversation_id,
                        BusinessMessage.created_at <= cutoff,
                    )
                )
                session.commit()

    with SessionLocal() as session:
        for conversation_id in inactive_ids:
            conversation = session.scalar(
                select(BusinessConversation)
                .where(BusinessConversation.id == conversation_id)
                .options(selectinload(BusinessConversation.segments))
            )
            if conversation is None:
                continue
            with cache.lock(f"ai:conversation-lock:{conversation.id}", ttl=600) as acquired:
                if not acquired:
                    skipped_ids.append(conversation.id)
                    continue
                for segment in conversation.segments:
                    _queue_deletion(
                        session,
                        external_id=segment.dify_conversation_id,
                        anonymous_user=segment.dify_user,
                    )
                session.delete(conversation)

        expired_query = select(BusinessConversationSegment).where(
            BusinessConversationSegment.last_message_at <= cutoff
        )
        if skipped_ids:
            expired_query = expired_query.where(
                BusinessConversationSegment.conversation_id.not_in(skipped_ids)
            )
        expired_segments = list(session.scalars(expired_query))
        for segment in expired_segments:
            _queue_deletion(
                session,
                external_id=segment.dify_conversation_id,
                anonymous_user=segment.dify_user,
            )
            session.delete(segment)
        session.commit()


def _retry_external_deletions() -> None:
    client = DifyAppClient(settings.dify_url, settings.dify_business_app_api_key)
    with SessionLocal() as session:
        tasks = list(
            session.scalars(
                select(ExternalDeletionTask)
                .where(ExternalDeletionTask.next_attempt_at <= datetime.now(timezone.utc))
                .limit(200)
            )
        )
        for task in tasks:
            try:
                if task.kind == "dify_conversation":
                    client.delete_conversation(task.external_id, str(task.payload.get("user") or ""))
                elif task.kind == "dify_document":
                    dataset_id = str(task.payload.get("dataset_id") or "")
                    knowledge = DifyKnowledgeClient(
                        settings.knowledge_base_url,
                        settings.dify_knowledge_api_key,
                        dataset_id,
                        timeout=60,
                    )
                    try:
                        if not _remove_or_disable_document(knowledge, task.external_id):
                            raise RuntimeError("Dify文档删除和停用仍然失败")
                    finally:
                        knowledge.close()
                session.delete(task)
            except Exception as exc:
                task.attempts += 1
                task.last_error = str(exc)[:1000]
                task.next_attempt_at = datetime.now(timezone.utc) + timedelta(days=1)
                if task.attempts >= 3:
                    logger.error(
                        "external deletion failed three times",
                        extra={"external_id": task.external_id},
                    )
        session.commit()


def _flush_clicks() -> None:
    dirty = cache.dirty_click_counters()
    processed: list[tuple[str, str]] = []
    with SessionLocal() as session:
        known_courses = set(session.scalars(select(Course.course_id)))
        for day_text, course_id in dirty:
            try:
                target_day = date.fromisoformat(day_text)
            except ValueError:
                processed.append((day_text, course_id))
                continue
            if course_id not in known_courses:
                processed.append((day_text, course_id))
                continue
            clicks, users = cache.click_counts(course_id, day_text)
            if not clicks and not users:
                processed.append((day_text, course_id))
                continue
            row = session.scalar(
                select(CourseClickDaily).where(
                    CourseClickDaily.course_id == course_id,
                    CourseClickDaily.click_date == target_day,
                )
            )
            if row is None:
                row = CourseClickDaily(course_id=course_id, click_date=target_day)
                session.add(row)
            row.clicks = clicks
            row.unique_users = users
            processed.append((day_text, course_id))
        session.commit()
    for day_text, course_id in processed:
        cache.mark_click_counter_flushed(day_text, course_id)


def _cleanup_files_and_stale_tasks() -> None:
    now = datetime.now(timezone.utc)
    failed_cutoff = now - timedelta(hours=settings.failed_file_retention_hours)
    stale_cutoff = now - timedelta(hours=1)
    status_cutoff = now - timedelta(days=settings.cleaning_status_retention_days)
    with SessionLocal() as session:
        stale = list(
            session.scalars(
                select(CleaningFile).where(
                    CleaningFile.status == "processing",
                    CleaningFile.updated_at <= stale_cutoff,
                )
            )
        )
        for row in stale:
            if row.attempts < 2:
                row.status = "pending"
                process_cleaning_file.apply_async(args=[row.id], queue="ai_cleaning")
            else:
                row.status = "failed"
                row.error_code = "CLEANING_FAILED"
                row.error_message = "Worker中断后自动重试仍未完成"
            _finish_batch(session, row.batch_id)

        expired_failed = list(
            session.scalars(
                select(CleaningFile).where(
                    CleaningFile.status == "failed",
                    CleaningFile.updated_at <= failed_cutoff,
                )
            )
        )
        for row in expired_failed:
            target = settings.upload_root.resolve() / row.batch_id / row.storage_filename
            target.unlink(missing_ok=True)
        completed_files = list(
            session.scalars(select(CleaningFile).where(CleaningFile.status == "succeeded"))
        )
        for row in completed_files:
            target = settings.upload_root.resolve() / row.batch_id / row.storage_filename
            target.unlink(missing_ok=True)
        expired_batches = list(
            session.scalars(
                select(CleaningBatch).where(
                    CleaningBatch.status == "completed",
                    CleaningBatch.updated_at <= status_cutoff,
                )
            )
        )
        for batch in expired_batches:
            session.delete(batch)
        session.commit()

    for folder in settings.upload_root.resolve().glob("*"):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def _cleanup_system_event_logs() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.system_log_retention_days)
    with SessionLocal() as session:
        session.execute(delete(SystemEventLog).where(SystemEventLog.occurred_at < cutoff))
        session.commit()


@celery_app.task(name="ai_management.nightly_maintenance")
def nightly_maintenance() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for name, function in (
        ("history", _cleanup_history),
        ("external_deletions", _retry_external_deletions),
        ("clicks", _flush_clicks),
        ("files", _cleanup_files_and_stale_tasks),
        ("system_event_logs", _cleanup_system_event_logs),
    ):
        try:
            function()
            results[name] = True
            record_event(
                level="INFO",
                category="task",
                component="maintenance_worker",
                event_code=f"MAINTENANCE_{name.upper()}_COMPLETED",
                status="succeeded",
                message=f"夜间维护步骤完成：{name}",
                resource_type="maintenance_step",
                resource_id=name,
            )
        except Exception as exc:
            logger.exception("nightly maintenance step failed", extra={"step": name})
            results[name] = False
            record_event(
                level="ERROR",
                category="task",
                component="maintenance_worker",
                event_code=f"MAINTENANCE_{name.upper()}_FAILED",
                status="failed",
                message=f"夜间维护步骤失败：{name}",
                resource_type="maintenance_step",
                resource_id=name,
                details={"error_type": type(exc).__name__},
            )
    return results


__all__ = [
    "_cleanup_files_and_stale_tasks",
    "_cleanup_history",
    "_cleanup_system_event_logs",
    "_flush_clicks",
    "_queue_deletion",
    "_retry_external_deletions",
    "nightly_maintenance",
]
