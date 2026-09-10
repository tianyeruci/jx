from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
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
from ...schemas import (
    FeedbackRequest,
    MomentsInputs,
    MomentsRunRequest,
    ToolConversationCreateRequest,
    WritingInputs,
    WritingRunRequest,
)
from ...security import dify_user, require_internal_token, require_user_id, user_hash
from ...services.observability import record_event
from ...services.style_settings import get_style_prompt
from ...services.tool_conversations import _current, create, current, is_reset_intent, owned, reset
from ...services.tool_examples import list_examples
from ...sse import SSEEmitter

logger = logging.getLogger(__name__)
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
STOP_CONFIRMATION_TIMEOUT_SECONDS = 185
_TOOL_STOP_BEFORE_MARKER_HOOK = None
_TOOL_TERMINAL_LINEARIZATION_HOOK = None

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
            cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
            cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
        raise
    except ServiceError as exc:
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
            cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
            cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
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


def _wait_for_tool_stop(task_id: str, anonymous_user: str) -> bool:
    """Wait for stop_tool's remote confirmation without racing its claim."""
    stopping_key = f"ai:tool-task-stopping:{task_id}:{anonymous_user}"
    stopped_key = f"ai:tool-task-stopped:{task_id}:{anonymous_user}"
    if not cache.get_json(stopping_key) and not cache.get_json(stopped_key):
        return False
    deadline = time.monotonic() + STOP_CONFIRMATION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if cache.get_json(stopped_key):
            return True
        if not cache.get_json(stopping_key):
            return False
        time.sleep(0.1)
    return bool(cache.get_json(stopped_key))


