from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .cache import cache
from .config import get_settings
from .context import build_context, should_summarize
from .courses import validate_recommendation
from .db import SessionLocal
from .dify import DifyAppClient, extract_json_object
from .errors import ServiceError
from .models import (
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    ExternalDeletionTask,
)
from .schemas import RecommendationDecision
from .security import dify_user
from .sse import event
from .stream_filter import StreamingUrlRedactor
from .timeutil import business_today

logger = logging.getLogger(__name__)


def _business_client() -> DifyAppClient:
    settings = get_settings()
    return DifyAppClient(settings.dify_url, settings.dify_business_app_api_key)


def _decision_from_outputs(outputs: Any) -> RecommendationDecision | None:
    if not isinstance(outputs, dict):
        return None
    candidates: list[Any] = [
        outputs.get("recommendation_decision"),
        outputs.get("decision_json"),
        outputs.get("decision"),
        # LLM nodes in Dify 1.13.x expose their structured JSON as ``text``.
        # Keep this before the whole-output fallback, which would otherwise
        # validate ``{"text": ...}`` as an empty decision.
        outputs.get("text"),
    ]
    # Some DSLs expose decision fields directly as node outputs. Only accept
    # the whole mapping when it actually contains decision fields; arbitrary
    # node outputs such as ``{"answer": ...}`` or ``{"text": prose}``
    # must not become a valid all-default decision and overwrite the real one.
    if any(key in outputs for key in ("intent", "candidate_course_ids", "candidate_route_id")):
        candidates.append(outputs)
    for candidate in candidates:
        value = extract_json_object(candidate)
        if not value:
            continue
        try:
            return RecommendationDecision.model_validate(value)
        except Exception:
            continue
    return None


def _store_segment(
    session: Session,
    *,
    conversation: BusinessConversation,
    dify_conversation_id: str,
    anonymous_user: str,
    user_message: BusinessMessage,
    assistant_message: BusinessMessage,
) -> BusinessConversationSegment:
    segment = session.scalar(
        select(BusinessConversationSegment).where(
            BusinessConversationSegment.conversation_id == conversation.id,
            BusinessConversationSegment.segment_date == business_today(),
        )
    )
    if segment is None:
        segment = BusinessConversationSegment(
            conversation_id=conversation.id,
            segment_date=business_today(),
            dify_conversation_id=dify_conversation_id,
            dify_user=anonymous_user,
            last_message_at=datetime.now(timezone.utc),
        )
        session.add(segment)
        session.flush()
    else:
        segment.dify_conversation_id = dify_conversation_id
        segment.last_message_at = datetime.now(timezone.utc)
    user_message.segment_id = segment.id
    assistant_message.segment_id = segment.id
    return segment


def _persist_business_result(
    *,
    prepared: dict[str, Any],
    decision: RecommendationDecision | None,
    final_status: str,
    user_hash: str,
    query: str,
    answer_parts: list[str],
    dify_task_id: str,
    dify_message_id: str,
    first_token_ms: int | None,
    total_ms: int,
    usage_metadata: dict[str, Any],
    resources: list[dict[str, Any]],
    actual_dify_conversation_id: str,
    anonymous_user: str,
) -> tuple[dict[str, Any], bool]:
    recommendation: dict[str, Any] = {}
    enqueue_summary = False
    with SessionLocal() as work_session:
        conversation_db = work_session.get(
            BusinessConversation,
            prepared["conversation_id"],
        )
        user_db = work_session.get(BusinessMessage, prepared["user_message_id"])
        assistant_db = work_session.get(
            BusinessMessage,
            prepared["assistant_message_id"],
        )
        if not conversation_db or not user_db or not assistant_db:
            raise RuntimeError("本地会话或消息在生成期间已不存在")
        if decision and final_status != "failed":
            recommendation = validate_recommendation(
                work_session,
                decision,
                user_hash=user_hash,
                query=query,
            )
        assistant_db.content = "".join(answer_parts)
        assistant_db.status = final_status
        assistant_db.dify_task_id = dify_task_id or None
        assistant_db.dify_message_id = dify_message_id or None
        assistant_db.first_token_ms = first_token_ms
        assistant_db.total_ms = total_ms
        assistant_db.token_usage = int(usage_metadata.get("total_tokens") or 0)
        assistant_db.retriever_resources = resources
        assistant_db.recommendation = recommendation
        conversation_db.last_active_at = datetime.now(timezone.utc)
        if actual_dify_conversation_id:
            _store_segment(
                work_session,
                conversation=conversation_db,
                dify_conversation_id=actual_dify_conversation_id,
                anonymous_user=anonymous_user,
                user_message=user_db,
                assistant_message=assistant_db,
            )
        if final_status == "completed":
            enqueue_summary = should_summarize(
                build_context(
                    work_session,
                    conversation_db,
                    user_hash,
                )
            )
        work_session.commit()
        if recommendation.get("progress_applied"):
            cache.delete(f"ai:route-progress:{user_hash}")
    return recommendation, enqueue_summary


