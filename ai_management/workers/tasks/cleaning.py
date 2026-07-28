from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from knowledge_cleaner.dify import DifyKnowledgeClient

from ...cache import cache
from ...cleaning import STRATEGY_REGISTRY, cleaning_batch_cache_key, dify_document_name
from ...config import get_settings
from ...db import SessionLocal
from ...models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    ExternalDeletionTask,
)
from ...services.observability.events import record_event
from ..celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


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
    replacement_document_id = ""
    client: DifyKnowledgeClient | None = None
    target: Path | None = None
    try:
        upload_mode = "cleaned"
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
            upload_mode = batch.upload_mode
            replacement_document_id = row.replace_document_id or ""
            strategy = STRATEGY_REGISTRY.get(batch.strategy_code)
            if upload_mode != "raw" and strategy is None:
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
            # Retry cleanup only documents created by an incomplete earlier
            # attempt.  Do not delete a completed document before its
            # replacement has indexed successfully.
            for old in list(row.documents):
                if old.status != "succeeded" and old.dify_document_id:
                    if not _remove_or_disable_document(client, old.dify_document_id):
                        _queue_document_cleanup(old.dify_document_id, client.dataset_id)
                if old.status != "succeeded":
                    session.delete(old)
            session.commit()

            if upload_mode == "raw":
                from ...cleaning import CleanedDocument

                documents = [
                    CleanedDocument(
                        markdown="__raw_upload__",
                        title=row.original_filename,
                        metadata={"document_type": "raw_upload", "review_status": "approved"},
                    )
                ]
            else:
                assert strategy is not None
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
                    if strategy and strategy.sensitive
                    else row.original_filename
                )
                remote_name = (
                    source_name if upload_mode == "raw" else dify_document_name(source_name, row.source_sha256, sequence)
                )
                metadata = {
                    **(batch.common_metadata or {}),
                    **(document.metadata or {}),
                    "source_file_id": row.id,
                    "source_extension": row.extension,
                    "source_sha256": row.source_sha256,
                    "upload_batch_id": batch.id,
                    "cleaning_strategy": batch.strategy_code,
                    "source_document_index": sequence,
                    "build_version": batch.build_version,
                }
                if not strategy or not strategy.sensitive:
                    metadata["source_filename"] = row.original_filename
                else:
                    metadata.pop("source_filename", None)
                metadata = {
                    key: value
                    for key, value in metadata.items()
                    if value not in (None, "", [], {})
                }
                allowed_remote_fields = getattr(strategy, "dify_metadata_fields", None) if strategy else None
                remote_metadata = (
                    {
                        key: value
                        for key, value in metadata.items()
                        if key in allowed_remote_fields
                    }
                    if allowed_remote_fields is not None
                    else metadata
                )
                if not remote_metadata:
                    raise ValueError(f"第 {sequence} 个清洗文档没有可写入Dify的Metadata")
                field_names.update(remote_metadata)
                record = CleaningDocument(
                    file_id=row.id,
                    sequence=sequence,
                    title=document.title[:300],
                    dify_document_name=remote_name,
                    metadata_json=metadata,
                )
                session.add(record)
                prepared.append((record, document, remote_metadata))
            session.commit()

        field_ids = client.ensure_metadata_names(sorted(field_names))
        for record, document, metadata in prepared:
            _set_file_state(file_id, status="processing", stage="dify_upload")
            if upload_mode == "raw":
                document_id, dify_batch = client.create_document_by_file(target)
            else:
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
            client.ensure_document_has_child_chunks(document_id)
            _set_file_state(file_id, status="processing", stage="metadata")
            client.set_document_metadata(document_id, metadata, field_ids)
            with SessionLocal() as session:
                current = session.get(CleaningDocument, record.id)
                current.status = "succeeded"
                session.commit()

        if replacement_document_id and replacement_document_id not in created_remote_ids:
            # New content is fully indexed and has metadata at this point.
            # Only now remove the old document, so a failed replacement never
            # destroys the last known-good version.
            if not _remove_or_disable_document(client, replacement_document_id):
                raise RuntimeError("新文档已索引，但旧同名文档无法删除或停用")

        with SessionLocal() as session:
            row = session.get(CleaningFile, file_id)
            row.status = "succeeded"
            row.stage = "completed"
            row.error_code = None
            row.error_message = None
            row.replace_document_id = None
            _finish_batch(session, row.batch_id)
            session.commit()
        if target:
            target.unlink(missing_ok=True)
        record_event(
            level="INFO",
            category="task",
            component="cleaning_worker",
            event_code="CLEANING_FILE_COMPLETED",
            status="succeeded",
            message="清洗文件任务完成",
            resource_type="cleaning_file",
            resource_id=file_id,
        )
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
                    record_event(
                        level="WARNING",
                        category="task",
                        component="cleaning_worker",
                        event_code="CLEANING_FILE_RETRYING",
                        status="retrying",
                        message="清洗文件任务失败，等待自动重试",
                        resource_type="cleaning_file",
                        resource_id=file_id,
                        details={"error_type": type(exc).__name__, "attempt": row.attempts},
                    )
                    raise self.retry(exc=exc, countdown=10, max_retries=1)
        record_event(
            level="ERROR",
            category="task",
            component="cleaning_worker",
            event_code="CLEANING_FILE_FAILED",
            status="failed",
            message="清洗文件任务失败",
            resource_type="cleaning_file",
            resource_id=file_id,
            details={"error_type": type(exc).__name__},
        )
        return False
    finally:
        if client:
            client.close()


__all__ = [
    "_finish_batch",
    "_knowledge_client",
    "_queue_document_cleanup",
    "_remove_or_disable_document",
    "_set_file_state",
    "process_cleaning_file",
]