def _claim_tool_terminal(task_id: str):
    """Coordinate terminal cleanup with stop_tool; never steal its claim."""
    # Allow the stop owner to finish its remote call and release the claim;
    # the marker is the durable evidence of the stopped terminal state.
    deadline = time.monotonic() + STOP_CONFIRMATION_TIMEOUT_SECONDS + 30
    while True:
        claim = cache.lock(f"ai:tool-stop-claim:{task_id}", ttl=30)
        state = claim.__enter__()
        if state.acquired:
            return claim
        claim.__exit__(None, None, None)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


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
    actor_hash: str = "",
    acquired_lock: Any | None = None,
    operation: str = "generation",
):
    emit = SSEEmitter(request_id)
    answer = ""
    task_id = ""
    message_id = ""
    stopped = False
    saw_message_end = False
    terminalized = False
    terminal_status: str | None = None
    task_announced = False
    lock_context = acquired_lock or cache.lock(
        f"ai:tool-conversation:{row.user_hash}:{row.kind}", ttl=600
    )
    lock_state = (
        type("LockState", (), {"acquired": True})()
        if acquired_lock is not None
        else lock_context.__enter__()
    )
    if not lock_state.acquired:
        if acquired_lock is None:
            lock_context.__exit__(None, None, None)
        yield emit(
            "error",
            {
                "code": "CONFLICT",
                "message": "当前会话正在生成，请稍后重试",
                "request_id": request_id,
                "retryable": True,
                "conversation_id": row.id,
                "status": "failed",
            },
        )
        return
    try:
        yield emit("request_started")
        yield emit("status", {"code": "generating", "message": "正在生成内容…"})
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
                    "conversation_id": row.id,
                    "status": "failed",
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
            if not isinstance(item, dict):
                raise ServiceError("DIFY_RESPONSE_INVALID", "Dify流事件格式无效", 502, True)
            raw_data = item.get("data")
            data = {} if raw_data is None else raw_data
            if not isinstance(data, dict):
                raise ServiceError("DIFY_RESPONSE_INVALID", "Dify流事件格式无效", 502, True)
            task_id = str(item.get("task_id") or data.get("task_id") or task_id)
            message_id = str(item.get("message_id") or data.get("message_id") or message_id)
            if task_id:
                cache.set_json(
                    f"ai:tool-task:{task_id}",
                    {
                        "entry": entry,
                        "user": anonymous_user,
                        "transport": "chat",
                        "conversation_id": row.id,
                    },
                    ttl=600,
                )
                if not task_announced:
                    task_announced = True
                    yield emit("status", {"code": "task_started", "task_id": task_id})
            event = str(item.get("event") or "")
            if event in {"error", "workflow_error"}:
                raise ServiceError("DIFY_RESPONSE_INVALID", "Dify生成失败", 502, True)
            remote_conversation_id = data.get("conversation_id") or item.get("conversation_id")
            if remote_conversation_id:
                if row.dify_conversation_id and str(remote_conversation_id) != str(
                    row.dify_conversation_id
                ):
                    raise ServiceError(
                        "DIFY_RESPONSE_INVALID", "Dify会话标识与当前会话不一致", 502, True
                    )
                if not row.dify_conversation_id:
                    # Persist the first opaque Dify conversation ID early so
                    # a stopped/disconnected first run can still be deleted
                    # precisely. Repeated token events must not commit.
                    row.dify_conversation_id = str(remote_conversation_id)
                    row.last_active_at = datetime.now(timezone.utc)
                    session.commit()
            delta = str(data.get("answer") or data.get("text") or item.get("answer") or "")
            if event in {"message", "agent_message", "text_chunk"} and delta:
                answer += delta
                yield emit("answer_delta", {"delta": delta})
            if task_id and cache.get_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}"):
                stopped = True
                break
            if event in {"message_end", "message_finished"}:
                saw_message_end = True
                message_id = str(
                    data.get("message_id")
                    or data.get("id")
                    or item.get("message_id")
                    or item.get("id")
                    or message_id
                )
                row.dify_conversation_id = str(
                    data.get("conversation_id")
                    or item.get("conversation_id")
                    or row.dify_conversation_id
                )
                row.last_active_at = datetime.now(timezone.utc)
        # Dify owns the short-lived conversation memory.  Do not persist
        # user素材 or generated正文 locally; only keep the opaque Dify ID and
        # activity timestamp needed for retention and access control.
        row.last_active_at = datetime.now(timezone.utc)
        session.commit()
        if task_id and cache.get_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}"):
            stopped = True
        if (
            task_id
            and cache.get_json(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
            and not stopped
        ):
            deadline = time.monotonic() + STOP_CONFIRMATION_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                time.sleep(0.1)
                if cache.get_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}"):
                    stopped = True
                    break
                stopping = cache.get_json(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
                if not stopping or (
                    isinstance(stopping, dict) and stopping.get("status") == "failed"
                ):
                    break
        stopping = (
            cache.get_json(f"ai:tool-task-stopping:{task_id}:{anonymous_user}") if task_id else None
        )
        if task_id and stopping is True and not stopped:
            raise ServiceError("DIFY_UNAVAILABLE", "停止请求尚未确认", 502, True)
        if task_id and (saw_message_end or stopped):
            terminal_claim = _claim_tool_terminal(task_id)
            if terminal_claim is None:
                raise ServiceError("DIFY_RESPONSE_INVALID", "生成终态协调失败", 502, True)
            if _TOOL_TERMINAL_LINEARIZATION_HOOK is not None:
                _TOOL_TERMINAL_LINEARIZATION_HOOK(task_id)
            stopped = stopped or bool(
                cache.get_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
            )
            terminal_status = "stopped" if stopped else "completed"
            cache.delete(f"ai:tool-task:{task_id}")
            cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
            cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
            terminal_claim.__exit__(None, None, None)
            terminalized = True
        if not saw_message_end and not stopped:
            raise ServiceError("DIFY_RESPONSE_INVALID", "Dify流未正常结束", 502, True)
        if terminal_status != "stopped" and not message_id:
            raise ServiceError("DIFY_RESPONSE_INVALID", "Dify未返回消息标识", 502, True)
    except GeneratorExit:
        if task_id:
            terminal_claim = _claim_tool_terminal(task_id)
            if terminal_claim is not None:
                already_stopped = bool(
                    cache.get_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
                )
                stopping = cache.get_json(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
                if not already_stopped and not stopping:
                    try:
                        client.stop_chat(task_id, anonymous_user)
                    except Exception:
                        logger.warning("failed to stop Chat after client disconnect")
                cache.delete(f"ai:tool-task:{task_id}")
                cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
                cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
                terminal_claim.__exit__(None, None, None)
        raise
    except ServiceError as exc:
        stop_confirmed = False
        if task_id:
            terminal_claim = _claim_tool_terminal(task_id)
            if terminal_claim is not None:
                stop_confirmed = bool(
                    cache.get_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
                )
                cache.delete(f"ai:tool-task:{task_id}")
                cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
                cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
                terminal_claim.__exit__(None, None, None)
        if stop_confirmed:
            record_event(
                level="INFO",
                category="operation",
                component="tools_api",
                event_code="TOOL_MESSAGE_REGENERATE_TERMINAL"
                if operation == "regenerate"
                else "TOOL_GENERATION_TERMINAL",
                status="stopped",
                message="工具生成结束",
                request_id=request_id,
                actor_hash=actor_hash or user_hash(anonymous_user),
                resource_type="generation_task",
                resource_id=task_id,
                details={"entry": entry, "message_id": message_id or None, "status": "stopped"},
            )
            yield emit(
                "message_end",
                {
                    "request_id": request_id,
                    "conversation_id": row.id,
                    "message_id": message_id or None,
                    "task_id": task_id,
                    "status": "stopped",
                },
            )
            return
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
            cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
            cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
        record_event(
            level="INFO",
            category="operation",
            component="tools_api",
            event_code="TOOL_MESSAGE_REGENERATE_TERMINAL"
            if operation == "regenerate"
            else "TOOL_GENERATION_TERMINAL",
            status="failed",
            message="工具生成结束",
            request_id=request_id,
            actor_hash=actor_hash or user_hash(anonymous_user),
            resource_type="generation_task",
            resource_id=task_id or row.id,
            details={"entry": entry, "message_id": message_id or None, "status": "failed"},
        )
        yield emit(
            "error",
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": request_id,
                "retryable": exc.retryable,
                "conversation_id": row.id,
                "message_id": message_id or None,
                "task_id": task_id or None,
                "status": "failed",
            },
        )
        return
    except Exception:
        stop_confirmed = False
        if task_id:
            terminal_claim = _claim_tool_terminal(task_id)
            if terminal_claim is not None:
                stop_confirmed = bool(
                    cache.get_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
                )
                cache.delete(f"ai:tool-task:{task_id}")
                cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
                cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
                terminal_claim.__exit__(None, None, None)
        if stop_confirmed:
            yield emit(
                "message_end",
                {
                    "request_id": request_id,
                    "conversation_id": row.id,
                    "message_id": message_id or None,
                    "task_id": task_id,
                    "status": "stopped",
                },
            )
            return
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
            cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
            cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
        record_event(
            level="INFO",
            category="operation",
            component="tools_api",
            event_code="TOOL_MESSAGE_REGENERATE_TERMINAL"
            if operation == "regenerate"
            else "TOOL_GENERATION_TERMINAL",
            status="failed",
            message="工具生成结束",
            request_id=request_id,
            actor_hash=actor_hash or user_hash(anonymous_user),
            resource_type="generation_task",
            resource_id=task_id or row.id,
            details={"entry": entry, "message_id": message_id or None, "status": "failed"},
        )
        yield emit(
            "error",
            {
                "code": "DIFY_RESPONSE_INVALID",
                "message": "Dify流事件格式无效",
                "request_id": request_id,
                "conversation_id": row.id,
                "task_id": task_id or None,
                "message_id": message_id or None,
                "status": "failed",
                "retryable": True,
            },
        )
        return
    finally:
        lock_context.__exit__(None, None, None)
    terminal_claim = _claim_tool_terminal(task_id) if task_id and not terminalized else None
    if task_id and not terminalized and terminal_claim is None:
        yield emit(
            "error",
            {
                "code": "DIFY_RESPONSE_INVALID",
                "message": "生成终态协调失败",
                "request_id": request_id,
                "conversation_id": row.id,
                "message_id": message_id or None,
                "task_id": task_id or None,
                "status": "failed",
                "retryable": True,
            },
        )
        return
    if terminal_claim is not None:
        # stop_tool re-reads the mapping only after taking this same claim.
        # Whichever side wins therefore owns the terminal transition.
        if task_id and _wait_for_tool_stop(task_id, anonymous_user):
            stopped = True
        if task_id:
            cache.delete(f"ai:tool-task:{task_id}")
            cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
            cache.delete(f"ai:tool-task-stopping:{task_id}:{anonymous_user}")
        terminal_claim.__exit__(None, None, None)
    yield emit(
        "message_end",
        {
            "request_id": request_id,
            "conversation_id": row.id,
            "message_id": message_id or None,
            "task_id": task_id or None,
            "status": "stopped" if stopped else "completed",
        },
    )
    record_event(
        level="INFO",
        category="operation",
        component="tools_api",
        event_code="TOOL_MESSAGE_REGENERATE_TERMINAL"
        if operation == "regenerate"
        else "TOOL_GENERATION_TERMINAL",
        status="stopped" if stopped else "completed",
        message="工具生成结束",
        request_id=request_id,
        actor_hash=actor_hash or user_hash(anonymous_user),
        resource_type="generation_task",
        resource_id=task_id or row.id,
        details={
            "entry": entry,
            "message_id": message_id or None,
            "status": "stopped" if stopped else "completed",
        },
    )


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


def _tool_client(kind: str) -> DifyAppClient:
    settings = get_settings()
    if kind not in {"moments", "writing"}:
        raise ServiceError("MESSAGE_NOT_FOUND", "消息不存在", 404, False)
    key = (
        settings.dify_moments_app_api_key
        if kind == "moments"
        else settings.dify_writing_app_api_key
    )
    return DifyAppClient(settings.dify_url, key)


def _find_tool_message(
    client: DifyAppClient, row: ToolConversation, raw_user_id: str, message_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    messages = client.list_messages(
        conversation_id=row.dify_conversation_id, user=dify_user(raw_user_id)
    )
    target = next(
        (
            item
            for item in messages
            if str(item.get("id") or item.get("message_id") or "") == message_id
        ),
        None,
    )
    if target is None:
        raise ServiceError("MESSAGE_NOT_FOUND", "消息不存在", 404, False)
    return target, messages


def _audit_tool_action(
    request: Request, raw_user_id: str, kind: str, code: str, message_id: str, status: str
) -> None:
    record_event(
        level="INFO",
        category="operation",
        component="tools_api",
        event_code=code,
        status=status,
        message="工具消息操作",
        request_id=request.state.request_id,
        actor_hash=user_hash(raw_user_id),
        resource_type="tool_message",
        resource_id=message_id,
        details={"kind": kind},
    )


@router.post("/{kind}/messages/{message_id}/feedback")
def tool_feedback(
    kind: str,
    message_id: str,
    payload: FeedbackRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if kind not in {"moments", "writing"}:
        raise ServiceError("MESSAGE_NOT_FOUND", "消息不存在", 404, False)
    lock = cache.lock(f"ai:tool-conversation:{user_hash(raw_user_id)}:{kind}", ttl=600)
    if not lock.__enter__().acquired:
        lock.__exit__(None, None, None)
        raise ServiceError("CONFLICT", "当前会话正在生成，请稍后重试", 409, True)
    try:
        row = _current(session, raw_user_id, kind)
        if row is None or not row.dify_conversation_id:
            raise ServiceError("MESSAGE_NOT_FOUND", "消息不存在", 404, False)
        client = _tool_client(kind)
        _find_tool_message(client, row, raw_user_id, message_id)
        result = client.feedback(
            message_id, dify_user(raw_user_id), payload.rating, payload.content
        )
    finally:
        lock.__exit__(None, None, None)
    _audit_tool_action(request, raw_user_id, kind, "TOOL_MESSAGE_FEEDBACK", message_id, "succeeded")
    return result


@router.post("/{kind}/messages/{message_id}/regenerate")
def tool_regenerate(
    kind: str,
    message_id: str,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    if kind not in {"moments", "writing"}:
        raise ServiceError("MESSAGE_NOT_FOUND", "消息不存在", 404, False)
    operation_lock = cache.lock(f"ai:tool-conversation:{user_hash(raw_user_id)}:{kind}", ttl=600)
    if not operation_lock.__enter__().acquired:
        operation_lock.__exit__(None, None, None)
        raise ServiceError("CONFLICT", "当前会话正在生成，请稍后重试", 409, True)
    transferred_to_stream = False
    try:
        row = _current(session, raw_user_id, kind)
        if row is None or not row.dify_conversation_id:
            raise ServiceError("MESSAGE_NOT_FOUND", "消息不存在", 404, False)
        client = _tool_client(kind)
        target, messages = _find_tool_message(client, row, raw_user_id, message_id)

        def _created(item: dict[str, Any]) -> tuple[int, float]:
            value = item.get("created_at") if "created_at" in item else item.get("createdAt")
            if isinstance(value, bool):
                return (0, 0.0)
            try:
                epoch = float(value)
                return (1, epoch) if math.isfinite(epoch) else (0, 0.0)
            except (TypeError, ValueError):
                if not isinstance(value, str) or not value.strip():
                    return (0, 0.0)
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return (1, parsed.timestamp())
                except (TypeError, ValueError, OverflowError):
                    return (0, 0.0)

        answer_messages = [item for item in messages if item.get("answer") is not None]
        candidates = answer_messages or messages
        if any(_created(item)[0] == 0 for item in candidates):
            raise ServiceError("DIFY_RESPONSE_INVALID", "Dify消息缺少创建时间", 502, True)
        newest = max(enumerate(candidates), key=lambda pair: (_created(pair[1]), pair[0]))[1]
        if str(newest.get("id") or newest.get("message_id") or "") != message_id:
            raise ServiceError("MESSAGE_NOT_REGENERATABLE", "该消息不可重新生成", 409, False)
        raw_inputs = target.get("inputs")
        if not isinstance(raw_inputs, dict):
            raise ServiceError("DIFY_RESPONSE_INVALID", "Dify消息输入格式无效", 502, True)
        query = target.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ServiceError("DIFY_RESPONSE_INVALID", "Dify消息缺少原始问题", 502, False)
        try:
            if kind == "moments":
                parsed = MomentsInputs.model_validate(
                    {
                        **{k: raw_inputs[k] for k in MomentsInputs.model_fields if k in raw_inputs},
                        # Dify 1.13.3 stores the chat request in top-level
                        # ``query`` rather than echoing it as ``inputs.request``.
                        "request": query,
                    }
                )
                inputs = {
                    **parsed.model_dump(),
                    "type": row.tool_type,
                    "admin_output_style": get_style_prompt(session, "moments"),
                }
            else:
                parsed = WritingInputs.model_validate(
                    {
                        **{k: raw_inputs[k] for k in WritingInputs.model_fields if k in raw_inputs},
                        "request": query,
                    }
                )
                inputs = {
                    **parsed.model_dump(),
                    "type": row.tool_type,
                    "format": row.output_format,
                    "admin_output_style": get_style_prompt(session, "writing"),
                }
        except Exception as exc:
            raise ServiceError("DIFY_RESPONSE_INVALID", "Dify消息输入格式无效", 502, False) from exc
        _audit_tool_action(
            request, raw_user_id, kind, "TOOL_MESSAGE_REGENERATE", message_id, "accepted"
        )
        stream = _chat_stream(
            client=client,
            row=row,
            query=query,
            inputs=inputs,
            anonymous_user=dify_user(raw_user_id),
            request_id=request.state.request_id,
            entry=f"{kind}_advisor",
            session=session,
            actor_hash=user_hash(raw_user_id),
            acquired_lock=operation_lock,
            operation="regenerate",
        )
        transferred_to_stream = True
        return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
    finally:
        if not transferred_to_stream:
            operation_lock.__exit__(None, None, None)


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
            actor_hash=user_hash(raw_user_id),
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
            actor_hash=user_hash(raw_user_id),
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{task_id}/stop")
def stop_tool(
    task_id: str,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
) -> dict[str, Any]:
    settings = get_settings()
    anonymous_user = dify_user(raw_user_id)
    mapping = cache.get_json(f"ai:tool-task:{task_id}")
    if isinstance(mapping, dict):
        if mapping.get("user") != anonymous_user:
            raise ServiceError("TASK_NOT_FOUND", "未找到可停止的任务", 404, False)
        if (
            mapping.get("entry") not in {"moments_advisor", "writing_advisor"}
            or mapping.get("transport") != "chat"
        ):
            raise ServiceError("TASK_NOT_FOUND", "未找到可停止的任务", 404, False)
        app_key = (
            settings.dify_moments_app_api_key
            if mapping.get("entry") == "moments_advisor"
            else settings.dify_writing_app_api_key
        )
        client = DifyAppClient(settings.dify_url, app_key)
        # The claim and its visible marker must cover the full Dify stop
        # request.  Otherwise a slow-but-valid stop could lose its 60-second
        # marker and let a second request stop the same task again.
        claim_lock = cache.lock(
            f"ai:tool-stop-claim:{task_id}", ttl=STOP_CONFIRMATION_TIMEOUT_SECONDS
        )
        if not claim_lock.__enter__().acquired:
            claim_lock.__exit__(None, None, None)
            raise ServiceError("TASK_NOT_FOUND", "未找到可停止的任务", 404, False)
        mapping = cache.get_json(f"ai:tool-task:{task_id}")
        if (
            not isinstance(mapping, dict)
            or mapping.get("user") != anonymous_user
            or mapping.get("entry") not in {"moments_advisor", "writing_advisor"}
            or mapping.get("transport") != "chat"
        ):
            claim_lock.__exit__(None, None, None)
            raise ServiceError("TASK_NOT_FOUND", "未找到可停止的任务", 404, False)
        if cache.get_json(f"ai:tool-task-stopping:{task_id}:{anonymous_user}") or cache.get_json(
            f"ai:tool-task-stopped:{task_id}:{anonymous_user}"
        ):
            claim_lock.__exit__(None, None, None)
            raise ServiceError("TASK_NOT_FOUND", "未找到可停止的任务", 404, False)
        if _TOOL_STOP_BEFORE_MARKER_HOOK is not None:
            _TOOL_STOP_BEFORE_MARKER_HOOK(task_id)
        stopping_key = f"ai:tool-task-stopping:{task_id}:{anonymous_user}"
        cache.set_json(stopping_key, True, ttl=STOP_CONFIRMATION_TIMEOUT_SECONDS)
        record_event(
            level="INFO",
            category="operation",
            component="tools_api",
            event_code="TOOL_GENERATION_STOP_ACCEPTED",
            status="accepted",
            message="工具生成停止已受理",
            request_id=request.state.request_id,
            actor_hash=user_hash(raw_user_id),
            resource_type="generation_task",
            resource_id=task_id,
            details={"entry": mapping.get("entry")},
        )
        try:
            result = client.stop_chat(task_id, anonymous_user)
            cache.set_json(f"ai:tool-task-stopped:{task_id}:{anonymous_user}", True, ttl=600)
            cache.delete(stopping_key)
            record_event(
                level="INFO",
                category="operation",
                component="tools_api",
                event_code="TOOL_GENERATION_STOP_TERMINAL",
                status="succeeded",
                message="工具生成停止结束",
                request_id=request.state.request_id,
                actor_hash=user_hash(raw_user_id),
                resource_type="generation_task",
                resource_id=task_id,
                details={"entry": mapping.get("entry"), "status": "succeeded"},
            )
            return result
        except Exception:
            cache.set_json(
                f"ai:tool-task-stopping:{task_id}:{anonymous_user}",
                {"status": "failed"},
                ttl=STOP_CONFIRMATION_TIMEOUT_SECONDS,
            )
            cache.delete(f"ai:tool-task-stopped:{task_id}:{anonymous_user}")
            record_event(
                level="INFO",
                category="operation",
                component="tools_api",
                event_code="TOOL_GENERATION_STOP_TERMINAL",
                status="failed",
                message="工具生成停止结束",
                request_id=request.state.request_id,
                actor_hash=user_hash(raw_user_id),
                resource_type="generation_task",
                resource_id=task_id,
                details={"entry": mapping.get("entry"), "status": "failed"},
            )
            raise
        finally:
            claim_lock.__exit__(None, None, None)
    raise ServiceError("TASK_NOT_FOUND", "未找到可停止的任务", 404, False)
