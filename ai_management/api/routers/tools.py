from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...cache import cache
from ...config import get_settings
from ...db import get_session
from ...dify import DifyAppClient
from ...errors import ServiceError
from ...models import ToolConversation
from ...schemas import MomentsRunRequest, ToolConversationCreateRequest, WritingRunRequest
from ...security import dify_user, require_internal_token, require_user_id
from ...services.style_settings import get_style_prompt
from ...services.tool_conversations import create, current, is_reset_intent, owned, reset
from ...services.tool_examples import list_examples
from ...sse import SSEEmitter
from ...timeutil import now_beijing

logger = logging.getLogger(__name__)
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

router = APIRouter(
    prefix="/internal/v1/tools",
    tags=["tools"],
    dependencies=[Depends(require_internal_token)],
)


@router.get("/{kind}/examples")
def get_tool_examples(
    kind: str,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    return {"items": list_examples(session, kind, enabled_only=True)}


@router.get("/{kind}/conversations/current")
def get_current_conversation(
    kind: str, raw_user_id: str = Depends(require_user_id), session: Session = Depends(get_session)
):
    return current(session, raw_user_id, kind)


@router.post("/{kind}/conversations")
def create_tool_conversation(
    kind: str,
    payload: ToolConversationCreateRequest,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
):
    return create(session, raw_user_id, kind, payload.type, payload.output_format)


def _workflow_stream(
    *,
    client: DifyAppClient,
    inputs: dict[str, Any],
    anonymous_user: str,
    request_id: str,
    entry: str,
):
    started = time.monotonic()
    emit = SSEEmitter(request_id)
    first_token_ms: int | None = None
    yield emit("request_started")
    yield emit("status", {"code": "generating", "message": "正在生成内容…"})
    task_id = ""
    emitted_text = False
    metrics: dict[str, Any] = {}
    task_announced = False
    try:
        for item in client.stream_workflow(inputs=inputs, user=anonymous_user):
            task_id = str(item.get("task_id") or task_id)
            event_name = str(item.get("event") or "")
            data = item.get("data") or {}
            if task_id and not task_announced:
                task_announced = True
                cache.set_json(
                    f"ai:tool-task:{task_id}",
                    {"entry": entry, "user": anonymous_user, "transport": "workflow"},
                    ttl=600,
                )
                yield emit(
                    "status",
                    {
                        "code": "task_started",
                        "message": "已开始生成",
                        "task_id": task_id,
                    },
                )
            if event_name in {"text_chunk", "message", "agent_message"}:
                delta = str(data.get("text") or item.get("answer") or "")
                if delta:
                    if first_token_ms is None:
                        first_token_ms = int((time.monotonic() - started) * 1000)
                    emitted_text = True
                    yield emit("answer_delta", {"delta": delta})
            elif event_name == "workflow_finished":
                outputs = data.get("outputs") or {}
                text_value = outputs.get("text") or outputs.get("answer") or ""
                if text_value and not emitted_text:
                    if first_token_ms is None:
                        first_token_ms = int((time.monotonic() - started) * 1000)
                    yield emit("answer_delta", {"delta": str(text_value)})
                metrics = {
                    "elapsed_time": data.get("elapsed_time"),
                    "total_tokens": data.get("total_tokens"),
                    "total_steps": data.get("total_steps"),
                }
            elif event_name in {"error", "workflow_error"}:
                raise ServiceError(
                    "DIFY_UNAVAILABLE",
                    "内容生成失败，请稍后重试",
                    502,
                    True,
                )
    except GeneratorExit:
        if task_id:
            try:
                client.stop_workflow(task_id, anonymous_user)
            except Exception:
                logger.warning("failed to stop Workflow after client disconnect")
            cache.delete(f"ai:tool-task:{task_id}")
        raise
    except ServiceError as exc:
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
        yield emit(
            "error",
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": request_id,
                "retryable": exc.retryable,
            },
        )
        return
    except Exception:
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
        logger.exception("tool workflow stream failed request_id=%s entry=%s", request_id, entry)
        yield emit(
            "error",
            {
                "code": "UPSTREAM_ERROR",
                "message": "生成服务暂时不可用，请稍后重试",
                "request_id": request_id,
                "retryable": True,
            },
        )
        return
    yield emit(
        "message_end",
        {
            "request_id": request_id,
            "task_id": task_id or None,
            "first_token_ms": first_token_ms,
            "total_ms": int((time.monotonic() - started) * 1000),
            **metrics,
        },
    )
    logger.info(
        "workflow_completed request_id=%s entry=%s task_id=%s first_token_ms=%s "
        "total_ms=%s token_usage=%s",
        request_id,
        entry,
        task_id,
        first_token_ms,
        int((time.monotonic() - started) * 1000),
        metrics.get("total_tokens"),
    )
    if task_id:
        cache.delete(f"ai:tool-task:{task_id}")


