from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from celery import chain
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from knowledge_cleaner.dify import DifyKnowledgeClient

from ...cache import cache
from ...course_import import sync_cleaning_catalog_record
from ...courses import rebuild_snapshot
from ...cleaning import (
    ChunkingConfig,
    STRATEGY_REGISTRY,
    cleaning_batch_cache_key,
    get_settings,
)
from ...db import SessionLocal
from ...models import CleaningBatch, CleaningDocument, CleaningFile, ExternalDeletionTask, utcnow
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


def _remove_or_disable_document(client: DifyKnowledgeClient, document_id: str) -> bool:
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


def _chunking_from_json(payload: dict[str, Any] | None) -> ChunkingConfig:
    if not payload:
        return ChunkingConfig()
    return ChunkingConfig(
        doc_form=str(payload.get("doc_form") or "hierarchical_model"),
        process_mode=str(payload.get("process_mode") or "hierarchical"),
        parent_mode=str(payload.get("parent_mode") or "paragraph"),
        parent_separator=payload.get("parent_separator"),
        parent_max_tokens=int(payload.get("parent_max_tokens") or 1000),
        child_separator=payload.get("child_separator"),
        child_max_tokens=int(payload.get("child_max_tokens") or 350),
        parent_chunk_overlap=int(payload.get("parent_chunk_overlap") or 0),
        child_chunk_overlap=int(payload.get("child_chunk_overlap") or 0),
    )


