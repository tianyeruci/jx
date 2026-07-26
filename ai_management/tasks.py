from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from knowledge_cleaner.dify import DifyKnowledgeClient

from .cache import cache
from .cleaning import (
    STRATEGY_REGISTRY,
    cleaning_batch_cache_key,
    dify_document_name,
)
from .config import get_settings
from .context import build_context
from .db import SessionLocal
from .dify import DifyAppClient, extract_json_object
from .models import (
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    BusinessProfile,
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    Course,
    CourseClickDaily,
    ExternalDeletionTask,
)
from .timeutil import business_today

logger = logging.getLogger(__name__)
settings = get_settings()
CONFIRMED_PROFILE_FIELDS = {
    "business_goal",
    "focus_directions",
    "experience_stage",
    "learning_status",
    "time_constraints",
    "budget_constraints",
    "confirmed_routes",
}

celery_app = Celery(
    "ai_management",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_default_queue="default",
    worker_prefetch_multiplier=1,
    beat_schedule={
        "nightly-maintenance": {
            "task": "ai_management.nightly_maintenance",
            "schedule": crontab(hour=settings.cleanup_hour, minute=settings.cleanup_minute),
        }
    },
)


def _summary_payload(response: dict[str, Any]) -> dict[str, Any] | None:
    data = response.get("data") or {}
    outputs = data.get("outputs") or response.get("outputs") or {}
    for candidate in (
        outputs.get("result"),
        outputs.get("summary_profile_json"),
        outputs,
    ):
        parsed = extract_json_object(candidate)
        if parsed:
            return parsed
    return None


def _merge_profile_payload(
    profile: BusinessProfile,
    payload: dict[str, Any],
    messages: list[BusinessMessage],
) -> None:
    user_messages = {
        item.id: item.content for item in messages if item.role == "user"
    }
    confirmed = dict(profile.confirmed or {})
    candidates = list(profile.candidates or [])
    proposed_confirmed = payload.get("confirmed_profile_updates") or []

    if isinstance(proposed_confirmed, dict):
        # Older Workflow output has no auditable evidence. Preserve it only as
        # a candidate rather than silently upgrading a model guess to fact.
        proposed_confirmed = [
            {
                "field": field,
                "value": value,
                "evidence_quote": "",
                "source_message_id": "",
                "reason": "missing_evidence",
            }
            for field, value in proposed_confirmed.items()
        ]
    if isinstance(proposed_confirmed, list):
        for item in proposed_confirmed:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            source_id = str(item.get("source_message_id") or "")
            evidence = str(item.get("evidence_quote") or "")
            value = item.get("value")
            source_text = user_messages.get(source_id, "")
            if (
                field in CONFIRMED_PROFILE_FIELDS
                and evidence
                and evidence in source_text
                and value not in (None, "", [], {})
            ):
                if field not in confirmed or confirmed[field] == value:
                    confirmed[field] = value
                    continue
                candidates.append({**item, "conflicts_with_confirmed": True})
            else:
                candidates.append({**item, "confirmation_rejected": True})

    proposed_candidates = payload.get("candidate_profile_updates") or []
    if isinstance(proposed_candidates, list):
        candidates.extend(item for item in proposed_candidates if isinstance(item, dict))
    route_candidates = payload.get("route_progress_candidates") or []
    if isinstance(route_candidates, list):
        candidates.extend(
            {"candidate_type": "route_progress", **item}
            for item in route_candidates
            if isinstance(item, dict)
        )
    profile.confirmed = confirmed
    profile.candidates = candidates[-100:]


def _summarize_sync(conversation_id: str) -> bool:
    with SessionLocal() as session:
        conversation = session.get(BusinessConversation, conversation_id)
        if conversation is None:
            return False
        old_summary = conversation.summary
        old_cursor = conversation.summary_through_message_id
        context = build_context(session, conversation, conversation.user_hash)
        if not context.unsummarized_message_ids:
            return True
        messages = list(
            session.scalars(
                select(BusinessMessage)
                .where(BusinessMessage.id.in_(context.unsummarized_message_ids))
                .order_by(BusinessMessage.created_at.asc())
            )
        )
        target_message_id = messages[-1].id
        serialized_messages = [
            {
                "message_id": item.id,
                "role": item.role,
                "content": item.content,
                "created_at": item.created_at.isoformat(),
            }
            for item in messages
        ]
        user_key = conversation.user_hash

    client = DifyAppClient(
        settings.dify_url,
        settings.dify_summary_profile_app_api_key,
    )
    response = client.run_workflow(
        inputs={
            "old_summary": old_summary,
            "messages_json": json.dumps(serialized_messages, ensure_ascii=False),
            "target_message_id": target_message_id,
        },
        user=f"summary_{user_key[:40]}",
    )
    payload = _summary_payload(response)
    if not payload or not isinstance(payload.get("updated_summary"), str):
        raise ValueError("摘要画像 Workflow 输出格式无效")

    with SessionLocal() as session:
        conversation = session.get(BusinessConversation, conversation_id)
        if conversation is None:
            return False
        if (
            conversation.summary != old_summary
            or conversation.summary_through_message_id != old_cursor
        ):
            return False
        conversation.summary = payload["updated_summary"].strip()
        conversation.summary_through_message_id = target_message_id
        conversation.summary_gap = False

        profile = session.get(BusinessProfile, user_key)
        if profile is None:
            profile = BusinessProfile(user_hash=user_key)
            session.add(profile)
        _merge_profile_payload(profile, payload, messages)
        session.commit()
        cache.delete(f"ai:summary:{conversation_id}")
        cache.delete(f"ai:profile:{user_key}")
    return True