def _chat_stream(
    *,
    client: DifyAppClient,
    row,
    query: str,
    inputs: dict[str, Any],
    anonymous_user: str,
    request_id: str,
    entry: str,
    session: Session,
):
    emit = SSEEmitter(request_id)
    yield emit("request_started")
    yield emit("status", {"code": "generating", "message": "正在生成内容…"})
    answer = ""
    task_id = ""
    task_announced = False
    lock_context = cache.lock(f"ai:tool-conversation:{row.user_hash}:{row.kind}", ttl=600)
    lock_state = lock_context.__enter__()
    if not lock_state.acquired:
        lock_context.__exit__(None, None, None)
        yield emit(
            "error",
            {
                "code": "CONFLICT",
                "message": "当前会话正在生成，请稍后重试",
                "request_id": request_id,
                "retryable": True,
            },
        )
        return
    try:
        # The route resolves the ORM row before StreamingResponse starts. A
        # prior request may have committed the Dify ID in the meantime, so
        # refresh inside the lock before the first Chatflow call.
        # Reset can win the race after the route resolved ``row`` but before
        # this generator acquires the lock. Re-query inside the lock so a
        # deleted session becomes a deterministic SSE 404 instead of HTTP 500.
        fresh = session.scalar(
            select(ToolConversation).where(
                ToolConversation.id == row.id,
                ToolConversation.user_hash == row.user_hash,
                ToolConversation.kind == row.kind,
                ToolConversation.status == "active",
            )
        )
        if fresh is None:
            yield emit(
                "error",
                {
                    "code": "NOT_FOUND",
                    "message": "会话不存在或无权访问",
                    "request_id": request_id,
                    "retryable": False,
                },
            )
            return
        row = fresh
        for item in client.stream_chat(
            query=query,
            user=anonymous_user,
            conversation_id=row.dify_conversation_id,
            inputs=inputs,
        ):
            data = item.get("data") or {}
            task_id = str(item.get("task_id") or data.get("task_id") or task_id)
            if task_id:
                cache.set_json(
                    f"ai:tool-task:{task_id}",
                    {"entry": entry, "user": anonymous_user, "transport": "chat"},
                    ttl=600,
                )
                if not task_announced:
                    task_announced = True
                    yield emit(
                        "status",
                        {"code": "task_started", "message": "已开始生成", "task_id": task_id},
                    )
            event = str(item.get("event") or "")
            delta = str(data.get("answer") or data.get("text") or item.get("answer") or "")
            if event in {"message", "agent_message", "text_chunk"} and delta:
                answer += delta
                yield emit("answer_delta", {"delta": delta})
            if event in {"message_end", "message_finished"}:
                row.dify_conversation_id = str(
                    data.get("conversation_id")
                    or item.get("conversation_id")
                    or row.dify_conversation_id
                )
                row.last_active_at = now_beijing()
                session.commit()
        # Dify owns the short-lived conversation memory.  Do not persist
        # user素材 or generated正文 locally; only keep the opaque Dify ID and
        # activity timestamp needed for retention and access control.
        row.last_active_at = now_beijing()
        session.commit()
    except GeneratorExit:
        if task_id:
            try:
                client.stop_chat(task_id, anonymous_user)
            except Exception:
                logger.warning("failed to stop Chat after client disconnect")
            cache.delete(f"ai:tool-task:{task_id}")
        raise
    except ServiceError as exc:
        session.rollback()
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
        yield emit(
            "error",
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": request_id,
                "retryable": exc.retryable,
            },
        )
        return
    except Exception:
        session.rollback()
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
        logger.exception("tool chat stream failed request_id=%s entry=%s", request_id, entry)
        yield emit(
            "error",
            {
                "code": "UPSTREAM_ERROR",
                "message": "生成服务暂时不可用，请稍后重试",
                "request_id": request_id,
                "retryable": True,
            },
        )
        return
    finally:
        lock_context.__exit__(None, None, None)
    yield emit("message_end", {"request_id": request_id, "task_id": task_id or None})
    if task_id:
        cache.delete(f"ai:tool-task:{task_id}")


