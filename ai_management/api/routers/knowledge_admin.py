from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from knowledge_cleaner.dify import DifyKnowledgeClient

from ...cleaning import (
    STRATEGY_REGISTRY,
    create_cleaning_batch,
    get_cleaning_batch,
    get_cleaning_item,
    get_cleaning_items,
    retry_cleaning_file,
    retry_cleaning_item,
    review_cleaning_item,
)
from ...config import get_settings
from ...db import get_session
from ...errors import ServiceError
from ...schemas import CleaningReviewDecisionRequest, DeleteDocumentRequest, DocumentStatusRequest
from ...security import require_admin_token, require_internal_token
from ...services.observability import record_event

router = APIRouter(
    prefix="/internal/v1/admin/knowledge-bases",
    tags=["admin-knowledge"],
    dependencies=[Depends(require_internal_token), Depends(require_admin_token)],
)

_BASES = {
    "general": {"name": "通用知识库"},
    "course": {"name": "课程知识库"},
}


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


def _serialize_document(document: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata(document)
    return {
        "document_id": str(document.get("id") or ""),
        "name": _document_name(document),
        "enabled": bool(document.get("enabled", True)) and not bool(document.get("archived", False)),
        "indexing_status": str(document.get("indexing_status") or ""),
        "word_count": int(document.get("word_count") or 0),
        "segment_count": int(document.get("segment_count") or 0),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "source_sha256": metadata.get("source_sha256", ""),
        "source_filename": metadata.get("source_filename", ""),
    }


def _documents(kb_code: str) -> list[dict[str, Any]]:
    client = _client(kb_code)
    try:
        return list(client.iter_documents())
    except Exception as exc:
        raise ServiceError("DIFY_UNAVAILABLE", "读取知识库文档失败", 502, True) from exc
    finally:
        client.close()


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


@router.get("/{kb_code}/documents")
def list_documents(kb_code: str) -> dict[str, list[dict[str, Any]]]:
    _dataset_id(kb_code)
    return {"items": [_serialize_document(item) for item in _documents(kb_code)]}


@router.get("/{kb_code}/cleaning-strategies")
def list_cleaning_strategies(kb_code: str) -> dict[str, list[dict[str, Any]]]:
    _dataset_id(kb_code)
    items = []
    for item in STRATEGY_REGISTRY.values():
        required = getattr(item, "knowledge_base_code", None)
        if required and required != kb_code:
            continue
        if item.code in {"standard_document", "test_markdown"}:
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


@router.post("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry")
def retry_upload_file(
    kb_code: str, batch_id: str, file_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    _dataset_id(kb_code)
    payload = get_cleaning_batch(batch_id, session)
    if payload["knowledge_base_code"] != kb_code:
        raise ServiceError("VALIDATION_ERROR", "上传批次不属于该知识库", 404, False)
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
    return retry_cleaning_item(batch_id, file_id, item_id, session)


@router.post("/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions")
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
        markdown_text = (await markdown_file.read()).decode("utf-8-sig")
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
) -> dict[str, Any]:
    matches = [item for item in _documents(kb_code) if str(item.get("id") or "") == document_id]
    if not matches:
        raise ServiceError("VALIDATION_ERROR", "知识库文档不存在", 404, False)
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
    record_event(
        level="INFO", category="operation", component="knowledge_admin_api",
        event_code="KNOWLEDGE_DOCUMENT_STATUS_UPDATED", status="succeeded",
        message="知识库文档状态已更新", actor_hash="admin", resource_type="dify_document",
        resource_id=document_id, details={"kb_code": kb_code, "enabled": payload.enabled},
    )
    return {"document_id": document_id, "enabled": payload.enabled}


@router.delete("/{kb_code}/documents/{document_id}")
def delete_document(
    kb_code: str,
    document_id: str,
    payload: DeleteDocumentRequest,
) -> dict[str, Any]:
    matches = [item for item in _documents(kb_code) if str(item.get("id") or "") == document_id]
    if not matches:
        raise ServiceError("VALIDATION_ERROR", "知识库文档不存在", 404, False)
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
    record_event(
        level="INFO", category="operation", component="knowledge_admin_api",
        event_code="KNOWLEDGE_DOCUMENT_DELETED", status="succeeded", message="知识库文档已删除",
        actor_hash="admin", resource_type="dify_document", resource_id=document_id,
        details={"kb_code": kb_code, "name": actual_name},
    )
    return {"deleted": True, "document_id": document_id}