@celery_app.task(
    name="ai_management.summarize_conversation",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def summarize_conversation(conversation_id: str) -> bool:
    return _summarize_sync(conversation_id)


def _knowledge_client(batch: CleaningBatch) -> DifyKnowledgeClient:
    dataset_id = (
        settings.dify_general_dataset_id
        if batch.knowledge_base_code == "general"
        else settings.dify_course_dataset_id
    )
    if not settings.dify_knowledge_api_key or not dataset_id:
        raise ValueError("Dify知识库 API Key 或 Dataset ID 未配置")
    return DifyKnowledgeClient(
        settings.knowledge_base_url,
        settings.dify_knowledge_api_key,
        dataset_id,
        timeout=180,
    )


def _remove_or_disable_document(
    client: DifyKnowledgeClient,
    document_id: str,
) -> bool:
    try:
        client.delete_document(document_id)
        return True
    except Exception:
        logger.warning("failed to delete Dify document %s; trying disable", document_id)
    try:
        client.disable_document(document_id)
        return True
    except Exception:
        logger.exception("failed to delete or disable Dify document %s", document_id)
        return False


def _queue_document_cleanup(document_id: str, dataset_id: str) -> None:
    with SessionLocal() as session:
        session.add(
            ExternalDeletionTask(
                kind="dify_document",
                external_id=document_id,
                payload={"dataset_id": dataset_id},
                attempts=0,
                last_error="Dify文档删除和停用均失败",
            )
        )
        session.commit()


def _set_file_state(
    file_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    with SessionLocal() as session:
        row = session.get(CleaningFile, file_id)
        if row is None:
            return
        if status is not None:
            row.status = status
        if stage is not None:
            row.stage = stage
        row.error_code = error_code
        row.error_message = error_message
        batch_id = row.batch_id
        session.commit()
        cache.delete(cleaning_batch_cache_key(batch_id))


def _finish_batch(session, batch_id: str) -> None:
    batch = session.get(CleaningBatch, batch_id)
    if batch is None:
        return
    non_terminal = session.scalar(
        select(func.count(CleaningFile.id)).where(
            CleaningFile.batch_id == batch_id,
            CleaningFile.status.in_(["pending", "processing"]),
        )
    )
    batch.status = "completed" if not non_terminal else "processing"
    cache.delete(cleaning_batch_cache_key(batch_id))


@celery_app.task(
    name="ai_management.process_cleaning_file",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def process_cleaning_file(self, file_id: str) -> bool:
    created_remote_ids: list[str] = []
    client: DifyKnowledgeClient | None = None
    target: Path | None = None
    try:
        with SessionLocal() as session:
            row = session.scalar(
                select(CleaningFile)
                .where(CleaningFile.id == file_id)
                .options(
                    selectinload(CleaningFile.batch),
                    selectinload(CleaningFile.documents),
                )
            )
            if row is None or row.status not in {"pending", "processing"}:
                return False
            batch = row.batch
            strategy = STRATEGY_REGISTRY.get(batch.strategy_code)
            if strategy is None:
                raise ValueError("清洗策略已从当前代码中移除")
            row.status = "processing"
            row.stage = "cleaning"
            row.attempts += 1
            row.error_code = None
            row.error_message = None
            target = settings.upload_root.resolve() / batch.id / row.storage_filename
            if not target.exists():
                raise FileNotFoundError("临时源文件不存在或已经过期")

            client = _knowledge_client(batch)
            for old in list(row.documents):
                if old.dify_document_id:
                    if not _remove_or_disable_document(client, old.dify_document_id):
                        _queue_document_cleanup(
                            old.dify_document_id,
                            client.dataset_id,
                        )
                session.delete(old)
            session.commit()

            documents = strategy.clean(target, row.original_filename)
            if not documents:
                raise ValueError("策略没有生成任何可入库文档")

            field_names: set[str] = set()
            prepared: list[tuple[CleaningDocument, Any, dict[str, Any]]] = []
            for sequence, document in enumerate(documents, start=1):
                if not document.markdown.strip():
                    raise ValueError(f"第 {sequence} 个清洗文档正文为空")
                source_name = (
                    f"anonymous_{row.id[:8]}.md"
                    if strategy.sensitive
                    else row.original_filename
                )
                remote_name = dify_document_name(
                    source_name,
                    row.source_sha256,
                    sequence,
                )
                metadata = {
                    **(batch.common_metadata or {}),
                    **(document.metadata or {}),
                    "source_file_id": row.id,
                    "source_extension": row.extension,
                    "source_sha256": row.source_sha256,
                    "upload_batch_id": batch.id,
                    "cleaning_strategy": batch.strategy_code,
                }
                if not strategy.sensitive:
                    metadata["source_filename"] = row.original_filename
                else:
                    metadata.pop("source_filename", None)
                metadata = {
                    key: value
                    for key, value in metadata.items()
                    if value not in (None, "", [], {})
                }
                field_names.update(metadata)
                record = CleaningDocument(
                    file_id=row.id,
                    sequence=sequence,
                    title=document.title[:300],
                    dify_document_name=remote_name,
                    metadata_json=metadata,
                )
                session.add(record)
                prepared.append((record, document, metadata))
            session.commit()

        field_ids = client.ensure_metadata_names(sorted(field_names))
        for record, document, metadata in prepared:
            _set_file_state(file_id, status="processing", stage="dify_upload")
            document_id, dify_batch = client.create_document(
                record.dify_document_name,
                document.markdown,
                parent_separator=document.parent_separator,
                parent_max_tokens=document.parent_max_tokens,
                child_separator=document.child_separator,
                child_max_tokens=document.child_max_tokens,
            )
            created_remote_ids.append(document_id)
            with SessionLocal() as session:
                current = session.get(CleaningDocument, record.id)
                current.dify_document_id = document_id
                current.dify_batch_id = dify_batch
                current.status = "processing"
                session.commit()
            _set_file_state(file_id, status="processing", stage="dify_indexing")
            client.wait_for_indexing(dify_batch, timeout_seconds=1800, interval=2)
            client.ensure_document_has_segments(document_id)
            _set_file_state(file_id, status="processing", stage="metadata")
            client.set_document_metadata(document_id, metadata, field_ids)
            with SessionLocal() as session:
                current = session.get(CleaningDocument, record.id)
                current.status = "succeeded"
                session.commit()

        with SessionLocal() as session:
            row = session.get(CleaningFile, file_id)
            row.status = "succeeded"
            row.stage = "metadata"
            row.error_code = None
            row.error_message = None
            _finish_batch(session, row.batch_id)
            session.commit()
        if target:
            target.unlink(missing_ok=True)
        return True
    except Exception as exc:
        logger.exception("cleaning file failed", extra={"file_id": file_id})
        if client:
            for document_id in created_remote_ids:
                if not _remove_or_disable_document(client, document_id):
                    _queue_document_cleanup(document_id, client.dataset_id)
        with SessionLocal() as session:
            row = session.get(CleaningFile, file_id)
            if row:
                retry = row.attempts < 2
                if retry:
                    row.status = "pending"
                    row.error_code = "CLEANING_FAILED"
                    row.error_message = str(exc)[:2000]
                else:
                    row.status = "failed"
                    row.error_code = "CLEANING_FAILED"
                    row.error_message = str(exc)[:2000]
                _finish_batch(session, row.batch_id)
                session.commit()
                if retry:
                    raise self.retry(exc=exc, countdown=10, max_retries=1)
        return False
    finally:
        if client:
            client.close()


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
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.history_retention_days)
    inactive_ids: list[str] = []
    active_with_old: list[str] = []
    skipped_ids: list[str] = []
    with SessionLocal() as session:
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
        with cache.lock(
            f"ai:conversation-lock:{conversation_id}",
            ttl=600,
        ) as acquired:
            if not acquired:
                continue
            try:
                _summarize_sync(conversation_id)
            except Exception:
                logger.exception("final summary before retention cleanup failed")
                with SessionLocal() as session:
                    conversation = session.get(BusinessConversation, conversation_id)
                    if conversation:
                        conversation.summary_gap = True
                        session.commit()
            with SessionLocal() as session:
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
    target_day = business_today() - timedelta(days=1)
    with SessionLocal() as session:
        course_ids = list(session.scalars(select(Course.course_id)))
        for course_id in course_ids:
            clicks, users = cache.click_counts(course_id, target_day.isoformat())
            if not clicks and not users:
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
        session.commit()


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
                process_cleaning_file.apply_async(args=[row.id], queue="cleaning")
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
            session.scalars(
                select(CleaningFile).where(CleaningFile.status == "succeeded")
            )
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


@celery_app.task(name="ai_management.nightly_maintenance")
def nightly_maintenance() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for name, function in (
        ("history", _cleanup_history),
        ("external_deletions", _retry_external_deletions),
        ("clicks", _flush_clicks),
        ("files", _cleanup_files_and_stale_tasks),
    ):
        try:
            function()
            results[name] = True
        except Exception:
            logger.exception("nightly maintenance step failed", extra={"step": name})
            results[name] = False
    return results
