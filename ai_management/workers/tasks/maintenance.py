from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from knowledge_cleaner.dify import DifyKnowledgeClient

from ...cache import cache
from ...cleaning import maintain_conversion_markdown_artifacts
from ...config import get_settings
from ...context import messages_after_summary
from ...courses import rebuild_snapshot
from ...db import SessionLocal
from ...dify import DifyAppClient
from ...model_cleaning import (
    BailianClient,
    get_provider_batch_ids,
)
from ...models import (
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    Course,
    CourseClickDaily,
    DifyPublicationShard,
    ExternalDeletionTask,
    ModelCleaningTask,
    SystemEventLog,
    ToolConversation,
    ToolMessage,
)
from ...services.admin_settings import get_history_retention_days
from ...services.bailian_cleanup import (
    clear_successful_bailian_file_tasks,
    ensure_bailian_cleanup_tasks,
)
from ...services.observability.events import record_event
from ..celery_app import celery_app
from .cleaning import (
    _finish_batch,
    _refresh_file_state,
    _remove_or_disable_document,
    process_cleaning_file,
)
from .ocr import process_ocr_cleaning_file
from .summary import _summarize_sync

logger = logging.getLogger(__name__)
settings = get_settings()
_EXTERNAL_DELETION_BATCH_LIMIT = 200
_EXTERNAL_DELETION_FETCH_LIMIT = 1000
_EXTERNAL_DELETION_PER_KIND_QUOTA = 50
_EXTERNAL_DELETION_MAX_AUTO_ATTEMPTS = 3


def _cleanup_model_review_artifacts() -> dict[str, int]:
    root = (settings.audit_root / "model-review").resolve()
    if not root.is_dir():
        return {"deleted": 0, "bytes": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.model_review_retention_days)
    with SessionLocal() as session:
        active = {
            str(item.id)
            for item in session.scalars(
                select(CleaningDocument.id).where(CleaningDocument.status == "review_required")
            )
        }
    candidates: list[tuple[Path, int, bool]] = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            data = json.loads(path.read_text(encoding="utf-8"))
            ids = {
                str(entry.get("document_id"))
                for entry in data.get("entries", [])
                if isinstance(entry, dict)
            }
            protected = bool(ids & active)
            candidates.append((path, stat.st_size, protected))
            if not protected and datetime.fromtimestamp(stat.st_mtime, timezone.utc) < cutoff:
                path.unlink()
        except (OSError, ValueError, UnicodeError):
            logger.warning("model review artifact skipped due to invalid or inaccessible content")
    remaining = [(p, size, protected) for p, size, protected in candidates if p.exists()]
    total = sum(size for _, size, _ in remaining)
    for path, size, protected in sorted(remaining, key=lambda item: item[0].stat().st_mtime):
        if total <= settings.model_review_max_bytes or protected:
            continue
        try:
            path.unlink()
            total -= size
        except OSError:
            logger.warning("model review artifact quota cleanup skipped")
    return {
        "deleted": len(candidates) - len([item for item in remaining if item[0].exists()]),
        "bytes": total,
    }


@celery_app.task(name="ai_management.cleanup_tool_conversations")
def _cleanup_tool_conversations() -> dict[str, int]:
    """Remove closed moments/writing sessions and their exact Dify records.

    This task is deliberately separate from the business history retention
    task.  A failed remote delete is retained as an exact retry item while the
    local session is still removed, so a second midnight run is idempotent.
    """
    deleted = 0
    queued = 0
    with SessionLocal() as session:
        rows = session.scalars(
            select(ToolConversation).where(ToolConversation.status == "closed").limit(200)
        ).all()
        for row in rows:
            if row.dify_conversation_id:
                try:
                    key = (
                        settings.dify_moments_app_api_key
                        if row.kind == "moments"
                        else settings.dify_writing_app_api_key
                    )
                    DifyAppClient(settings.dify_url, key).delete_conversation(
                        row.dify_conversation_id, f"u_{row.user_hash[:40]}"
                    )
                except Exception as exc:
                    exists = session.scalar(
                        select(ExternalDeletionTask).where(
                            ExternalDeletionTask.kind == "dify_tool_conversation",
                            ExternalDeletionTask.external_id == row.dify_conversation_id,
                        )
                    )
                    if exists is None:
                        session.add(
                            ExternalDeletionTask(
                                kind="dify_tool_conversation",
                                external_id=row.dify_conversation_id,
                                payload={"kind": row.kind, "user": f"u_{row.user_hash[:40]}"},
                                last_error=str(exc)[:1000],
                            )
                        )
                        queued += 1
            session.delete(row)
            deleted += 1
        # Keep the latest active session mapping for continuity, but never
        # retain local message正文 across the midnight boundary.
        active_rows = session.scalars(
            select(ToolConversation).where(ToolConversation.status == "active").limit(200)
        ).all()
        for row in active_rows:
            session.query(ToolMessage).filter(ToolMessage.conversation_id == row.id).delete()
        # Reset requests remove the local mapping immediately and leave an
        # exact external-deletion item.  Process those tool-only items in the
        # same midnight window so Dify memory follows the local retention
        # boundary instead of waiting for the business 02:00 maintenance.
        pending = session.scalars(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "dify_tool_conversation"
            )
        ).all()
        for task in pending:
            payload = task.payload or {}
            tool_kind = str(payload.get("kind") or "")
            if tool_kind not in {"moments", "writing"}:
                task.last_error = "工具会话删除任务缺少合法 kind"
                continue
            key = (
                settings.dify_moments_app_api_key
                if tool_kind == "moments"
                else settings.dify_writing_app_api_key
            )
            try:
                DifyAppClient(settings.dify_url, key).delete_conversation(
                    task.external_id, str(payload.get("user") or "")
                )
            except Exception as exc:
                task.last_error = str(exc)[:1000]
                continue
            session.delete(task)
        session.commit()
    return {"deleted": deleted, "queued": queued}


