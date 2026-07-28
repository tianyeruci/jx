from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...cache import cache
from ...config import get_settings
from ...db import get_session
from ...dify import DifyAppClient
from ...errors import ServiceError
from ...schemas import MomentsRunRequest, WritingRunRequest
from ...security import dify_user, require_internal_token, require_user_id
from ...services.style_settings import get_style_prompt
from ...sse import SSEEmitter

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
                    {"entry": entry, "user": anonymous_user},
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


@router.post("/moments/run")
def run_moments(
    payload: MomentsRunRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    settings = get_settings()
    client = DifyAppClient(settings.dify_url, settings.dify_moments_app_api_key)
    return StreamingResponse(
        _workflow_stream(
            client=client,
            inputs={
                **payload.inputs.model_dump(),
                "admin_output_style": get_style_prompt(session, "moments"),
            },
            anonymous_user=dify_user(raw_user_id),
            request_id=request.state.request_id,
            entry="moments_advisor",
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
    settings = get_settings()
    client = DifyAppClient(settings.dify_url, settings.dify_writing_app_api_key)
    return StreamingResponse(
        _workflow_stream(
            client=client,
            inputs={
                **payload.inputs.model_dump(),
                "admin_output_style": get_style_prompt(session, "writing"),
            },
            anonymous_user=dify_user(raw_user_id),
            request_id=request.state.request_id,
            entry="writing_advisor",
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
    if isinstance(mapping, dict):
        if mapping.get("user") != anonymous_user:
            raise ServiceError("VALIDATION_ERROR", "未找到可停止的任务", 404, False)
        app_key = (
            settings.dify_moments_app_api_key
            if mapping.get("entry") == "moments_advisor"
            else settings.dify_writing_app_api_key
        )
        return DifyAppClient(settings.dify_url, app_key).stop_workflow(
            task_id,
            anonymous_user,
        )
    for key in (settings.dify_moments_app_api_key, settings.dify_writing_app_api_key):
        try:
            return DifyAppClient(settings.dify_url, key).stop_workflow(
                task_id,
                anonymous_user,
            )
        except Exception:
            continue
    raise ServiceError("DIFY_UNAVAILABLE", "未找到可停止的任务", 404, False)