def _content_sha(markdown: str, metadata: dict[str, Any], chunking: dict[str, Any]) -> str:
    payload = {
        "markdown": markdown,
        "metadata": metadata,
        "chunking": chunking,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _course_document_publish_order(documents: list[CleaningDocument]) -> list[str]:
    """Order one uploaded course file so same-file prerequisites publish first.

    Dify indexing is intentionally asynchronous, but database catalogue writes
    must observe prerequisite records.  A Celery chain gives that ordering
    without inventing a route or mutating the course content.  Invalid cycles
    and unknown prerequisites retain their original order and are rejected by
    the normal catalogue validation with a user-visible reason.
    """

    entries: dict[str, tuple[CleaningDocument, set[str]]] = {}
    for document in sorted(documents, key=lambda item: item.sequence):
        payload = (document.audit_json or {}).get("catalog_record")
        course_id = payload.get("course_id") if isinstance(payload, dict) else ""
        if not isinstance(course_id, str) or not course_id or course_id in entries:
            continue
        prerequisites = payload.get("prerequisite_course_ids", [])
        entries[course_id] = (
            document,
            {str(item) for item in prerequisites if str(item).strip()},
        )

    unresolved = set(entries)
    ordered_ids: list[str] = []
    while unresolved:
        ready = sorted(
            (
                course_id
                for course_id in unresolved
                if not (entries[course_id][1] & unresolved)
            ),
            key=lambda course_id: entries[course_id][0].sequence,
        )
        if not ready:
            # Leave invalid dependency graphs to the existing strict validator.
            ordered_ids.extend(
                entries[course_id][0].id
                for course_id in sorted(unresolved, key=lambda item: entries[item][0].sequence)
            )
            break
        for course_id in ready:
            unresolved.remove(course_id)
            ordered_ids.append(entries[course_id][0].id)

    known_ids = {document.id for document in documents}
    ordered_set = set(ordered_ids)
    # Documents without a usable catalogue record still go through the normal
    # failure path after the well-formed records, preserving source order.
    ordered_ids.extend(
        document.id
        for document in sorted(documents, key=lambda item: item.sequence)
        if document.id in known_ids and document.id not in ordered_set
    )
    return ordered_ids


def _finish_batch(session, batch_id: str) -> None:
    batch = session.scalar(
        select(CleaningBatch)
        .where(CleaningBatch.id == batch_id)
        .options(selectinload(CleaningBatch.files).selectinload(CleaningFile.documents))
    )
    if batch is None:
        return
    statuses = []
    for file_row in batch.files:
        statuses.append(_refresh_file_state(session, file_row.id, commit=False))
    batch.status = "completed" if statuses and all(status in {"succeeded", "excluded", "review_required", "failed", "mixed"} for status in statuses) else "processing"
    cache.delete(cleaning_batch_cache_key(batch_id))


def _refresh_file_state(session, file_id: str, *, commit: bool = True) -> str:
    row = session.get(CleaningFile, file_id)
    if row is None:
        return "failed"
    documents = list(
        session.scalars(
            select(CleaningDocument)
            .where(CleaningDocument.file_id == file_id)
            .order_by(CleaningDocument.sequence)
        )
    )
    if not documents:
        if row.status in {"failed", "review_required", "succeeded", "excluded"}:
            row.outcome = row.status
            if row.status in {"succeeded", "excluded"} and row.source_deleted_at is None:
                row.source_deleted_at = utcnow()
        elif row.status == "processing":
            row.outcome = "processing"
        if commit:
            cache.delete(cleaning_batch_cache_key(row.batch_id))
            session.commit()
        return row.outcome or row.status

    pending = sum(item.status == "pending" for item in documents)
    processing = sum(item.status == "processing" for item in documents)
    succeeded = sum(item.status == "succeeded" for item in documents)
    excluded = sum(item.status == "excluded" for item in documents)
    review_required = sum(item.status == "review_required" for item in documents)
    failed = sum(item.status == "failed" for item in documents)
    total = len(documents)
    if pending or processing:
        row.status = "processing"
        row.outcome = "processing"
        row.stage = "publishing"
    elif succeeded == total:
        row.status = "succeeded"
        row.outcome = "succeeded"
        row.stage = "completed"
    elif excluded == total:
        row.status = "excluded"
        row.outcome = "excluded"
        row.stage = "completed"
    elif review_required == total:
        row.status = "review_required"
        row.outcome = "review_required"
        row.stage = "review"
    elif failed == total:
        row.status = "failed"
        row.outcome = "failed"
        row.stage = "failed"
    else:
        row.status = "mixed"
        row.outcome = "mixed"
        row.stage = "completed"
    if row.outcome in {"succeeded", "excluded"}:
        target = settings.upload_root.resolve() / row.batch_id / row.storage_filename
        target.unlink(missing_ok=True)
        row.source_deleted_at = utcnow()
    if commit:
        cache.delete(cleaning_batch_cache_key(row.batch_id))
        session.commit()
    return row.outcome


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
        _finish_batch(session, row.batch_id)
        session.commit()


def _set_document_state(
    document_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool | None = None,
) -> None:
    with SessionLocal() as session:
        row = session.get(CleaningDocument, document_id)
        if row is None:
            return
        if status is not None:
            row.status = status
        if stage is not None:
            row.stage = stage
        row.error_code = error_code
        row.error_message = error_message
        if retryable is not None:
            row.retryable = retryable
        _refresh_file_state(session, row.file_id, commit=False)
        _finish_batch(session, row.file.batch_id)
        session.commit()


def _publish_document(session, row: CleaningDocument, batch: CleaningBatch, file_row: CleaningFile) -> bool:
    client: DifyKnowledgeClient | None = None
    created_remote_id = ""
    replaced_catalog_document_id = ""
    try:
        client = _knowledge_client(batch)
        row.status = "processing"
        row.stage = "dify_upload"
        row.attempts += 1
        row.error_code = None
        row.error_message = None
        session.commit()
        if row.dify_document_id:
            if not _remove_or_disable_document(client, row.dify_document_id):
                _queue_document_cleanup(row.dify_document_id, client.dataset_id)
            row.dify_document_id = None
            row.dify_batch_id = None
            session.commit()

        if batch.upload_mode == "raw":
            target = settings.upload_root.resolve() / batch.id / file_row.storage_filename
            document_id, dify_batch_id = client.create_document_by_file(target)
            expected_parent_count = 0
            expected_child_counts: list[int] = []
        else:
            chunking = _chunking_from_json(row.chunking_json)
            document_id, dify_batch_id = client.create_document(
                row.dify_document_name,
                row.cleaned_markdown,
                doc_form=chunking.doc_form,
                process_mode=chunking.process_mode,
                parent_mode=chunking.parent_mode,
                parent_separator=chunking.parent_separator,
                parent_max_tokens=chunking.parent_max_tokens,
                child_separator=chunking.child_separator,
                child_max_tokens=chunking.child_max_tokens,
                parent_chunk_overlap=chunking.parent_chunk_overlap,
                child_chunk_overlap=chunking.child_chunk_overlap,
            )
            expected_parent_count = 1 if chunking.parent_mode == "full-doc" else None
            expected_child_counts = []
        created_remote_id = document_id
        row.dify_document_id = document_id
        row.dify_batch_id = dify_batch_id
        row.stage = "dify_indexing"
        session.commit()

        client.wait_for_indexing(dify_batch_id, timeout_seconds=1800, interval=2)
        structure = client.verify_document_structure(
            document_id,
            parent_mode=_chunking_from_json(row.chunking_json).parent_mode if batch.upload_mode != "raw" else "paragraph",
            expected_parent_count=expected_parent_count,
            expected_child_counts=expected_child_counts or None,
        )
        row.remote_parent_count = int(structure["parent_count"])
        row.remote_child_count = int(structure["child_count"])
        row.stage = "metadata"
        session.commit()

        allowed_remote_fields = getattr(STRATEGY_REGISTRY.get(batch.strategy_code), "dify_metadata_fields", None)
        remote_metadata = {
            key: value
            for key, value in (row.metadata_json or {}).items()
            if allowed_remote_fields is None or key in allowed_remote_fields
        }
        field_ids = client.ensure_metadata_names(sorted(remote_metadata))
        client.set_document_metadata(document_id, remote_metadata, field_ids)
        client.verify_document_metadata(document_id, remote_metadata)
        if batch.strategy_code in {"course_information", "learning_route"}:
            payload = (row.audit_json or {}).get("catalog_record")
            if not isinstance(payload, dict):
                raise ValueError("课程目录清洗结果缺少受控结构化记录")
            replaced_catalog_document_id = sync_cleaning_catalog_record(
                session,
                strategy_code=batch.strategy_code,
                payload=payload,
                dify_document_id=document_id,
                source_file=file_row.original_filename,
            )
        row.status = "succeeded"
        row.stage = "completed"
        row.retryable = False
        session.commit()
        if batch.strategy_code in {"course_information", "learning_route"}:
            # DB is authoritative.  Rebuild the effective cache only after its
            # transaction succeeds; an old Dify copy is then safe to retire.
            try:
                rebuild_snapshot(session)
            except Exception:
                # The next scheduled rebuild repairs the cache.  Do not turn a
                # committed catalogue/Dify publish into a destructive retry.
                logger.exception("course snapshot rebuild failed after catalogue commit")
            if replaced_catalog_document_id and replaced_catalog_document_id != document_id:
                if not _remove_or_disable_document(client, replaced_catalog_document_id):
                    _queue_document_cleanup(replaced_catalog_document_id, client.dataset_id)
        return True
    except Exception as exc:
        logger.exception("cleaning document publish failed", extra={"document_id": row.id})
        if client and created_remote_id:
            if not _remove_or_disable_document(client, created_remote_id):
                _queue_document_cleanup(created_remote_id, client.dataset_id)
        retry = row.attempts < 2
        row.status = "pending" if retry else "failed"
        row.stage = "retrying" if retry else "failed"
        row.error_code = "CLEANING_FAILED"
        row.error_message = str(exc)[:2000]
        row.retryable = not retry
        session.commit()
        if retry:
            raise
        return False
    finally:
        if client:
            client.close()


@celery_app.task(
    name="ai_management.process_cleaning_file",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def process_cleaning_file(self, file_id: str) -> bool:
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
            if batch.upload_mode != "raw" and strategy is None:
                raise ValueError("清洗策略已从当前代码中移除")
            target = settings.upload_root.resolve() / batch.id / row.storage_filename
            if not target.exists():
                raise FileNotFoundError("临时源文件不存在或已经过期")

            row.status = "processing"
            row.outcome = "processing"
            row.stage = "cleaning"
            row.attempts += 1
            row.error_code = None
            row.error_message = None
            for existing in list(row.documents):
                if existing.status != "succeeded":
                    session.delete(existing)
            session.commit()

            if batch.upload_mode == "raw":
                from ...cleaning import CleanedDocument

                cleaned_documents = [
                    CleanedDocument(
                        markdown="__raw_upload__",
                        title=row.original_filename,
                        metadata={"document_type": "raw_upload", "review_status": "approved"},
                    )
                ]
            else:
                assert strategy is not None
                cleaned_documents = strategy.clean(target, row.original_filename)
            if not cleaned_documents:
                raise ValueError("策略没有生成任何可入库文档")

            for sequence, document in enumerate(cleaned_documents, start=1):
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
                if not getattr(strategy, "sensitive", False):
                    metadata["source_filename"] = row.original_filename
                metadata = {
                    key: value
                    for key, value in metadata.items()
                    if value not in (None, "", [], {})
                }
                chunking = document.chunking or ChunkingConfig()
                status = document.disposition or "pending"
                item = CleaningDocument(
                    file_id=row.id,
                    sequence=sequence,
                    title=document.title[:300],
                    dify_document_name=row.original_filename if batch.upload_mode == "raw" else row.original_filename,
                    document_key=f"{row.id}:{sequence}:{row.source_sha256[:12]}",
                    status=status,
                    stage="review" if status == "review_required" else ("excluded" if status == "excluded" else "pending"),
                    error_code=document.error_code,
                    error_message=document.error_message,
                    metadata_json=metadata,
                    chunking_json=asdict(chunking),
                    audit_json=document.audit or {},
                    warnings_json=document.warnings or [],
                    cleaned_markdown=document.markdown,
                    retryable=status == "failed",
                )
                item.dify_document_name = (
                    row.original_filename
                    if batch.upload_mode == "raw"
                    else f"{Path(row.original_filename).stem}__{row.source_sha256[:8]}__{sequence}.md"
                )
                item.content_sha256 = _content_sha(item.cleaned_markdown, item.metadata_json, item.chunking_json)
                session.add(item)
            session.commit()

            pending_documents = list(
                session.scalars(
                    select(CleaningDocument)
                    .where(
                        CleaningDocument.file_id == row.id,
                        CleaningDocument.status == "pending",
                    )
                    .order_by(CleaningDocument.sequence)
                )
            )
            if batch.strategy_code == "course_information":
                ordered_ids = _course_document_publish_order(pending_documents)
                if ordered_ids:
                    chain(
                        *(
                            process_cleaning_document.si(item_id).set(queue="ai_cleaning")
                            for item_id in ordered_ids
                        )
                    ).apply_async()
            else:
                for item in pending_documents:
                    process_cleaning_document.apply_async(args=[item.id], queue="ai_cleaning")
            _refresh_file_state(session, row.id, commit=False)
            _finish_batch(session, row.batch_id)
            session.commit()
        record_event(
            level="INFO",
            category="task",
            component="cleaning_worker",
            event_code="CLEANING_FILE_PARSED",
            status="succeeded",
            message="清洗文件解析完成",
            resource_type="cleaning_file",
            resource_id=file_id,
        )
        return True
    except Exception as exc:
        logger.exception("cleaning file failed", extra={"file_id": file_id})
        with SessionLocal() as session:
            row = session.get(CleaningFile, file_id)
            if row:
                retry = row.attempts < 2
                row.status = "pending" if retry else "failed"
                row.outcome = "pending" if retry else "failed"
                row.stage = "retrying" if retry else "failed"
                row.error_code = "CLEANING_FAILED"
                row.error_message = str(exc)[:2000]
                _finish_batch(session, row.batch_id)
                session.commit()
                if retry:
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


@celery_app.task(
    name="ai_management.process_cleaning_document",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def process_cleaning_document(self, document_id: str) -> bool:
    try:
        with SessionLocal() as session:
            row = session.scalar(
                select(CleaningDocument)
                .where(CleaningDocument.id == document_id)
                .options(
                    selectinload(CleaningDocument.file).selectinload(CleaningFile.batch)
                )
            )
            if row is None or row.status not in {"pending", "processing"}:
                return False
            batch = row.file.batch
            file_row = row.file
            result = _publish_document(session, row, batch, file_row)
            _refresh_file_state(session, file_row.id, commit=False)
            _finish_batch(session, file_row.batch_id)
            session.commit()
            return result
    except Exception as exc:
        with SessionLocal() as session:
            row = session.get(CleaningDocument, document_id)
            if row is not None and row.attempts < 2:
                _refresh_file_state(session, row.file_id, commit=False)
                _finish_batch(session, row.file.batch_id)
                session.commit()
                raise self.retry(exc=exc, countdown=10, max_retries=1)
        record_event(
            level="ERROR",
            category="task",
            component="cleaning_worker",
            event_code="CLEANING_DOCUMENT_FAILED",
            status="failed",
            message="清洗条目入库失败",
            resource_type="cleaning_document",
            resource_id=document_id,
            details={"error_type": type(exc).__name__},
        )
        return False


__all__ = [
    "_finish_batch",
    "_knowledge_client",
    "_queue_document_cleanup",
    "_refresh_file_state",
    "_remove_or_disable_document",
    "_set_document_state",
    "_set_file_state",
    "process_cleaning_document",
    "process_cleaning_file",
]
