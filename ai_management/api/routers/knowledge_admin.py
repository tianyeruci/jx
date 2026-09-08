from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from knowledge_cleaner.dify import DifyKnowledgeClient

from ...cache import cache
from ...cleaning import (
    STRATEGY_REGISTRY,
    _reject_disabled_course_write,
    cleaning_batch_cache_key,
    create_cleaning_batch,
    get_cleaning_batch,
    get_cleaning_item,
    get_cleaning_items,
    retry_cleaning_file,
    retry_cleaning_item,
    retry_model_contract_item,
    review_cleaning_item,
)
from ...config import get_settings
from ...courses import rebuild_snapshot
from ...db import SessionLocal, get_session
from ...errors import ServiceError
from ...model_cleaning import get_provider_batch_ids
from ...models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    Course,
    CourseDirection,
    DifyPublicationShard,
    LearningRoute,
    ModelCleaningTask,
)
from ...schemas import CleaningReviewDecisionRequest, DeleteDocumentRequest, DocumentStatusRequest
from ...security import require_admin_token, require_internal_token
from ...services.admin_settings import bump_catalog_revision
from ...services.bailian_cleanup import (
    clear_successful_bailian_file_tasks,
    ensure_bailian_cleanup_tasks,
)
from ...services.observability import (
    exception_details,
    record_event,
)

router = APIRouter(
    prefix="/internal/v1/admin/knowledge-bases",
    tags=["admin-knowledge"],
    dependencies=[Depends(require_internal_token), Depends(require_admin_token)],
)

_BASES = {
    "general": {"name": "通用知识库"},
    "course": {"name": "课程知识库"},
}