def _reset_stream(request_id: str):
    emit = SSEEmitter(request_id)
    yield emit("request_started")
    yield emit("answer_delta", {"delta": "已开启新会话"})
    yield emit("message_end", {"request_id": request_id, "task_id": None})


@router.post("/{kind}/conversations/{conversation_id}/reset")
def reset_tool_conversation(
    kind: str,
    conversation_id: str,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
):
    return reset(session, raw_user_id, kind, conversation_id)


@router.post("/moments/run")
def run_moments(
    payload: MomentsRunRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    if is_reset_intent(payload.inputs.request):
        reset(session, raw_user_id, "moments", payload.conversation_id)
        return StreamingResponse(
            _reset_stream(request.state.request_id),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    row = owned(session, raw_user_id, "moments", payload.conversation_id)
    settings = get_settings()
    client = DifyAppClient(settings.dify_url, settings.dify_moments_app_api_key)
    return StreamingResponse(
        _chat_stream(
            client=client,
            row=row,
            query=payload.inputs.request,
            inputs={
                **payload.inputs.model_dump(),
                "type": row.tool_type,
                "admin_output_style": get_style_prompt(session, "moments"),
            },
            anonymous_user=dify_user(raw_user_id),
            request_id=request.state.request_id,
            entry="moments_advisor",
            session=session,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/writing/run")
def run_writing(
    payload: WritingRunRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    if is_reset_intent(payload.inputs.request):
        reset(session, raw_user_id, "writing", payload.conversation_id)
        return StreamingResponse(
            _reset_stream(request.state.request_id),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    row = owned(session, raw_user_id, "writing", payload.conversation_id)
    settings = get_settings()
    client = DifyAppClient(settings.dify_url, settings.dify_writing_app_api_key)
    return StreamingResponse(
        _chat_stream(
            client=client,
            row=row,
            query=payload.inputs.request,
            inputs={
                **payload.inputs.model_dump(),
                "type": row.tool_type,
                "format": row.output_format,
                "admin_output_style": get_style_prompt(session, "writing"),
            },
            anonymous_user=dify_user(raw_user_id),
            request_id=request.state.request_id,
            entry="writing_advisor",
            session=session,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{task_id}/stop")
def stop_tool(
    task_id: str,
    raw_user_id: str = Depends(require_user_id),
) -> dict[str, Any]:
    settings = get_settings()
    anonymous_user = dify_user(raw_user_id)
    mapping = cache.get_json(f"ai:tool-task:{task_id}")
    if not isinstance(mapping, dict) or mapping.get("user") != anonymous_user:
        raise ServiceError("VALIDATION_ERROR", "未找到可停止的任务", 404, False)
    entry = mapping.get("entry")
    transport = mapping.get("transport")
    if entry not in {"moments_advisor", "writing_advisor"} or transport not in {"workflow", "chat"}:
        raise ServiceError("VALIDATION_ERROR", "未找到可停止的任务", 404, False)
    app_key = (
        settings.dify_moments_app_api_key
        if entry == "moments_advisor"
        else settings.dify_writing_app_api_key
    )
    client = DifyAppClient(settings.dify_url, app_key)
    if transport == "workflow":
        return client.stop_workflow(task_id, anonymous_user)
    return client.stop_chat(task_id, anonymous_user)