def prepare_business_chat(
    session: Session,
    *,
    user_id: str,
    user_hash: str,
    query: str,
    conversation_id: str | None,
    request_id: str,
) -> Iterator[bytes]:
    settings = get_settings()
    query = query.strip()
    if not query:
        raise ServiceError("VALIDATION_ERROR", "问题不能为空", 422, False)
    if len(query) > settings.max_query_chars:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"单次问题不能超过 {settings.max_query_chars} 个字符",
            422,
            False,
        )

    if conversation_id:
        conversation = session.scalar(
            select(BusinessConversation).where(
                BusinessConversation.id == conversation_id,
                BusinessConversation.user_hash == user_hash,
            )
        )
        if conversation is None:
            raise ServiceError("CONVERSATION_NOT_FOUND", "历史对话不存在", 404, False)
    else:
        conversation = BusinessConversation(
            user_hash=user_hash,
            title=query[:40] or "新对话",
            last_active_at=datetime.now(timezone.utc),
        )
        session.add(conversation)
        session.flush()

    lock_context = cache.lock(f"ai:conversation-lock:{conversation.id}", ttl=600)
    acquired = lock_context.__enter__()
    if not acquired:
        lock_context.__exit__(None, None, None)
        raise ServiceError("CONVERSATION_BUSY", "当前对话正在生成回答，请稍后再试", 409, True)

    user_message = BusinessMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
        status="completed",
    )
    assistant_message = BusinessMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        status="pending",
    )
    session.add_all([user_message, assistant_message])
    session.flush()
    context = build_context(session, conversation, user_hash, exclude_message_id=user_message.id)
    conversation.last_active_at = datetime.now(timezone.utc)

    segment = session.scalar(
        select(BusinessConversationSegment).where(
            BusinessConversationSegment.conversation_id == conversation.id,
            BusinessConversationSegment.segment_date == business_today(),
        )
    )
    dify_conversation_id = segment.dify_conversation_id if segment else ""
    anonymous_user = dify_user(user_id)
    prepared = {
        "conversation_id": conversation.id,
        "user_message_id": user_message.id,
        "assistant_message_id": assistant_message.id,
        "dify_conversation_id": dify_conversation_id,
        "anonymous_user": anonymous_user,
        "inputs": context.inputs,
    }
    session.commit()

    def stream() -> Iterator[bytes]:
        started = time.monotonic()
        first_token_ms: int | None = None
        answer_parts: list[str] = []
        dify_task_id = ""
        dify_message_id = ""
        actual_dify_conversation_id = prepared["dify_conversation_id"]
        resources: list[dict[str, Any]] = []
        usage_metadata: dict[str, Any] = {}
        decision: RecommendationDecision | None = None
        final_status = "completed"
        error_to_emit: ServiceError | None = None
        enqueue_summary = False
        task_announced = False
        url_redactor = StreamingUrlRedactor()

        if enqueue_summary and final_status == "completed":
            try:
                from .tasks import summarize_conversation

                summarize_conversation.delay(prepared["conversation_id"])
            except Exception:
                logger.exception("failed to enqueue summary task")
        yield event(
            "request_started",
            {
                "request_id": request_id,
                "conversation_id": prepared["conversation_id"],
                "message_id": prepared["assistant_message_id"],
            },
        )
        yield event("status", {"code": "retrieving", "message": "正在检索相关资料…"})

        try:
            with SessionLocal() as work_session:
                assistant = work_session.get(BusinessMessage, prepared["assistant_message_id"])
                if assistant:
                    assistant.status = "streaming"
                    work_session.commit()

            for item in _business_client().stream_chat(
                query=query,
                user=anonymous_user,
                conversation_id=prepared["dify_conversation_id"],
                inputs=prepared["inputs"],
            ):
                event_name = str(item.get("event") or "")
                dify_task_id = str(item.get("task_id") or dify_task_id)
                dify_message_id = str(item.get("message_id") or dify_message_id)
                actual_dify_conversation_id = str(
                    item.get("conversation_id") or actual_dify_conversation_id
                )
                if dify_task_id and not task_announced:
                    task_announced = True
                    yield event(
                        "status",
                        {
                            "code": "task_started",
                            "message": "已开始生成",
                            "task_id": dify_task_id,
                        },
                    )

                if event_name in {"message", "agent_message"}:
                    raw_delta = str(item.get("answer") or "")
                    if raw_delta:
                        if first_token_ms is None:
                            first_token_ms = int((time.monotonic() - started) * 1000)
                        delta = url_redactor.feed(raw_delta)
                        if delta:
                            answer_parts.append(delta)
                            yield event("answer_delta", {"delta": delta})
                # Dify 1.13.x emits ``node_finished`` for Chatflow node
                # completion (older clients used ``workflow_node_finished``).
                # Both forms carry the node outputs used for recommendation
                # validation; accept either so course cards/routes are not
                # silently lost when the deployed Dify version uses the
                # canonical event name.
                elif event_name in {"workflow_node_finished", "node_finished"}:
                    data = item.get("data") or {}
                    parsed = _decision_from_outputs(data.get("outputs"))
                    if parsed:
                        decision = parsed
                elif event_name == "message_end":
                    metadata = item.get("metadata") or {}
                    raw_resources = metadata.get("retriever_resources") or []
                    if isinstance(raw_resources, list):
                        resources = [value for value in raw_resources if isinstance(value, dict)]
                    usage = metadata.get("usage") or {}
                    if isinstance(usage, dict):
                        usage_metadata = usage
                elif event_name in {"error", "workflow_error"}:
                    raise ServiceError(
                        "DIFY_UNAVAILABLE",
                        "Dify 生成过程中发生错误",
                        502,
                        True,
                    )
            final_delta = url_redactor.finish()
            if final_delta:
                answer_parts.append(final_delta)
                yield event("answer_delta", {"delta": final_delta})
        except GeneratorExit:
            final_status = "stopped"
            if dify_task_id:
                try:
                    _business_client().stop_chat(dify_task_id, anonymous_user)
                except Exception:
                    logger.warning("failed to stop Dify task after client disconnect")
            raise
        except ServiceError as exc:
            final_status = "failed"
            error_to_emit = exc
        except Exception:
            logger.exception("business chat stream failed", extra={"request_id": request_id})
            final_status = "failed"
            error_to_emit = ServiceError(
                "DIFY_UNAVAILABLE",
                "AI服务暂时不可用，请稍后重试",
                502,
                True,
            )
        finally:
            if dify_task_id and cache.get_json(
                f"ai:business-task-stopped:{dify_task_id}"
            ):
                final_status = "stopped"
                cache.delete(f"ai:business-task-stopped:{dify_task_id}")
            total_ms = int((time.monotonic() - started) * 1000)
            recommendation: dict[str, Any] = {}
            try:
                recommendation, enqueue_summary = _persist_business_result(
                    prepared=prepared,
                    decision=decision,
                    final_status=final_status,
                    user_hash=user_hash,
                    query=query,
                    answer_parts=answer_parts,
                    dify_task_id=dify_task_id,
                    dify_message_id=dify_message_id,
                    first_token_ms=first_token_ms,
                    total_ms=total_ms,
                    usage_metadata=usage_metadata,
                    resources=resources,
                    actual_dify_conversation_id=actual_dify_conversation_id,
                    anonymous_user=anonymous_user,
                )
            except Exception:
                logger.exception(
                    "failed to persist business chat result",
                    extra={"request_id": request_id},
                )
                final_status = "failed"
                error_to_emit = ServiceError(
                    "DIFY_UNAVAILABLE",
                    "回答已生成但保存失败，请稍后重试",
                    503,
                    True,
                )
            finally:
                logger.info(
                    "business_chat_completed request_id=%s user_hash=%s "
                    "local_conversation_id=%s dify_conversation_id=%s "
                    "dify_message_id=%s task_id=%s first_token_ms=%s total_ms=%s "
                    "token_usage=%s retrieval_count=%s recommended_course_ids=%s "
                    "recommended_route_id=%s status=%s",
                    request_id,
                    user_hash,
                    prepared["conversation_id"],
                    actual_dify_conversation_id,
                    dify_message_id,
                    dify_task_id,
                    first_token_ms,
                    total_ms,
                    usage_metadata.get("total_tokens"),
                    len(resources),
                    [
                        item.get("course_id")
                        for item in recommendation.get("course_cards", [])
                    ],
                    (recommendation.get("learning_route") or {}).get("route_id"),
                    final_status,
                )
                lock_context.__exit__(None, None, None)

        if resources:
            yield event("retriever_resources", resources)
        if recommendation.get("course_cards"):
            yield event("course_cards", recommendation["course_cards"])
        if recommendation.get("learning_route"):
            yield event("learning_route", recommendation["learning_route"])
        if recommendation.get("expert_insight"):
            yield event("expert_insight", {"content": recommendation["expert_insight"]})
        if error_to_emit:
            yield event(
                "error",
                {
                    "code": error_to_emit.code,
                    "message": error_to_emit.message,
                    "request_id": request_id,
                    "retryable": error_to_emit.retryable,
                },
            )
            return
        yield event(
            "message_end",
            {
                "request_id": request_id,
                "conversation_id": prepared["conversation_id"],
                "message_id": prepared["assistant_message_id"],
                "dify_message_id": dify_message_id or None,
                "task_id": dify_task_id or None,
                "status": final_status,
                "first_token_ms": first_token_ms,
                "total_ms": int((time.monotonic() - started) * 1000),
                "token_usage": usage_metadata,
            },
        )

    return stream()