async def _read_upload_bounded(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, limit - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ServiceError("PAYLOAD_TOO_LARGE", "上传文件超过大小限制", 413, False)
        chunks.append(chunk)
    return b"".join(chunks)


def _dataset_id(kb_code: str) -> str:
    settings = get_settings()
    if kb_code == "general":
        return settings.dify_general_dataset_id
    if kb_code == "course":
        return settings.dify_course_dataset_id
    raise ServiceError("VALIDATION_ERROR", "知识库代码只能是 general 或 course", 422, False)


def _client(kb_code: str) -> DifyKnowledgeClient:
    settings = get_settings()
    dataset_id = _dataset_id(kb_code)
    if not settings.dify_knowledge_api_key or not dataset_id:
        raise ServiceError("DIFY_UNAVAILABLE", "知识库服务尚未配置", 503, True)
    return DifyKnowledgeClient(
        settings.knowledge_base_url,
        settings.dify_knowledge_api_key,
        dataset_id,
        timeout=180,
    )


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    raw = document.get("doc_metadata") or document.get("metadata") or []
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        return {}
    return {
        str(item.get("name")): item.get("value")
        for item in raw
        if isinstance(item, dict) and item.get("name")
    }


def _document_name(document: dict[str, Any]) -> str:
    details = document.get("data_source_info") or document.get("data_source_detail_dict") or {}
    return str(
        document.get("name")
        or details.get("upload_file")
        or details.get("file_name")
        or _metadata(document).get("source_filename")
        or ""
    )


def _catalog_record_for_document(
    session: Session,
    kb_code: str,
    document_id: str,
) -> Course | LearningRoute | None:
    """Return the DB authority record for a published catalogue document.

    General course-knowledge documents remain ordinary Dify documents.  A
    record returned here is different: its enabled state controls structured
    recommendation and therefore must never drift from Dify.
    """
    if kb_code != "course":
        return None
    course = session.scalar(select(Course).where(Course.dify_document_id == document_id))
    if course is not None:
        return course
    return session.scalar(
        select(LearningRoute).where(LearningRoute.dify_document_id == document_id)
    )


def _serialize_document(
    document: dict[str, Any],
    local_source: dict[str, str] | None = None,
) -> dict[str, Any]:
    metadata = _metadata(document)
    local_source = local_source or {}
    return {
        "document_id": str(document.get("id") or ""),
        "name": _document_name(document) or str(local_source.get("name") or ""),
        "enabled": bool(document.get("enabled", True))
        and not bool(document.get("archived", False)),
        "indexing_status": str(document.get("indexing_status") or ""),
        "word_count": int(document.get("word_count") or 0),
        "segment_count": int(document.get("segment_count") or 0),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "source_sha256": str(
            metadata.get("source_sha256") or local_source.get("source_sha256") or ""
        ),
        "source_filename": str(
            metadata.get("source_filename") or local_source.get("source_filename") or ""
        ),
    }


def _documents(kb_code: str) -> list[dict[str, Any]]:
    client = _client(kb_code)
    try:
        return list(client.iter_documents(max_pages=1000, max_items=100000))
    except Exception as exc:
        sensitive_values = (
            get_settings().dify_knowledge_api_key,
            _dataset_id(kb_code),
        )
        raise ServiceError(
            "DIFY_UNAVAILABLE",
            "读取知识库文档失败",
            502,
            True,
            exception_details(
                exc,
                phase="list_documents",
                sensitive_values=sensitive_values,
                knowledge_base_code=kb_code,
            ),
        ) from exc
    finally:
        client.close()


def _local_cleaning_document_sources(
    kb_code: str,
    session: Session,
) -> dict[str, dict[str, str]]:
    rows = session.execute(
        select(
            CleaningDocument.dify_document_id,
            CleaningDocument.dify_document_name,
            CleaningFile.original_filename,
            CleaningFile.source_sha256,
        )
        .join(CleaningFile, CleaningFile.id == CleaningDocument.file_id)
        .join(CleaningBatch, CleaningBatch.id == CleaningFile.batch_id)
        .where(
            CleaningBatch.knowledge_base_code == kb_code,
            CleaningDocument.dify_document_id.is_not(None),
        )
    )
    result: dict[str, dict[str, str]] = {}
    for dify_document_id, dify_document_name, original_filename, source_sha256 in rows:
        if not dify_document_id:
            continue
        result[str(dify_document_id)] = {
            "name": str(dify_document_name or ""),
            "source_filename": str(original_filename or ""),
            "source_sha256": str(source_sha256 or ""),
        }
    return result


@router.get("")
def knowledge_bases() -> dict[str, list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    for code, definition in _BASES.items():
        docs = _documents(code)
        items.append(
            {
                "code": code,
                "name": definition["name"],
                "document_count": len(docs),
                "enabled_document_count": sum(
                    bool(item.get("enabled", True)) and not bool(item.get("archived", False))
                    for item in docs
                ),
            }
        )
    return {"items": items}


@router.get("/course/catalog-directions")
def catalog_directions(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "items": [
            {
                "code": item.code,
                "name": item.name,
                "aliases": item.aliases or [],
                "enabled": item.enabled,
                "review_status": item.review_status,
            }
            for item in session.scalars(select(CourseDirection).order_by(CourseDirection.name))
        ]
    }


@router.get("/{kb_code}/documents")
def list_documents(
    kb_code: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    local_sources = _local_cleaning_document_sources(kb_code, session)
    return {
        "items": [
            _serialize_document(
                item,
                local_sources.get(str(item.get("id") or "")),
            )
            for item in _documents(kb_code)
        ],
        "next_cursor": None,
    }


@router.get("/{kb_code}/cleaning-strategies")
def list_cleaning_strategies(kb_code: str) -> dict[str, list[dict[str, Any]]]:
    _dataset_id(kb_code)
    # Course uploads remain accepted when an existing integration calls their
    # explicit upload endpoint, but strategy discovery no longer advertises
    # any course/route cleaning flow.
    if kb_code == "course":
        return {"items": []}
    hidden_strategy_codes = {
        "course_information",
        "learning_route",
        "course_route_catalog",
    }
    items = []
    for item in STRATEGY_REGISTRY.values():
        required = getattr(item, "knowledge_base_code", None)
        if required and required != kb_code:
            continue
        if item.code in {"standard_document", "test_markdown", *hidden_strategy_codes}:
            continue
        items.append(
            {
                "code": item.code,
                "display_name": item.display_name,
                "supported_extensions": sorted(item.supported_extensions),
                "knowledge_base_code": getattr(item, "knowledge_base_code", None),
            }
        )
    return {"items": items}


@router.post("/{kb_code}/upload-batches")
async def upload_batch(
    kb_code: str,
    files: list[UploadFile] = File(...),
    upload_mode: str = Form(default="cleaned"),
    strategy_code: str = Form(default="standard_document"),
    common_metadata: str = Form(default="{}"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    return await create_cleaning_batch(
        strategy_code=strategy_code,
        knowledge_base_code=kb_code,
        upload_mode=upload_mode,
        common_metadata=common_metadata,
        files=files,
        session=session,
    )


@router.get("/{kb_code}/upload-batches/{batch_id}")
def upload_batch_status(
    kb_code: str, batch_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    _dataset_id(kb_code)
    payload = get_cleaning_batch(batch_id, session)
    if payload["knowledge_base_code"] != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
    return payload


def _failed_items_statement(
    *,
    batch_id: str,
    status: str,
    error_code: str | None = None,
    cursor: str | None = None,
):
    statement = (
        select(CleaningDocument, CleaningFile)
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningFile.batch_id == batch_id,
            CleaningDocument.status == status,
        )
    )
    if error_code:
        statement = statement.where(CleaningDocument.error_code == error_code)
    if cursor:
        statement = statement.where(CleaningDocument.id > cursor)
    return statement.order_by(CleaningDocument.id).limit(501)


def _serialize_failed_item(
    document: CleaningDocument,
    file_row: CleaningFile,
    strategy_code: str,
) -> dict[str, Any]:
    audit = document.audit_json or {}
    return {
        "item_id": document.id,
        "file_id": file_row.id,
        "filename": file_row.original_filename,
        "strategy_code": strategy_code,
        "title": document.title,
        "status": document.status,
        "error_code": document.error_code or "",
        "error_reason": document.error_message or "",
        "attempts": document.attempts,
        "model_attempts": int(audit.get("model_attempts") or 0),
        "retryable": bool(document.retryable),
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


@router.get("/{kb_code}/upload-batches/{batch_id}/failed-items", response_model=None)
def list_failed_upload_items(
    kb_code: str,
    batch_id: str,
    status: str = "failed",
    error_code: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    format: str = "json",
    session: Session = Depends(get_session),
) -> dict[str, Any] | StreamingResponse:
    """List bounded failure records without loading the whole batch."""

    _dataset_id(kb_code)
    batch = session.scalar(
        select(CleaningBatch).where(
            CleaningBatch.id == batch_id,
            CleaningBatch.knowledge_base_code == kb_code,
        )
    )
    if batch is None:
        raise ServiceError("VALIDATION_ERROR", "上传批次不存在", 404, False)
    status = status.strip().lower()
    if status not in {"failed", "review_required"}:
        raise ServiceError(
            "VALIDATION_ERROR",
            "失败记录状态只能是 failed 或 review_required",
            422,
            False,
        )
    format = format.strip().lower()
    if format not in {"json", "csv"}:
        raise ServiceError("VALIDATION_ERROR", "format 只能是 json 或 csv", 422, False)
    limit = max(1, min(limit, 500))

    if format == "csv":

        def stream_csv():
            fields = [
                "item_id",
                "file_id",
                "filename",
                "strategy_code",
                "title",
                "status",
                "error_code",
                "error_reason",
                "attempts",
                "model_attempts",
                "retryable",
                "created_at",
                "updated_at",
            ]
            with SessionLocal() as stream_session:
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=fields)
                writer.writeheader()
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
                next_cursor = cursor
                while True:
                    rows = list(
                        stream_session.execute(
                            _failed_items_statement(
                                batch_id=batch_id,
                                status=status,
                                error_code=error_code,
                                cursor=next_cursor,
                            )
                        )
                    )
                    if not rows:
                        break
                    has_more = len(rows) > 500
                    page_rows = rows[:500]
                    for document, file_row in page_rows:
                        writer.writerow(
                            _serialize_failed_item(document, file_row, batch.strategy_code)
                        )
                    yield output.getvalue()
                    output.seek(0)
                    output.truncate(0)
                    if not has_more:
                        break
                    next_cursor = page_rows[-1][0].id

        return StreamingResponse(
            stream_csv(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (f'attachment; filename="failed-items-{batch_id}.csv"')
            },
        )

    rows = list(
        session.execute(
            _failed_items_statement(
                batch_id=batch_id,
                status=status,
                error_code=error_code,
                cursor=cursor,
            )
        )
    )
    has_more = len(rows) > limit
    items = [
        _serialize_failed_item(document, file_row, batch.strategy_code)
        for document, file_row in rows[:limit]
    ]
    total = session.scalar(
        select(func.count(CleaningDocument.id))
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningFile.batch_id == batch_id,
            CleaningDocument.status == status,
            *([CleaningDocument.error_code == error_code] if error_code else []),
        )
    )
    return {
        "batch_id": batch_id,
        "strategy_code": batch.strategy_code,
        "status": status,
        "items": items,
        "total": int(total or 0),
        "next_cursor": items[-1]["item_id"] if has_more and items else None,
    }


@router.post("/{kb_code}/upload-batches/{batch_id}/model-tasks/{task_id}/approve")
def approve_model_task(
    kb_code: str,
    batch_id: str,
    task_id: str,
    candidate_fingerprint: str = Form(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    batch = session.get(CleaningBatch, batch_id)
    task = session.scalar(
        select(ModelCleaningTask)
        .where(
            ModelCleaningTask.id == task_id,
            ModelCleaningTask.batch_id == batch_id,
        )
        .with_for_update()
    )
    if (
        batch is None
        or task is None
        or task.batch_id != batch_id
        or batch.knowledge_base_code != kb_code
    ):
        raise ServiceError("VALIDATION_ERROR", "模型任务不存在", 404, False)
    if (
        task.mode == "batch"
        and task.approval_fingerprint == candidate_fingerprint
        and task.status
        in {"approved", "batch_uploading", "batch_running", "validating", "completed"}
    ):
        # The queue submit can succeed while the HTTP response is lost.  A
        # repeated approval with the same candidate snapshot is a stable
        # idempotent read and must not enqueue a second paid Batch.
        return {
            "task_id": task.id,
            "status": task.status,
            "candidate_fingerprint": task.candidate_fingerprint,
        }
    if task.mode != "batch" or task.status != "awaiting_approval":
        raise ServiceError("CONFLICT", "模型任务当前不可批准", 409, False)
    if candidate_fingerprint != task.candidate_fingerprint:
        raise ServiceError("CONFLICT", "候选集已变化，请重新确认费用", 409, False)
    settings = get_settings()
    if (
        not task.price_version
        or not task.price_effective_date
        or not task.price_valid_until
        or settings.cleaning_input_price_per_million <= 0
        or settings.cleaning_output_price_per_million <= 0
    ):
        raise ServiceError(
            "CONFIGURATION_ERROR",
            "价格版本缺失或无效，禁止产生Batch付费调用",
            409,
            False,
        )
    try:
        price_valid_until = date.fromisoformat(task.price_valid_until)
    except ValueError as exc:
        raise ServiceError("CONFIGURATION_ERROR", "价格有效期格式无效", 409, False) from exc
    if price_valid_until < datetime.now(timezone.utc).date():
        raise ServiceError(
            "CONFIGURATION_ERROR", "价格快照已过期，禁止产生Batch付费调用", 409, False
        )
    task.approval_fingerprint = candidate_fingerprint
    task.approved_at = datetime.now(timezone.utc)
    task.status = "approved"
    batch.status = "model_batch_uploading"
    session.commit()
    # Status polling (including the test UI) may have cached the previous
    # awaiting_approval payload.  Invalidate before queueing so a refresh
    # cannot invite a duplicate paid approval request.
    cache.delete(cleaning_batch_cache_key(batch_id))
    from ...workers.tasks.model_cleaning import submit_batch_model_task

    try:
        submit_batch_model_task.apply_async(args=[task.id], queue="ai_model_batch")
    except Exception as exc:
        task.status = "awaiting_approval"
        task.approval_fingerprint = ""
        task.approved_at = None
        batch.status = "awaiting_model_approval"
        session.commit()
        cache.delete(cleaning_batch_cache_key(batch_id))
        raise ServiceError(
            "TASK_QUEUE_UNAVAILABLE",
            "模型任务提交失败，未消耗已确认费用",
            503,
            True,
        ) from exc
    return {
        "task_id": task.id,
        "status": task.status,
        "candidate_fingerprint": task.candidate_fingerprint,
    }


@router.post("/{kb_code}/upload-batches/{batch_id}/model-tasks/{task_id}/items/{item_id}/retry")
def retry_model_contract_item_route(
    kb_code: str,
    batch_id: str,
    task_id: str,
    item_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    batch = session.get(CleaningBatch, batch_id)
    if batch is None or batch.knowledge_base_code != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
    try:
        return retry_model_contract_item(
            batch_id=batch_id,
            task_id=task_id,
            item_id=item_id,
            session=session,
        )
    except ValueError as exc:
        raise ServiceError("VALIDATION_ERROR", str(exc), 409, False) from exc


@router.post("/{kb_code}/upload-batches/{batch_id}/model-tasks/{task_id}/cancel")
def cancel_model_task(
    kb_code: str,
    batch_id: str,
    task_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    batch = session.get(CleaningBatch, batch_id)
    task = session.scalar(
        select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
    )
    if (
        batch is None
        or task is None
        or task.batch_id != batch_id
        or batch.knowledge_base_code != kb_code
    ):
        raise ServiceError("VALIDATION_ERROR", "模型任务不存在", 404, False)
    if task.mode != "batch" or task.status not in {
        "awaiting_approval",
        "approved",
        "batch_uploading",
        "batch_running",
        "validating",
    }:
        raise ServiceError("CONFLICT", "模型任务当前不可取消", 409, False)
    provider_ids = get_provider_batch_ids(task)
    provider_file_ids = {
        *(task.provider_input_file_ids or []),
        *(task.provider_output_file_ids or []),
        *(task.provider_error_file_ids or []),
    }
    terminal_batches = (task.result_counts or {}).get("provider_terminal_batches", {})
    if isinstance(terminal_batches, dict):
        for terminal_status in terminal_batches.values():
            if not isinstance(terminal_status, dict):
                continue
            for field in ("output_file_id", "error_file_id"):
                if terminal_status.get(field):
                    provider_file_ids.add(str(terminal_status[field]))
    documents = session.scalars(
        select(CleaningDocument)
        .join(CleaningFile)
        .where(
            CleaningFile.batch_id == batch_id,
            CleaningDocument.status.in_(
                (
                    "awaiting_model",
                    "model_processing",
                    "shard_pending",
                    "shard_indexing",
                )
            ),
        )
        .execution_options(yield_per=500)
    )
    for document in documents:
        document.status = "review_required"
        document.stage = "review"
        document.error_code = "MODEL_TASK_CANCELLED"
        document.error_message = "管理员已取消模型任务，条目需复核或重新上传"
    task.status = "cancelled"
    batch.status = "partially_completed"
    task.cleanup_status = "pending_retry" if (provider_ids or provider_file_ids) else "completed"
    if provider_ids or provider_file_ids:
        ensure_bailian_cleanup_tasks(
            session,
            task,
            provider_batch_ids=provider_ids,
            provider_file_ids=provider_file_ids,
            include_file_tasks=not provider_ids,
            last_error="管理员已取消模型任务，等待供应商资源清理",
        )
    session.commit()

    if provider_ids or provider_file_ids:
        from ...model_cleaning import BailianClient

        settings = get_settings()
        file_ids = set(provider_file_ids)
        pending_batch_ids: set[str] = set()
        cancel_errors: dict[str, str] = {}
        client = None
        try:
            client = BailianClient(
                api_key=settings.bailian_api_key,
                base_url=settings.bailian_base_url,
                timeout_seconds=60,
            )
            for provider_id in provider_ids:
                try:
                    cancelled = client.cancel_batch(provider_id)
                except Exception as exc:
                    pending_batch_ids.add(provider_id)
                    cancel_errors[provider_id] = str(exc)[:1000]
                    continue
                for field in ("output_file_id", "error_file_id"):
                    if cancelled.get(field):
                        file_ids.add(str(cancelled[field]))
                provider_status = str(cancelled.get("status") or "cancelled")
                if provider_status not in {"completed", "failed", "expired", "cancelled"}:
                    pending_batch_ids.add(provider_id)
                    cancel_errors[provider_id] = (
                        f"供应商取消仍处于 {provider_status}，保留 Batch 和文件引用"
                    )
        except Exception as exc:
            pending_batch_ids.update(provider_ids)
            cancel_errors.update({provider_id: str(exc)[:1000] for provider_id in provider_ids})
        finally:
            if client is not None:
                client.close()

        task = session.scalar(
            select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
        )
        if task is None:
            raise ServiceError("VALIDATION_ERROR", "模型任务不存在", 404, False)
        if pending_batch_ids:
            task.cleanup_status = "pending_retry"
            ensure_bailian_cleanup_tasks(
                session,
                task,
                provider_batch_ids=pending_batch_ids,
                provider_file_ids=file_ids,
                include_file_tasks=False,
                last_error="；".join(
                    cancel_errors.get(provider_id, "供应商取消仍未终态")
                    for provider_id in sorted(pending_batch_ids)
                ),
            )
            session.commit()
        else:
            cleanup_failed = False
            deleted_file_ids: set[str] = set()
            cleanup_client = None
            try:
                cleanup_client = BailianClient(
                    api_key=settings.bailian_api_key,
                    base_url=settings.bailian_base_url,
                    timeout_seconds=60,
                )
                for file_id in sorted(file_ids):
                    try:
                        cleanup_client.delete_file(file_id)
                        deleted_file_ids.add(file_id)
                    except Exception as exc:
                        cleanup_failed = True
                        ensure_bailian_cleanup_tasks(
                            session,
                            task,
                            provider_file_ids=[file_id],
                            last_error=str(exc),
                        )
            except Exception as exc:
                cleanup_failed = True
                ensure_bailian_cleanup_tasks(
                    session,
                    task,
                    provider_file_ids=file_ids,
                    last_error=str(exc),
                )
            finally:
                if cleanup_client is not None:
                    cleanup_client.close()
            task.cleanup_status = "pending_retry" if cleanup_failed else "completed"
            if not cleanup_failed:
                clear_successful_bailian_file_tasks(session, deleted_file_ids)
                task.provider_batch_ids = []
                task.provider_batch_id = None
                task.provider_input_file_ids = []
                task.provider_output_file_ids = []
                task.provider_error_file_ids = []
                counts = dict(task.result_counts or {})
                counts.pop("provider_terminal_batches", None)
                task.result_counts = counts
            session.commit()
    return {"task_id": task.id, "status": task.status}


@router.post("/{kb_code}/upload-batches/{batch_id}/shards/{shard_id}/retry")
def retry_publication_shard(
    kb_code: str,
    batch_id: str,
    shard_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    shard = session.scalar(
        select(DifyPublicationShard)
        .join(CleaningFile)
        .where(
            DifyPublicationShard.id == shard_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    batch = session.get(CleaningBatch, batch_id)
    if (
        shard is None
        or batch is None
        or batch.knowledge_base_code != kb_code
        or shard.status not in {"failed", "shard_pending"}
    ):
        raise ServiceError("VALIDATION_ERROR", "分片不存在或当前不可重试", 404, False)
    shard.status = "shard_pending"
    shard.last_error = ""
    for document in shard.documents:
        document.status = "shard_pending"
        document.stage = "shard_pending"
    session.commit()
    from ...workers.tasks.model_cleaning import publish_dify_shard

    publish_dify_shard.apply_async(args=[shard.id], queue="ai_publication")
    return {"shard_id": shard.id, "status": shard.status}


@router.post("/{kb_code}/upload-batches/{batch_id}/shards/{shard_id}/replace")
def replace_publication_shard(
    kb_code: str,
    batch_id: str,
    shard_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    shard = session.scalar(
        select(DifyPublicationShard)
        .join(CleaningFile)
        .where(
            DifyPublicationShard.id == shard_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    batch = session.get(CleaningBatch, batch_id)
    if (
        shard is None
        or batch is None
        or batch.knowledge_base_code != kb_code
        or shard.status != "succeeded"
    ):
        raise ServiceError("VALIDATION_ERROR", "分片不存在或当前不可精确替换", 404, False)
    shard.status = "shard_pending"
    for document in shard.documents:
        document.status = "shard_pending"
        document.stage = "shard_pending"
    session.commit()
    from ...workers.tasks.model_cleaning import publish_dify_shard

    publish_dify_shard.apply_async(args=[shard.id], queue="ai_publication")
    return {
        "shard_id": shard.id,
        "status": shard.status,
        "replaces_document_id": shard.dify_document_id,
    }


@router.get("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/catalog-review.xlsx")
def export_catalog_review_workbook(
    kb_code: str,
    batch_id: str,
    file_id: str,
    session: Session = Depends(get_session),
) -> Response:
    _dataset_id(kb_code)
    from ...catalog_review import export_catalog_review

    try:
        content = export_catalog_review(session, batch_id=batch_id, file_id=file_id)
    except ValueError as exc:
        raise ServiceError("VALIDATION_ERROR", str(exc), 422, False) from exc
    return Response(
        content=content,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": 'attachment; filename="catalog-review.xlsx"'},
    )


@router.post("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/catalog-review-import")
async def import_catalog_review_workbook(
    kb_code: str,
    batch_id: str,
    file_id: str,
    review_file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    from ...catalog_review import import_catalog_review

    content = await _read_upload_bounded(
        review_file, get_settings().catalog_review_max_mb * 1024 * 1024
    )
    try:
        result = import_catalog_review(
            session,
            batch_id=batch_id,
            file_id=file_id,
            content=content,
        )
    except ValueError as exc:
        raise ServiceError("VALIDATION_ERROR", str(exc), 422, False) from exc
    cache.delete(cleaning_batch_cache_key(batch_id))
    first_pending = session.scalar(
        select(CleaningDocument.id)
        .where(
            CleaningDocument.file_id == file_id,
            CleaningDocument.status == "pending",
        )
        .order_by(CleaningDocument.sequence)
        .limit(1)
    )
    if first_pending:
        from ...workers.tasks.cleaning import process_cleaning_document

        process_cleaning_document.apply_async(args=[first_pending], queue="ai_publication")
    return {**result, "status": "publishing" if first_pending else "completed"}


@router.post("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry")
def retry_upload_file(
    kb_code: str, batch_id: str, file_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    _dataset_id(kb_code)
    payload = get_cleaning_batch(batch_id, session)
    if payload["knowledge_base_code"] != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
    _reject_disabled_course_write(
        strategy_code=str(payload.get("strategy_code") or ""),
        knowledge_base_code=kb_code,
    )
    return retry_cleaning_file(batch_id, file_id, session)


@router.get("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items")
def list_upload_items(
    kb_code: str,
    batch_id: str,
    file_id: str,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    payload = get_cleaning_batch(batch_id, session)
    if payload["knowledge_base_code"] != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
    return get_cleaning_items(
        batch_id,
        file_id,
        status=status,
        cursor=cursor,
        limit=max(1, min(limit, 200)),
        session=session,
    )


@router.get("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}")
def upload_item_detail(
    kb_code: str,
    batch_id: str,
    file_id: str,
    item_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    payload = get_cleaning_batch(batch_id, session)
    if payload["knowledge_base_code"] != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
    return get_cleaning_item(batch_id, file_id, item_id, session)


@router.post("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry")
def retry_upload_item(
    kb_code: str,
    batch_id: str,
    file_id: str,
    item_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    payload = get_cleaning_batch(batch_id, session)
    if payload["knowledge_base_code"] != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
    _reject_disabled_course_write(
        strategy_code=str(payload.get("strategy_code") or ""),
        knowledge_base_code=kb_code,
    )
    return retry_cleaning_item(batch_id, file_id, item_id, session)


@router.post(
    "/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions"
)
async def review_upload_item(
    kb_code: str,
    batch_id: str,
    file_id: str,
    item_id: str,
    action: str = Form(...),
    expected_revision: int = Form(...),
    reason: str = Form(default=""),
    metadata_patch: str = Form(default="{}"),
    markdown_file: UploadFile | None = File(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _dataset_id(kb_code)
    payload = get_cleaning_batch(batch_id, session)
    if payload["knowledge_base_code"] != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
    _reject_disabled_course_write(
        strategy_code=str(payload.get("strategy_code") or ""),
        knowledge_base_code=kb_code,
    )
    try:
        parsed = CleaningReviewDecisionRequest(
            action=action,
            expected_revision=expected_revision,
            reason=reason,
            metadata_patch=json.loads(metadata_patch or "{}"),
        )
    except ValueError as exc:
        raise ServiceError("VALIDATION_ERROR", "复核参数无效", 422, False) from exc
    markdown_text = None
    if markdown_file is not None:
        try:
            markdown_text = (
                await _read_upload_bounded(
                    markdown_file, get_settings().review_markdown_max_mb * 1024 * 1024
                )
            ).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ServiceError(
                "VALIDATION_ERROR", "复核 Markdown 必须使用 UTF-8 编码", 422, False
            ) from exc
    return review_cleaning_item(
        batch_id,
        file_id,
        item_id,
        action=parsed.action,
        expected_revision=parsed.expected_revision,
        reason=parsed.reason,
        metadata_patch=parsed.metadata_patch,
        markdown_text=markdown_text,
        session=session,
    )


@router.patch("/{kb_code}/documents/{document_id}/status")
def set_document_status(
    kb_code: str,
    document_id: str,
    payload: DocumentStatusRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    matches = [item for item in _documents(kb_code) if str(item.get("id") or "") == document_id]
    if not matches:
        raise ServiceError("VALIDATION_ERROR", "知识库文档不存在", 404, False)
    catalog_record = _catalog_record_for_document(session, kb_code, document_id)
    if (
        catalog_record is not None
        and payload.enabled
        and catalog_record.review_status != "approved"
    ):
        raise ServiceError("RECOMMENDATION_INVALID", "未审核目录记录不能启用", 409, False)
    # Disabling must make the authority catalogue safe before an external
    # Dify call.  If that call is ambiguous or fails after applying, the
    # course stays unavailable for structured recommendation.
    if catalog_record is not None and not payload.enabled:
        catalog_record.enabled = False
        bump_catalog_revision(session)
        session.commit()
        rebuild_snapshot(session)
    client = _client(kb_code)
    try:
        if payload.enabled:
            client.enable_documents([document_id])
        else:
            client.disable_documents([document_id])
    except Exception as exc:
        raise ServiceError("DIFY_UNAVAILABLE", "更新知识库文档状态失败", 502, True) from exc
    finally:
        client.close()
    # For enabling, wait until Dify is available before exposing the record
    # in the authority catalogue.  This prevents a course card whose
    # retrieval copy is still disabled.
    if catalog_record is not None and payload.enabled:
        catalog_record.enabled = payload.enabled
        bump_catalog_revision(session)
        session.commit()
        rebuild_snapshot(session)
    record_event(
        level="INFO",
        category="operation",
        component="knowledge_admin_api",
        event_code="KNOWLEDGE_DOCUMENT_STATUS_UPDATED",
        status="succeeded",
        message="知识库文档状态已更新",
        actor_hash="admin",
        resource_type="dify_document",
        resource_id=document_id,
        details={"kb_code": kb_code, "enabled": payload.enabled},
    )
    return {
        "document_id": document_id,
        "enabled": payload.enabled,
        "catalog_record": {
            "record_type": "course" if isinstance(catalog_record, Course) else "learning_route",
            "record_id": (
                catalog_record.course_id
                if isinstance(catalog_record, Course)
                else catalog_record.route_id
            ),
        }
        if catalog_record is not None
        else None,
    }


@router.delete("/{kb_code}/documents/{document_id}")
def delete_document(
    kb_code: str,
    document_id: str,
    payload: DeleteDocumentRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    matches = [item for item in _documents(kb_code) if str(item.get("id") or "") == document_id]
    if not matches:
        raise ServiceError("VALIDATION_ERROR", "知识库文档不存在", 404, False)
    catalog_record = _catalog_record_for_document(session, kb_code, document_id)
    if catalog_record is not None:
        raise ServiceError(
            "CONFLICT",
            "课程或学习路线目录文档不能在此删除；请先停用目录记录，后续以新目录文件替换。",
            409,
            False,
        )
    actual_name = _document_name(matches[0])
    if payload.confirm_name != actual_name:
        raise ServiceError("CONFLICT", "confirm_name 与当前文档名称不一致", 409, False)
    client = _client(kb_code)
    try:
        client.delete_document(document_id)
    except Exception as exc:
        raise ServiceError("DIFY_UNAVAILABLE", "Dify 删除失败，未标记为成功", 502, True) from exc
    finally:
        client.close()
    local_document = session.scalar(
        select(CleaningDocument)
        .where(CleaningDocument.dify_document_id == document_id)
        .options(selectinload(CleaningDocument.file))
    )
    if local_document is not None:
        local_document.remote_deleted_at = datetime.now(timezone.utc)
        local_document.dify_document_id = None
        for shard in session.scalars(
            select(DifyPublicationShard).where(DifyPublicationShard.dify_document_id == document_id)
        ):
            shard.remote_deleted_at = datetime.now(timezone.utc)
            shard.dify_document_id = None
        if local_document.file and not any(
            item.dify_document_id
            for item in local_document.file.documents
            if item.id != local_document.id
        ):
            local_document.file.dedupe_released_at = datetime.now(timezone.utc)
        session.commit()
    record_event(
        level="INFO",
        category="operation",
        component="knowledge_admin_api",
        event_code="KNOWLEDGE_DOCUMENT_DELETED",
        status="succeeded",
        message="知识库文档已删除",
        actor_hash="admin",
        resource_type="dify_document",
        resource_id=document_id,
        details={"kb_code": kb_code, "name": actual_name},
    )
    return {"deleted": True, "document_id": document_id}


@router.delete("/{kb_code}/sources/{file_id}")
def delete_uploaded_source(
    kb_code: str,
    file_id: str,
    payload: DeleteDocumentRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Delete every Dify publication shard belonging to one uploaded source.

    The source file is the duplicate-protection unit: re-upload is released
    only after all known remote document IDs have been deleted successfully.
    """
    file_row = session.scalar(
        select(CleaningFile)
        .join(CleaningBatch, CleaningBatch.id == CleaningFile.batch_id)
        .where(
            CleaningFile.id == file_id,
            CleaningBatch.knowledge_base_code == kb_code,
        )
        .options(
            selectinload(CleaningFile.documents),
            selectinload(CleaningFile.publication_shards),
        )
    )
    if file_row is None:
        raise ServiceError("VALIDATION_ERROR", "上传源文件不存在", 404, False)
    if payload.confirm_name != file_row.original_filename:
        raise ServiceError("CONFLICT", "confirm_name 与上传源文件名不一致", 409, False)
    ids = {str(item.dify_document_id) for item in file_row.documents if item.dify_document_id} | {
        str(item.dify_document_id) for item in file_row.publication_shards if item.dify_document_id
    }
    client = _client(kb_code) if ids else None
    try:
        for document_id in sorted(ids):
            try:
                client.delete_document(document_id)
            except Exception as exc:
                if not (getattr(getattr(exc, "response", None), "status_code", None) == 404):
                    raise
            for item in file_row.documents:
                if item.dify_document_id == document_id:
                    item.remote_deleted_at = datetime.now(timezone.utc)
                    item.dify_document_id = None
            for item in file_row.publication_shards:
                if item.dify_document_id == document_id:
                    item.remote_deleted_at = datetime.now(timezone.utc)
                    item.dify_document_id = None
            session.commit()
    except Exception as exc:
        raise ServiceError("DIFY_UNAVAILABLE", "源文件仍有知识库文档未删除", 502, True) from exc
    finally:
        if client is not None:
            client.close()
    deleted_at = datetime.now(timezone.utc)
    root = get_settings().upload_root.resolve()
    storage_name = str(file_row.storage_filename or "")
    storage_path = Path(storage_name)
    if "\x00" in storage_name or storage_path.is_absolute() or ".." in storage_path.parts:
        raise ServiceError("CLEANING_FAILED", "源文件路径不安全，未标记为完成", 500, True)
    target = (root / str(file_row.batch_id) / storage_path).resolve(strict=False)
    try:
        if not target.is_relative_to(root):
            raise ValueError("源文件路径越出上传目录")
        target.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        raise ServiceError("CLEANING_FAILED", "源文件删除失败，未标记为完成", 500, True) from exc
    file_row.dedupe_released_at = deleted_at
    file_row.source_deleted_at = deleted_at
    file_row.status = "excluded"
    file_row.outcome = "excluded"
    file_row.stage = "source_deleted"
    batch_outcomes = list(
        session.scalars(
            select(CleaningFile.outcome)
            .where(CleaningFile.batch_id == file_row.batch_id)
            .order_by(CleaningFile.id)
        )
    )
    if batch_outcomes and all(
        str(outcome) in {"succeeded", "excluded", "review_required", "failed", "mixed"}
        for outcome in batch_outcomes
    ):
        batch = session.get(CleaningBatch, file_row.batch_id)
        if batch is not None:
            batch.status = "completed"
    session.commit()
    cache.delete(cleaning_batch_cache_key(file_row.batch_id))
    record_event(
        level="INFO",
        category="operation",
        component="knowledge_admin_api",
        event_code="KNOWLEDGE_SOURCE_DELETED",
        status="succeeded",
        message="上传源文件及其全部知识库分片已删除",
        actor_hash="admin",
        resource_type="cleaning_file",
        resource_id=file_id,
        details={"kb_code": kb_code, "deleted_document_count": len(ids)},
    )
    return {"deleted": True, "file_id": file_id, "deleted_document_count": len(ids)}