def _model_task_cleanup_file_ids(task: ModelCleaningTask) -> set[str]:
    file_ids = {
        str(file_id)
        for file_id in (
            *(task.provider_input_file_ids or []),
            *(task.provider_output_file_ids or []),
            *(task.provider_error_file_ids or []),
        )
        if file_id
    }
    terminal_batches = (task.result_counts or {}).get("provider_terminal_batches", {})
    if isinstance(terminal_batches, dict):
        for status in terminal_batches.values():
            if not isinstance(status, dict):
                continue
            for field in ("output_file_id", "error_file_id"):
                if status.get(field):
                    file_ids.add(str(status[field]))
    return file_ids


def _reconcile_model_cleanup_work(session) -> None:
    """Rebuild missing exact Bailian cleanup work after a local crash."""

    tasks = list(
        session.scalars(
            select(ModelCleaningTask)
            .where(
                ModelCleaningTask.status.in_(("cancelled", "completed", "validating")),
                ModelCleaningTask.cleanup_status != "completed",
            )
            .order_by(ModelCleaningTask.updated_at, ModelCleaningTask.id)
            .limit(_EXTERNAL_DELETION_FETCH_LIMIT)
        )
    )
    for task in tasks:
        provider_batch_ids = get_provider_batch_ids(task)
        file_ids = _model_task_cleanup_file_ids(task)
        if not provider_batch_ids and not file_ids:
            continue
        ensure_bailian_cleanup_tasks(
            session,
            task,
            provider_batch_ids=provider_batch_ids,
            provider_file_ids=file_ids,
            include_file_tasks=not provider_batch_ids,
            last_error="维护任务从本地状态重建供应商清理工作",
        )


def _select_fair_external_deletion_tasks(session) -> list[ExternalDeletionTask]:
    candidates = list(
        session.scalars(
            select(ExternalDeletionTask)
            .where(ExternalDeletionTask.next_attempt_at <= datetime.now(timezone.utc))
            .order_by(
                ExternalDeletionTask.next_attempt_at,
                ExternalDeletionTask.id,
            )
            .limit(_EXTERNAL_DELETION_FETCH_LIMIT)
        )
    )
    by_kind: dict[str, list[ExternalDeletionTask]] = {}
    for task in candidates:
        if (task.payload or {}).get("state") == "manual_intervention_required":
            continue
        by_kind.setdefault(task.kind, []).append(task)
    selected: list[ExternalDeletionTask] = []
    while len(selected) < _EXTERNAL_DELETION_BATCH_LIMIT:
        progressed = False
        for kind in sorted(by_kind):
            queue = by_kind[kind]
            if not queue:
                continue
            selected.append(queue.pop(0))
            progressed = True
            if len(selected) >= _EXTERNAL_DELETION_BATCH_LIMIT:
                break
        if not progressed:
            break
    return selected


def _record_external_deletion_failure(
    task: ExternalDeletionTask,
    error: str,
    *,
    delay: timedelta,
) -> None:
    now = datetime.now(timezone.utc)
    task.attempts += 1
    task.last_error = error[:1000]
    payload = dict(task.payload or {})
    if task.attempts >= _EXTERNAL_DELETION_MAX_AUTO_ATTEMPTS:
        payload.update(
            {
                "state": "manual_intervention_required",
                "blocked_at": now.isoformat(),
            }
        )
        task.last_error = (error[:800] + "；已暂停自动重试，需人工处理")[:1000]
        task.next_attempt_at = now + timedelta(days=3650)
    else:
        task.next_attempt_at = now + delay
    task.payload = payload


