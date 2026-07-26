from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .cache import cache
from .cleaning import router as cleaning_router
from .config import get_settings
from .conversations import (
    delete_user_conversation,
    list_conversation_messages,
    list_user_conversations,
    prepare_business_chat,
)
from .courses import record_click
from .db import create_schema, get_session
from .dify import DifyAppClient
from .errors import ServiceError
from .models import (
    BusinessConversation,
    BusinessMessage,
    Course,
    CourseClickDaily,
    LearningRoute,
)
from .schemas import (
    BusinessChatRequest,
    FeedbackRequest,
    RenameConversationRequest,
    ToolRunRequest,
)
from .security import dify_user, require_internal_token, require_user_id, user_hash
from .sse import event
from .timeutil import business_today

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.upload_root.mkdir(parents=True, exist_ok=True)
    if settings.app_env != "production":
        create_schema()
    yield


app = FastAPI(
    title="jx-now AI Management Service",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(cleaning_router)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": request_id,
            "retryable": exc.retryable,
        },
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    raw_user_id = request.headers.get("X-User-Id")
    anonymous = user_hash(raw_user_id) if raw_user_id else ""
    logger.info(
        "request_accepted request_id=%s method=%s path=%s status=%s "
        "accept_ms=%s user_hash=%s",
        request.state.request_id,
        request.method,
        request.url.path,
        response.status_code,
        int((time.monotonic() - started) * 1000),
        anonymous,
    )
    return response


@app.post(
    "/internal/v1/business/chat",
    dependencies=[Depends(require_internal_token)],
)
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
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post(
    "/internal/v1/business/chat/{task_id}/stop",
    dependencies=[Depends(require_internal_token)],
)
def stop_business_chat(task_id: str, raw_user_id: str = Depends(require_user_id)) -> dict[str, Any]:
    settings = get_settings()
    cache.set_json(f"ai:business-task-stopped:{task_id}", True, ttl=600)
    try:
        return DifyAppClient(
            settings.dify_url,
            settings.dify_business_app_api_key,
        ).stop_chat(task_id, dify_user(raw_user_id))
    except Exception:
        cache.delete(f"ai:business-task-stopped:{task_id}")
        raise


