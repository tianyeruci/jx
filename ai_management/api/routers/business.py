from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...cache import cache
from ...config import get_settings
from ...conversations import (
    delete_user_conversation,
    list_conversation_messages,
    list_user_conversations,
    prepare_business_chat,
)
from ...db import get_session
from ...dify import DifyAppClient
from ...errors import ServiceError
from ...models import BusinessConversation, BusinessMessage
from ...schemas import BusinessChatRequest, FeedbackRequest, RenameConversationRequest
from ...security import dify_user, require_internal_token, require_user_id, user_hash
from ...services.observability import record_event

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

router = APIRouter(
    prefix="/internal/v1/business",
    tags=["business"],
    dependencies=[Depends(require_internal_token)],
)


@router.post("/chat")
def business_chat(
    payload: BusinessChatRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    stream = prepare_business_chat(
        session,
        user_id=raw_user_id,
        user_hash=user_hash(raw_user_id),
        query=payload.query,
        conversation_id=payload.conversation_id,
        request_id=request.state.request_id,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chat/{task_id}/stop")
def stop_business_chat(
    task_id: str,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
) -> dict[str, Any]:
    settings = get_settings()
    cache.set_json(f"ai:business-task-stopped:{task_id}", True, ttl=600)
    try:
        result = DifyAppClient(
            settings.dify_url,
            settings.dify_business_app_api_key,
        ).stop_chat(task_id, dify_user(raw_user_id))
        record_event(
            level="INFO",
            category="operation",
            component="business_api",
            event_code="BUSINESS_GENERATION_STOPPED",
            status="succeeded",
            message="商业顾问生成已停止",
            request_id=request.state.request_id,
            actor_hash=user_hash(raw_user_id),
            resource_type="generation_task",
            resource_id=task_id,
        )
        return result
    except Exception:
        cache.delete(f"ai:business-task-stopped:{task_id}")
        raise


@router.get("/conversations")
def conversations(
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return list_user_conversations(session, user_hash(raw_user_id))


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: str,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return list_conversation_messages(session, user_hash(raw_user_id), conversation_id)


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = session.scalar(
        select(BusinessConversation).where(
            BusinessConversation.id == conversation_id,
            BusinessConversation.user_hash == user_hash(raw_user_id),
        )
    )
    if row is None:
        raise ServiceError("CONVERSATION_NOT_FOUND", "历史对话不存在", 404, False)
    title = payload.title.strip()
    if not title:
        raise ServiceError("VALIDATION_ERROR", "会话标题不能为空", 422, False)
    row.title = title
    session.commit()
    record_event(
        level="INFO",
        category="operation",
        component="business_api",
        event_code="CONVERSATION_RENAMED",
        status="succeeded",
        message="会话已重命名",
        request_id=request.state.request_id,
        actor_hash=user_hash(raw_user_id),
        resource_type="conversation",
        resource_id=row.id,
    )
    return {"conversation_id": row.id, "title": row.title}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    delete_user_conversation(
        session,
        user_hash=user_hash(raw_user_id),
        conversation_id=conversation_id,
    )
    record_event(
        level="INFO",
        category="operation",
        component="business_api",
        event_code="CONVERSATION_DELETED",
        status="succeeded",
        message="会话已删除",
        request_id=request.state.request_id,
        actor_hash=user_hash(raw_user_id),
        resource_type="conversation",
        resource_id=conversation_id,
    )
    return {"deleted": True}


@router.post("/messages/{message_id}/feedback")
def feedback(
    message_id: str,
    payload: FeedbackRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    message = session.scalar(
        select(BusinessMessage)
        .join(BusinessConversation)
        .where(
            BusinessMessage.id == message_id,
            BusinessConversation.user_hash == user_hash(raw_user_id),
        )
    )
    if message is None or not message.dify_message_id:
        raise ServiceError("VALIDATION_ERROR", "该消息暂不支持反馈", 400, False)
    settings = get_settings()
    result = DifyAppClient(
        settings.dify_url,
        settings.dify_business_app_api_key,
    ).feedback(
        message.dify_message_id,
        dify_user(raw_user_id),
        payload.rating,
        payload.content,
    )
    record_event(
        level="INFO",
        category="operation",
        component="business_api",
        event_code="MESSAGE_FEEDBACK_SUBMITTED",
        status="succeeded",
        message="消息反馈已提交",
        request_id=request.state.request_id,
        actor_hash=user_hash(raw_user_id),
        resource_type="message",
        resource_id=message_id,
        details={"rating": payload.rating},
    )
    return result