def _queue_bailian_file_task(
    session,
    *,
    file_id: str,
    model_task_id: str | None,
    last_error: str,
) -> None:
    existing = session.scalar(
        select(ExternalDeletionTask)
        .where(
            ExternalDeletionTask.kind == "bailian_file",
            ExternalDeletionTask.external_id == file_id,
        )
        .limit(1)
    )
    if existing is None:
        session.add(
            ExternalDeletionTask(
                kind="bailian_file",
                external_id=file_id,
                payload={"model_task_id": model_task_id},
                attempts=0,
                last_error=last_error[:1000],
            )
        )


def _reconcile_cleaning_pipeline() -> dict[str, int]:
    """Release orphaned cleaning/publication work before it becomes a review queue.

    This is deliberately bounded and idempotent.  It only requeues rows that
    have been unchanged for a short window, while terminalizing an exhausted
    Dify shard and refreshing its file/batch aggregate in the same transaction.
    """
    now = datetime.now(timezone.utc)
    pending_cutoff = now - timedelta(minutes=10)
    processing_cutoff = now - timedelta(minutes=45)
    queued_documents: dict[str, str] = {}
    model_batches: set[str] = set()
    shard_ids: set[str] = set()
    touched_files: set[str] = set()
    touched_batches: set[str] = set()
    fallback_count = 0
    failed_shards = 0
    with SessionLocal() as session:
        pending_documents = list(
            session.scalars(
                select(CleaningDocument)
                .where(
                    CleaningDocument.status == "pending",
                    CleaningDocument.updated_at <= pending_cutoff,
                )
                .order_by(CleaningDocument.updated_at, CleaningDocument.sequence)
                .limit(500)
            )
        )
        for document in pending_documents:
            queued_documents.setdefault(str(document.file_id), str(document.id))

        awaiting_documents = list(
            session.scalars(
                select(CleaningDocument)
                .join(CleaningFile)
                .where(
                    CleaningDocument.status == "awaiting_model",
                    CleaningDocument.updated_at <= pending_cutoff,
                )
                .limit(500)
            )
        )
        for document in awaiting_documents:
            batch_id = session.scalar(
                select(CleaningFile.batch_id).where(CleaningFile.id == document.file_id)
            )
            if batch_id:
                model_batches.add(str(batch_id))

        stale_model_documents = list(
            session.scalars(
                select(CleaningDocument)
                .join(CleaningFile)
                .where(
                    CleaningDocument.status == "model_processing",
                    CleaningDocument.updated_at <= processing_cutoff,
                )
                .limit(500)
            )
        )
        from ...cleaning import source_markdown_fallback_enabled
        from .model_cleaning import _apply_source_markdown_fallback

        for document in stale_model_documents:
            batch = session.scalar(
                select(CleaningBatch).join(CleaningFile).where(CleaningFile.id == document.file_id)
            )
            if batch is None:
                continue
            source_markdown = document.cleaned_markdown
            source_metadata = dict(document.metadata_json or {})
            source_chunking = dict(document.chunking_json or {})
            if source_markdown.strip() and source_markdown_fallback_enabled(
                batch.strategy_code, source_metadata
            ):
                document.status = "failed"
                document.error_code = "MODEL_TASK_WATCHDOG_TIMEOUT"
                document.error_message = "模型任务超时，已使用清洗源 Markdown 继续入库"
                _apply_source_markdown_fallback(
                    document,
                    batch.strategy_code,
                    source_markdown=source_markdown,
                    source_metadata=source_metadata,
                    source_chunking=source_chunking,
                )
                fallback_count += 1
            else:
                document.status = "awaiting_model"
                document.stage = "model_retry"
                document.error_code = "MODEL_TASK_WATCHDOG_TIMEOUT"
                document.error_message = "模型任务超时，已重新排队"
                document.retryable = False
                batch_id = str(batch.id)
                model_batches.add(batch_id)
            touched_files.add(str(document.file_id))
            touched_batches.add(str(batch.id))

        stale_shards = list(
            session.scalars(
                select(DifyPublicationShard)
                .where(
                    DifyPublicationShard.status == "shard_indexing",
                    (
                        DifyPublicationShard.lease_expires_at.is_(None)
                        | (DifyPublicationShard.lease_expires_at < now)
                    ),
                    DifyPublicationShard.updated_at <= processing_cutoff,
                )
                .with_for_update()
                .limit(500)
            )
        )
        for shard in stale_shards:
            # The row lock makes the query/mutation pair atomic with the
            # publisher's claim.  Recheck after waiting on a concurrent
            # publisher so a just-succeeded shard is never reset.
            if (
                shard.status != "shard_indexing"
                or shard.updated_at is None
                or _utc(shard.updated_at) > processing_cutoff
                or (shard.lease_expires_at is not None and _utc(shard.lease_expires_at) >= now)
            ):
                continue
            retryable = shard.attempts < 2
            shard.status = "shard_pending" if retryable else "failed"
            shard.last_error = (
                "Dify 分片索引超过45分钟未完成，已重新排队"
                if retryable
                else "Dify 分片索引超过45分钟未完成，重试次数已耗尽"
            )
            for document in shard.documents:
                document.status = "shard_pending" if retryable else "failed"
                document.stage = "shard_pending" if retryable else "failed"
                document.error_code = "DIFY_INDEXING_WATCHDOG_TIMEOUT"
                document.error_message = shard.last_error
                document.retryable = retryable
            touched_files.add(str(shard.file_id))
            if shard.file:
                touched_batches.add(str(shard.file.batch_id))
            if retryable:
                shard_ids.add(str(shard.id))
            else:
                failed_shards += 1

        stale_shard_documents = list(
            session.scalars(
                select(CleaningDocument)
                .where(
                    CleaningDocument.status == "shard_pending",
                    CleaningDocument.updated_at <= pending_cutoff,
                )
                .limit(500)
            )
        )
        post_batch_ids: set[str] = set()
        for document in stale_shard_documents:
            file_row = session.get(CleaningFile, document.file_id)
            if file_row:
                post_batch_ids.add(str(file_row.batch_id))
                touched_files.add(str(file_row.id))
                touched_batches.add(str(file_row.batch_id))
        if post_batch_ids:
            from .model_cleaning import _plan_post_publication

            for batch_id in post_batch_ids:
                planned = _plan_post_publication(session, batch_id)
                shard_ids.update(str(item) for item in planned)

        for file_id in touched_files:
            _refresh_file_state(session, file_id, commit=False)
        for batch_id in touched_batches:
            _finish_batch(session, batch_id)
        session.commit()

    for document_id in queued_documents.values():
        from .cleaning import process_cleaning_document

        process_cleaning_document.apply_async(args=[document_id], queue="ai_publication")
    for batch_id in model_batches:
        try:
            from .model_cleaning import maybe_plan_model_task

            maybe_plan_model_task(batch_id)
        except Exception:
            logger.exception("cleaning model reconciliation failed", extra={"batch_id": batch_id})
    for shard_id in shard_ids:
        from .model_cleaning import publish_dify_shard

        publish_dify_shard.apply_async(args=[shard_id], queue="ai_publication")
    return {
        "queued_documents": len(queued_documents),
        "model_batches": len(model_batches),
        "shards": len(shard_ids),
        "fallback_documents": fallback_count,
        "failed_shards": failed_shards,
    }


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
                select(BusinessConversation.id).where(BusinessConversation.last_active_at <= cutoff)
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
    with SessionLocal() as session:
        _reconcile_model_cleanup_work(session)
        tasks = _select_fair_external_deletion_tasks(session)
        for task in tasks:
            try:
                if task.kind == "dify_conversation":
                    client = DifyAppClient(settings.dify_url, settings.dify_business_app_api_key)
                    client.delete_conversation(
                        task.external_id, str(task.payload.get("user") or "")
                    )
                elif task.kind == "dify_tool_conversation":
                    tool_kind = str((task.payload or {}).get("kind") or "")
                    if tool_kind not in {"moments", "writing"}:
                        raise RuntimeError("工具会话删除任务缺少合法 kind")
                    key = (
                        settings.dify_moments_app_api_key
                        if tool_kind == "moments"
                        else settings.dify_writing_app_api_key
                    )
                    DifyAppClient(settings.dify_url, key).delete_conversation(
                        task.external_id, str((task.payload or {}).get("user") or "")
                    )
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
                elif task.kind == "bailian_file":
                    model_client = BailianClient(
                        api_key=settings.bailian_api_key,
                        base_url=settings.bailian_base_url,
                        timeout_seconds=60,
                    )
                    try:
                        model_client.delete_file(task.external_id)
                    finally:
                        model_client.close()
                elif task.kind == "bailian_batch":
                    model_client = BailianClient(
                        api_key=settings.bailian_api_key,
                        base_url=settings.bailian_base_url,
                        timeout_seconds=60,
                    )
                    try:
                        cancelled = model_client.cancel_batch(task.external_id)
                        provider_status = str(cancelled.get("status") or "cancelled")
                        returned_file_ids = {
                            str(cancelled.get(field) or "")
                            for field in ("output_file_id", "error_file_id")
                            if cancelled.get(field)
                        }
                        returned_file_ids.update(
                            str(file_id)
                            for file_id in (task.payload or {}).get("file_ids", [])
                            if file_id
                        )
                        if provider_status not in {"completed", "failed", "expired", "cancelled"}:
                            _record_external_deletion_failure(
                                task,
                                f"供应商 Batch 取消仍处于 {provider_status}，等待下一次终态查询",
                                delay=timedelta(minutes=10),
                            )
                            continue
                        deleted_file_ids: set[str] = set()
                        for file_id in sorted(returned_file_ids):
                            try:
                                model_client.delete_file(file_id)
                                deleted_file_ids.add(file_id)
                            except Exception as file_exc:
                                _queue_bailian_file_task(
                                    session,
                                    file_id=file_id,
                                    model_task_id=task.payload.get("model_task_id"),
                                    last_error=str(file_exc),
                                )
                        clear_successful_bailian_file_tasks(session, deleted_file_ids)
                    finally:
                        model_client.close()
                session.delete(task)
            except Exception as exc:
                _record_external_deletion_failure(
                    task,
                    str(exc),
                    delay=timedelta(days=1),
                )
                if task.payload.get("state") == "manual_intervention_required":
                    logger.error(
                        "external deletion requires manual intervention",
                        extra={
                            "external_id": task.external_id,
                            "kind": task.kind,
                        },
                    )
        session.commit()