def list_user_conversations(session: Session, user_hash: str) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(BusinessConversation)
            .where(BusinessConversation.user_hash == user_hash)
            .order_by(BusinessConversation.last_active_at.desc())
        )
    )
    return [
        {
            "conversation_id": row.id,
            "title": row.title,
            "last_active_at": row.last_active_at,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def list_conversation_messages(
    session: Session, user_hash: str, conversation_id: str
) -> list[dict[str, Any]]:
    conversation = session.scalar(
        select(BusinessConversation).where(
            BusinessConversation.id == conversation_id,
            BusinessConversation.user_hash == user_hash,
        )
    )
    if conversation is None:
        raise ServiceError("CONVERSATION_NOT_FOUND", "历史对话不存在", 404, False)
    messages = list(
        session.scalars(
            select(BusinessMessage)
            .where(BusinessMessage.conversation_id == conversation_id)
            .order_by(BusinessMessage.created_at.asc())
        )
    )
    return [
        {
            "message_id": row.id,
            "role": row.role,
            "content": row.content,
            "status": row.status,
            "recommendation": row.recommendation,
            "retriever_resources": row.retriever_resources,
            "created_at": row.created_at,
        }
        for row in messages
    ]


def delete_user_conversation(
    session: Session, *, user_hash: str, conversation_id: str
) -> list[tuple[str, str]]:
    conversation = session.scalar(
        select(BusinessConversation)
        .where(
            BusinessConversation.id == conversation_id,
            BusinessConversation.user_hash == user_hash,
        )
        .options(selectinload(BusinessConversation.segments))
    )
    if conversation is None:
        raise ServiceError("CONVERSATION_NOT_FOUND", "历史对话不存在", 404, False)
    targets = [(segment.dify_conversation_id, segment.dify_user) for segment in conversation.segments]
    session.delete(conversation)
    session.commit()
    client = _business_client()
    for dify_conversation_id, anonymous_user in targets:
        try:
            client.delete_conversation(dify_conversation_id, anonymous_user)
        except Exception as exc:
            session.add(
                ExternalDeletionTask(
                    kind="dify_conversation",
                    external_id=dify_conversation_id,
                    payload={"user": anonymous_user},
                    attempts=1,
                    last_error=str(exc)[:1000],
                )
            )
    session.commit()
    return targets