@app.get(
    "/internal/v1/business/conversations",
    dependencies=[Depends(require_internal_token)],
)
def conversations(
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return list_user_conversations(session, user_hash(raw_user_id))


@app.get(
    "/internal/v1/business/conversations/{conversation_id}/messages",
    dependencies=[Depends(require_internal_token)],
)
def conversation_messages(
    conversation_id: str,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return list_conversation_messages(session, user_hash(raw_user_id), conversation_id)


@app.patch(
    "/internal/v1/business/conversations/{conversation_id}",
    dependencies=[Depends(require_internal_token)],
)
def rename_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
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
    return {"conversation_id": row.id, "title": row.title}


@app.delete(
    "/internal/v1/business/conversations/{conversation_id}",
    dependencies=[Depends(require_internal_token)],
)
def delete_conversation(
    conversation_id: str,
    raw_user_id: str = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    delete_user_conversation(
        session,
        user_hash=user_hash(raw_user_id),
        conversation_id=conversation_id,
    )
    return {"deleted": True}


@app.post(
    "/internal/v1/business/messages/{message_id}/feedback",
    dependencies=[Depends(require_internal_token)],
)
def feedback(
    message_id: str,
    payload: FeedbackRequest,
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
    return DifyAppClient(settings.dify_url, settings.dify_business_app_api_key).feedback(
        message.dify_message_id,
        dify_user(raw_user_id),
        payload.rating,
        payload.content,
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
    first_token_ms: int | None = None
    yield event("request_started", {"request_id": request_id})
    yield event("status", {"code": "generating", "message": "正在生成内容…"})
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
                yield event(
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
                    yield event("answer_delta", {"delta": delta})
            elif event_name == "workflow_finished":
                outputs = data.get("outputs") or {}
                text_value = outputs.get("text") or outputs.get("answer") or ""
                if text_value and not emitted_text:
                    if first_token_ms is None:
                        first_token_ms = int((time.monotonic() - started) * 1000)
                    yield event("answer_delta", {"delta": str(text_value)})
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
        yield event(
            "error",
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": request_id,
                "retryable": exc.retryable,
            },
        )
        return
    yield event(
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


@app.post(
    "/internal/v1/tools/moments/run",
    dependencies=[Depends(require_internal_token)],
)
def run_moments(
    payload: ToolRunRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
) -> StreamingResponse:
    settings = get_settings()
    client = DifyAppClient(settings.dify_url, settings.dify_moments_app_api_key)
    return StreamingResponse(
        _workflow_stream(
            client=client,
            inputs=payload.inputs,
            anonymous_user=dify_user(raw_user_id),
            request_id=request.state.request_id,
            entry="moments_advisor",
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post(
    "/internal/v1/tools/writing/run",
    dependencies=[Depends(require_internal_token)],
)
def run_writing(
    payload: ToolRunRequest,
    request: Request,
    raw_user_id: str = Depends(require_user_id),
) -> StreamingResponse:
    settings = get_settings()
    client = DifyAppClient(settings.dify_url, settings.dify_writing_app_api_key)
    return StreamingResponse(
        _workflow_stream(
            client=client,
            inputs=payload.inputs,
            anonymous_user=dify_user(raw_user_id),
            request_id=request.state.request_id,
            entry="writing_advisor",
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post(
    "/internal/v1/tools/{task_id}/stop",
    dependencies=[Depends(require_internal_token)],
)
def stop_tool(task_id: str, raw_user_id: str = Depends(require_user_id)) -> dict[str, Any]:
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
    errors: list[str] = []
    for key in (settings.dify_moments_app_api_key, settings.dify_writing_app_api_key):
        try:
            return DifyAppClient(settings.dify_url, key).stop_workflow(
                task_id, anonymous_user
            )
        except Exception as exc:
            errors.append(str(exc))
    raise ServiceError("DIFY_UNAVAILABLE", "未找到可停止的任务", 404, False)


@app.get(
    "/internal/v1/entry-config",
    dependencies=[Depends(require_internal_token)],
)
def entry_config() -> dict[str, Any]:
    settings = get_settings()
    definitions = [
        (
            "business_advisor",
            "商业顾问",
            settings.dify_business_app_api_key,
            {"history": True, "feedback": True, "course_recommendation": True},
        ),
        (
            "moments_advisor",
            "朋友圈顾问",
            settings.dify_moments_app_api_key,
            {"history": False, "feedback": False, "course_recommendation": False},
        ),
        (
            "writing_advisor",
            "日常写作",
            settings.dify_writing_app_api_key,
            {"history": False, "feedback": False, "course_recommendation": False},
        ),
    ]
    result: list[dict[str, Any]] = []
    for code, name, key, features in definitions:
        cache_key = f"ai:entry-config:{code}"
        parameters = cache.get_json(cache_key)
        if not isinstance(parameters, dict):
            parameters = {}
            try:
                parameters = DifyAppClient(
                    settings.dify_url,
                    key,
                    timeout_seconds=5,
                ).parameters()
                cache.set_json(cache_key, parameters, ttl=300)
            except Exception:
                pass
        result.append(
            {
                "code": code,
                "name": name,
                "opening_statement": parameters.get("opening_statement") or "",
                "suggested_questions": parameters.get("suggested_questions") or [],
                **features,
            }
        )
    return {"entries": result}


@app.get("/r/{token}")
def redirect_course(token: str, session: Session = Depends(get_session)) -> RedirectResponse:
    return RedirectResponse(record_click(session, token), status_code=302)


@app.get(
    "/internal/v1/admin/conversations",
    dependencies=[Depends(require_internal_token)],
)
def admin_conversations(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(BusinessConversation).order_by(BusinessConversation.last_active_at.desc()).limit(200)
        )
    )
    return [
        {
            "conversation_id": row.id,
            "user_hash": row.user_hash,
            "title": row.title,
            "summary": row.summary,
            "summary_gap": row.summary_gap,
            "last_active_at": row.last_active_at,
        }
        for row in rows
    ]


@app.get(
    "/internal/v1/admin/conversations/{conversation_id}",
    dependencies=[Depends(require_internal_token)],
)
def admin_conversation_detail(
    conversation_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    conversation = session.get(BusinessConversation, conversation_id)
    if conversation is None:
        raise ServiceError("CONVERSATION_NOT_FOUND", "历史对话不存在", 404, False)
    messages = list(
        session.scalars(
            select(BusinessMessage)
            .where(BusinessMessage.conversation_id == conversation_id)
            .order_by(BusinessMessage.created_at.asc())
        )
    )
    return {
        "conversation_id": conversation.id,
        "user_hash": conversation.user_hash,
        "title": conversation.title,
        "summary": conversation.summary,
        "messages": [
            {
                "message_id": item.id,
                "role": item.role,
                "content": item.content,
                "status": item.status,
                "token_usage": item.token_usage,
                "first_token_ms": item.first_token_ms,
                "total_ms": item.total_ms,
                "recommendation": item.recommendation,
                "created_at": item.created_at,
            }
            for item in messages
        ],
    }


@app.get(
    "/internal/v1/admin/course-clicks",
    dependencies=[Depends(require_internal_token)],
)
def admin_course_clicks(
    from_date: date | None = None,
    to_date: date | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(CourseClickDaily)
    if from_date:
        query = query.where(CourseClickDaily.click_date >= from_date)
    if to_date:
        query = query.where(CourseClickDaily.click_date <= to_date)
    rows = list(session.scalars(query.order_by(CourseClickDaily.click_date.desc())))
    result = [
        {
            "course_id": row.course_id,
            "date": row.click_date,
            "clicks": row.clicks,
            "unique_users": row.unique_users,
        }
        for row in rows
    ]
    today = business_today()
    if (from_date is None or from_date <= today) and (
        to_date is None or to_date >= today
    ):
        for course_id in session.scalars(select(Course.course_id)):
            clicks, unique_users = cache.click_counts(
                course_id,
                today.isoformat(),
            )
            if clicks or unique_users:
                result.append(
                    {
                        "course_id": course_id,
                        "date": today,
                        "clicks": clicks,
                        "unique_users": unique_users,
                        "live": True,
                    }
                )
    return result


@app.get(
    "/internal/v1/admin/catalog",
    dependencies=[Depends(require_internal_token)],
)
def admin_catalog(session: Session = Depends(get_session)) -> dict[str, Any]:
    courses = list(session.scalars(select(Course).order_by(Course.course_id)))
    routes = list(session.scalars(select(LearningRoute).order_by(LearningRoute.route_id)))
    return {
        "courses": [
            {
                "course_id": item.course_id,
                "name": item.name,
                "direction": item.direction,
                "enabled": item.enabled,
                "review_status": item.review_status,
                "optional_data": item.optional_data,
            }
            for item in courses
        ],
        "routes": [
            {
                "route_id": item.route_id,
                "name": item.name,
                "direction": item.direction,
                "enabled": item.enabled,
                "review_status": item.review_status,
                "stages": item.stages,
            }
            for item in routes
        ],
    }


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready(session: Session = Depends(get_session)) -> JSONResponse:
    settings = get_settings()
    database_ok = False
    try:
        session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        pass
    redis_ok = cache.ping()
    apps_configured = bool(
        settings.dify_business_app_api_key
        and settings.dify_moments_app_api_key
        and settings.dify_writing_app_api_key
        and settings.dify_summary_profile_app_api_key
    )
    knowledge_configured = bool(
        settings.dify_knowledge_api_key
        and settings.dify_general_dataset_id
        and settings.dify_course_dataset_id
    )
    configured = apps_configured and knowledge_configured
    dify_reachable = False
    if apps_configured:
        cached_dify_health = cache.get_json("ai:health:dify")
        if isinstance(cached_dify_health, dict):
            dify_reachable = bool(cached_dify_health.get("reachable"))
        else:
            try:
                DifyAppClient(
                    settings.dify_url,
                    settings.dify_business_app_api_key,
                    timeout_seconds=3,
                ).parameters()
                dify_reachable = True
            except Exception:
                dify_reachable = False
            cache.set_json(
                "ai:health:dify",
                {"reachable": dify_reachable},
                ttl=30,
            )
    ok = database_ok and (redis_ok or settings.app_env != "production") and configured
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not_ready",
            "database": database_ok,
            "redis": redis_ok,
            "dify_apps_configured": apps_configured,
            "dify_knowledge_configured": knowledge_configured,
            "dify_reachable": dify_reachable,
        },
    )


def run() -> None:
    import uvicorn

    uvicorn.run("ai_management.main:app", host="0.0.0.0", port=8000, reload=False)