def _flush_clicks() -> None:
    with cache.lock("ai:maintenance:click-flush", ttl=300) as lock_state:
        if not lock_state:
            return
        _flush_clicks_locked()


def _flush_clicks_locked() -> None:
    dirty = cache.dirty_click_counters()
    processed: list[tuple[str, str, int | None]] = []
    with SessionLocal() as session:
        known_courses = set(session.scalars(select(Course.course_id)))
        for day_text, course_id in dirty:
            try:
                target_day = date.fromisoformat(day_text)
            except ValueError:
                processed.append((day_text, course_id, None))
                continue
            if course_id not in known_courses:
                processed.append((day_text, course_id, None))
                continue
            clicks, users, version = cache.click_snapshot(course_id, day_text)
            if not clicks and not users:
                processed.append((day_text, course_id, version))
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
            processed.append((day_text, course_id, version))
        session.commit()
    for day_text, course_id, version in processed:
        cache.mark_click_counter_flushed(day_text, course_id, version)


def _cleanup_files_and_stale_tasks() -> None:
    now = datetime.now(timezone.utc)
    failed_cutoff = now - timedelta(hours=settings.failed_file_retention_hours)
    stale_cutoff = now - timedelta(hours=1)
    status_cutoff = now - timedelta(days=settings.cleaning_status_retention_days)
    requeue: list[tuple[str, str]] = []
    with SessionLocal() as session:
        stale = list(
            session.scalars(
                select(CleaningFile).where(
                    CleaningFile.status == "processing",
                    (
                        CleaningFile.lease_expires_at.is_(None)
                        | (CleaningFile.lease_expires_at < now)
                    ),
                    CleaningFile.updated_at <= stale_cutoff,
                )
            )
        )
        for row in stale:
            if row.attempts < 2:
                row.status = "pending"
                row.lease_token = None
                row.lease_expires_at = None
                requeue.append((str(row.id), row.original_filename))
            else:
                row.status = "failed"
                row.lease_token = None
                row.lease_expires_at = None
                row.error_code = "CLEANING_FAILED"
                row.error_message = "Worker中断后自动重试仍未完成"
            _refresh_file_state(session, row.id, commit=False)
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
                    CleaningBatch.status.in_({"completed", "failed", "review_required", "mixed"}),
                    CleaningBatch.updated_at <= status_cutoff,
                )
            )
        )
        for batch in expired_batches:
            session.delete(batch)
        session.commit()
    for file_id, original_filename in requeue:
        try:
            if Path(original_filename).suffix.lower() == ".pdf":
                process_ocr_cleaning_file.apply_async(args=[file_id], queue="ai_ocr")
            else:
                process_cleaning_file.apply_async(args=[file_id], queue="ai_cleaning")
        except Exception:
            logger.exception("stale cleaning file requeue failed", extra={"file_id": file_id})
            with SessionLocal() as session:
                row = session.get(CleaningFile, file_id)
                if row is not None and row.status == "pending":
                    row.error_code = "CLEANING_REQUEUE_FAILED"
                    row.error_message = "清洗任务重新入队失败，等待下次维护重试"
                    session.commit()

    for folder in settings.upload_root.resolve().glob("*"):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def _terminalize_stale_model_tasks() -> None:
    """Requeue orphaned model work instead of turning it into human review."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    active_states = {
        "running",
        "validating",
        "batch_uploading",
        "batch_running",
    }
    requeue_batch_ids: set[str] = set()
    poll_task_ids: set[str] = set()
    realtime_task_ids: set[str] = set()
    with SessionLocal() as session:
        tasks = list(
            session.scalars(
                select(ModelCleaningTask)
                .where(
                    ModelCleaningTask.status.in_(active_states),
                    (
                        ModelCleaningTask.lease_expires_at.is_(None)
                        | (ModelCleaningTask.lease_expires_at < datetime.now(timezone.utc))
                    ),
                    ModelCleaningTask.updated_at <= cutoff,
                )
                .with_for_update()
            )
        )
        for task in tasks:
            provider_batch_ids = get_provider_batch_ids(task)
            provider_file_ids = {
                str(file_id)
                for file_id in [
                    *(task.provider_input_file_ids or []),
                    *(task.provider_output_file_ids or []),
                    *(task.provider_error_file_ids or []),
                ]
                if file_id
            }
            terminal_output_ids: set[str] = set()
            terminal_error_ids: set[str] = set()
            terminal_batches = (task.result_counts or {}).get("provider_terminal_batches", {})
            if isinstance(terminal_batches, dict):
                provider_batch_ids.extend(
                    str(provider_batch_id)
                    for provider_batch_id in terminal_batches
                    if provider_batch_id and str(provider_batch_id) not in provider_batch_ids
                )
                for terminal_status in terminal_batches.values():
                    if not isinstance(terminal_status, dict):
                        continue
                    for field in ("output_file_id", "error_file_id"):
                        if terminal_status.get(field):
                            provider_file_ids.add(str(terminal_status[field]))
                            if field == "output_file_id":
                                terminal_output_ids.add(str(terminal_status[field]))
                            else:
                                terminal_error_ids.add(str(terminal_status[field]))

            documents = list(
                session.scalars(
                    select(CleaningDocument).where(
                        CleaningDocument.file_id.in_(
                            select(CleaningFile.id).where(CleaningFile.batch_id == task.batch_id)
                        ),
                        CleaningDocument.status.in_(["model_processing", "awaiting_model"]),
                    )
                )
            )
            if provider_batch_ids:
                # A stale local timestamp is not evidence that a provider
                # Batch is safe to cancel. Hand all known IDs to the normal
                # poll state machine, which owns mixed terminal/active
                # handling, result consumption, and eventual cleanup.
                task.provider_batch_ids = provider_batch_ids
                task.provider_batch_id = provider_batch_ids[0]
                task.provider_output_file_ids = sorted(
                    {
                        *(task.provider_output_file_ids or []),
                        *terminal_output_ids,
                    }
                )
                task.provider_error_file_ids = sorted(
                    {
                        *(task.provider_error_file_ids or []),
                        *terminal_error_ids,
                    }
                )
                task.status = "batch_running"
                task.last_error = "模型异步任务本地状态过期，已保留供应商 Batch 并交回正常轮询"
                batch = session.get(CleaningBatch, task.batch_id)
                if batch is not None:
                    batch.status = "model_batch_running"
                poll_task_ids.add(task.id)
                continue

            task.last_error = "模型任务本地状态已过期，未发现可轮询的供应商 Batch"
            for document in documents:
                document.status = "awaiting_model"
                document.stage = "model_retry"
                document.error_code = "MODEL_TASK_WATCHDOG_TIMEOUT"
                document.error_message = "模型异步任务超时，已重新排队"
                document.retryable = True
            batch = session.get(CleaningBatch, task.batch_id)
            if batch is None:
                task.status = "failed"
                task.last_error = "模型任务所属批次不存在，无法恢复"
            elif provider_file_ids or task.mode == "batch":
                # Without a provider Batch ID, keep exact file references for
                # the admin cancellation/submit recovery path. Never delete
                # output/error files merely because the local timestamp is
                # stale.
                task.status = "awaiting_approval"
                task.approval_fingerprint = ""
                task.approved_at = None
                batch.status = "awaiting_model_approval"
                task.last_error = (
                    "模型任务本地状态过期但没有可轮询的供应商 Batch；已保留文件引用并等待重新批准"
                )
            else:
                # Reuse the existing task row instead of creating a duplicate
                # candidate-fingerprint row under the unique constraint.
                task.status = "queued"
                batch.status = "model_validating"
                realtime_task_ids.add(task.id)
            continue

            had_remote_ids = bool(provider_batch_ids or provider_file_ids)
            cleanup_failed = False
            if had_remote_ids:
                model_client = BailianClient(
                    api_key=settings.bailian_api_key,
                    base_url=settings.bailian_base_url,
                    timeout_seconds=60,
                )
                try:
                    remote_statuses: dict[str, dict] = {}
                    query_failed = False
                    for provider_batch_id in provider_batch_ids:
                        try:
                            remote_status = model_client.get_batch(provider_batch_id)
                            if not str(remote_status.get("status") or ""):
                                raise ValueError("供应商 Batch 状态为空")
                            remote_statuses[provider_batch_id] = {
                                **remote_status,
                                "provider_batch_id": provider_batch_id,
                            }
                        except Exception as exc:
                            query_failed = True
                            task.provider_batch_ids = provider_batch_ids
                            task.provider_batch_id = provider_batch_ids[0]
                            task.provider_output_file_ids = sorted(
                                {
                                    *(task.provider_output_file_ids or []),
                                    *terminal_output_ids,
                                }
                            )
                            task.provider_error_file_ids = sorted(
                                {
                                    *(task.provider_error_file_ids or []),
                                    *terminal_error_ids,
                                }
                            )
                            task.last_error = (
                                "模型异步任务过期，但供应商 Batch 状态暂时无法确认；"
                                "已保留远端 ID 并安排重试：" + str(exc)[:1200]
                            )[:2000]
                            poll_task_ids.add(task.id)
                            break
                    if query_failed:
                        continue

                    terminal_summary = {
                        str(provider_batch_id): value
                        for provider_batch_id, value in terminal_batches.items()
                        if isinstance(value, dict)
                    }
                    terminal_summary.update(
                        {
                            provider_batch_id: status
                            for provider_batch_id, status in remote_statuses.items()
                            if str(status.get("status") or "")
                            in {"completed", "failed", "expired", "cancelled"}
                        }
                    )
                    completed_remote_work = any(
                        str(status.get("status") or "") == "completed"
                        for status in terminal_summary.values()
                    )
                    if (
                        completed_remote_work
                        or terminal_summary
                        and all(
                            str(status.get("status") or "")
                            in {"completed", "failed", "expired", "cancelled"}
                            for status in terminal_summary.values()
                        )
                    ):
                        output_ids = {
                            str(file_id)
                            for file_id in task.provider_output_file_ids or []
                            if file_id
                        }
                        output_ids.update(terminal_output_ids)
                        error_ids = {
                            str(file_id)
                            for file_id in task.provider_error_file_ids or []
                            if file_id
                        }
                        error_ids.update(terminal_error_ids)
                        for status in terminal_summary.values():
                            if status.get("output_file_id"):
                                output_ids.add(str(status["output_file_id"]))
                            if status.get("error_file_id"):
                                error_ids.add(str(status["error_file_id"]))
                        task.provider_batch_ids = provider_batch_ids
                        task.provider_batch_id = provider_batch_ids[0]
                        task.provider_output_file_ids = sorted(output_ids)
                        task.provider_error_file_ids = sorted(error_ids)
                        task.result_counts = {
                            **(task.result_counts or {}),
                            "provider_terminal_batches": terminal_summary,
                        }
                        task.last_error = (
                            "看门狗发现供应商 Batch 已有终态，已保留远端结果并安排正常轮询"
                        )
                        batch = session.get(CleaningBatch, task.batch_id)
                        if batch is not None:
                            batch.status = "model_batch_running"
                        poll_task_ids.add(task.id)
                        continue

                    for provider_batch_id in provider_batch_ids:
                        try:
                            cancelled = model_client.cancel_batch(provider_batch_id)
                            provider_file_ids.update(
                                str(cancelled.get(field) or "")
                                for field in ("output_file_id", "error_file_id")
                                if cancelled.get(field)
                            )
                        except Exception as exc:
                            cleanup_failed = True
                            session.add(
                                ExternalDeletionTask(
                                    kind="bailian_batch",
                                    external_id=provider_batch_id,
                                    payload={"model_task_id": task.id},
                                    attempts=0,
                                    last_error=str(exc)[:1000],
                                )
                            )
                    for file_id in sorted(provider_file_ids):
                        try:
                            model_client.delete_file(file_id)
                        except Exception as exc:
                            cleanup_failed = True
                            session.add(
                                ExternalDeletionTask(
                                    kind="bailian_file",
                                    external_id=file_id,
                                    payload={"model_task_id": task.id},
                                    attempts=0,
                                    last_error=str(exc)[:1000],
                                )
                            )
                finally:
                    model_client.close()
                task.provider_batch_ids = []
                task.provider_batch_id = None
                task.provider_input_file_ids = []
                task.provider_output_file_ids = []
                task.provider_error_file_ids = []
                task.cleanup_status = "pending_retry" if cleanup_failed else "completed"
            task.status = "failed"
            task.last_error = "模型异步任务超过6小时未更新，已由维护看门狗终止"
            documents = list(
                session.scalars(
                    select(CleaningDocument).where(
                        CleaningDocument.file_id.in_(
                            select(CleaningFile.id).where(CleaningFile.batch_id == task.batch_id)
                        ),
                        CleaningDocument.status.in_(["model_processing", "awaiting_model"]),
                    )
                )
            )
            for document in documents:
                document.status = "awaiting_model"
                document.stage = "model_retry"
                document.error_code = "MODEL_TASK_WATCHDOG_TIMEOUT"
                document.error_message = "模型异步任务超时，已重新排队"
                document.retryable = True
            # A stale task with provider IDs already submitted work externally.
            # Requeue only after every known remote resource was cancelled and
            # deleted.  A failed cleanup keeps the exact ExternalDeletionTask
            # trail and must not submit a duplicate provider Batch.
            if documents and not cleanup_failed:
                requeue_batch_ids.add(task.batch_id)
            batch = session.get(CleaningBatch, task.batch_id)
            if batch is not None:
                _finish_batch(session, batch.id)
        session.commit()
    if requeue_batch_ids:
        from .model_cleaning import maybe_plan_model_task

        for batch_id in requeue_batch_ids:
            try:
                maybe_plan_model_task(batch_id)
            except Exception:
                logger.exception(
                    "stale model task requeue failed",
                    extra={"batch_id": batch_id},
                )
    if poll_task_ids:
        from .model_cleaning import poll_batch_model_task

        for task_id in poll_task_ids:
            poll_batch_model_task.apply_async(args=[task_id], queue="ai_model_batch", countdown=0)
    if realtime_task_ids:
        from .model_cleaning import process_realtime_model_task

        for task_id in realtime_task_ids:
            process_realtime_model_task.apply_async(
                args=[task_id], queue="ai_model_realtime", countdown=0
            )


def _cleanup_system_event_logs() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.system_log_retention_days)
    with SessionLocal() as session:
        session.execute(
            delete(SystemEventLog).where(
                SystemEventLog.occurred_at < cutoff,
                ~(
                    (SystemEventLog.category == "pdf_conversion")
                    & (SystemEventLog.status == "open")
                ),
            )
        )
        session.commit()


def _cleanup_conversion_markdown() -> None:
    maintain_conversion_markdown_artifacts(delete_all=not settings.save_conversion_markdown)


def _refresh_course_snapshot() -> None:
    """Rebuild the effective recommendation catalogue from its DB authority."""
    with SessionLocal() as session:
        rebuild_snapshot(session)


@celery_app.task(name="ai_management.reconcile_cleaning_pipeline")
def reconcile_cleaning_pipeline() -> dict[str, int]:
    return _reconcile_cleaning_pipeline()


@celery_app.task(name="ai_management.nightly_maintenance")
def nightly_maintenance() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for name, function in (
        ("history", _cleanup_history),
        ("external_deletions", _retry_external_deletions),
        ("clicks", _flush_clicks),
        ("files", _cleanup_files_and_stale_tasks),
        ("model_review_artifacts", _cleanup_model_review_artifacts),
        ("model_watchdog", _terminalize_stale_model_tasks),
        ("conversion_markdown", _cleanup_conversion_markdown),
        ("system_event_logs", _cleanup_system_event_logs),
        ("course_snapshot", _refresh_course_snapshot),
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
    "_cleanup_tool_conversations",
    "_cleanup_files_and_stale_tasks",
    "_cleanup_conversion_markdown",
    "_cleanup_history",
    "_cleanup_system_event_logs",
    "_refresh_course_snapshot",
    "_flush_clicks",
    "_queue_deletion",
    "_retry_external_deletions",
    "_reconcile_cleaning_pipeline",
    "reconcile_cleaning_pipeline",
    "nightly_maintenance",
]
