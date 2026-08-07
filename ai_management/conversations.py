from __future__ import annotations

import logging
import re
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .cache import cache
from .config import get_settings
from .context import build_context, should_summarize
from .courses import (
    create_redirect_token,
    get_snapshot,
    infer_recommendation_decision,
    public_catalog_names,
    public_name_fallbacks,
    validate_recommendation,
)
from .db import SessionLocal
from .dify import DifyAppClient, extract_json_object
from .errors import ServiceError
from .models import (
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    BusinessProfile,
    ExternalDeletionTask,
)
from .privacy import load_public_redaction_rules
from .schemas import RecommendationDecision
from .security import dify_user
from .services.observability import record_event
from .services.style_settings import get_style_prompt
from .sse import SSEEmitter
from .stream_filter import (
    StreamingExactPrefixDeduper,
    StreamingPublicRedactor,
    publicize_internal_references,
    publicize_internal_references_in_value,
)
from .timeutil import business_today

logger = logging.getLogger(__name__)

PUBLIC_REFERENCE_PRIVACY_INSTRUCTION = (
    "公开引用不得展示资料中的真实姓名、昵称、笔名、个人账号称呼或原文件名；"
    "只能使用清洗后的“作者”“案例人物A”“匿名参与者”等稳定匿名称谓。"
    "课程、路线、阶段只能使用已审核目录中的正式名称；最终回答、推荐说明和专家建议不得输出课程ID、路线ID、阶段ID、"
    "course_、route_、stage_或内部标识占位词，也不得臆造目录中不存在的课程名称。"
)

PROGRESS_ANSWER_NODE_ID = "progress_answer"
QUICK_ANSWER_NODE_ID = "quick_answer"
ANSWER_NODE_PREFIX = "answer_"


def _default_next_step_advice(course_cards: list[dict[str, Any]]) -> str:
    """Return a short, deterministic composite note for validated cards.

    The note is deliberately independent of model output.  This keeps the
    user-facing expert card available when the model omitted the internal
    expert fields or failed their privacy/ID consistency checks.
    """

    if len(course_cards) > 1:
        return "建议按课程卡顺序完成当前阶段内容，并做一次对应练习；完成后告诉我进度，我再安排下一步。"
    return "建议先完成这门课程的当前内容，并做一次对应练习；完成后告诉我进度，我再安排下一步。"


def _validated_next_step_advice(
    recommendation: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    catalog_names: dict[str, str],
    fallbacks: dict[str, str] | None = None,
) -> str:
    """Normalize one combined expert note, falling back safely when needed."""

    cards = recommendation.get("course_cards")
    if not isinstance(cards, list) or not cards:
        return ""
    card_list = [card for card in cards if isinstance(card, dict)]
    if not card_list:
        return ""
    raw = str(recommendation.get("expert_insight") or "").strip()
    note = publicize_internal_references(
        raw,
        catalog_names,
        fallbacks or {},
    ).strip()
    note = re.sub(r"^(?:专家建议|下一步建议|匹配理由)\s*[:：]?\s*", "", note).strip()
    forbidden = {
        str(card.get("course_id") or "").strip()
        for card in card_list
        if str(card.get("course_id") or "").strip()
    }
    forbidden.update(str(value).strip() for value in (snapshot.get("courses") or {}))
    forbidden.update(str(value).strip() for value in (snapshot.get("routes") or {}))
    forbidden.update(str(value).strip() for value in (snapshot.get("stages") or {}))
    if (
        not note
        or len(note) > 80
        or re.search(r"https?://|www\.", note, flags=re.IGNORECASE)
        or any(value and value in note for value in forbidden)
    ):
        note = _default_next_step_advice(card_list)
    return note[:80].rstrip()


def _route_followup_guidance(route: dict[str, Any] | None) -> str:
    """Build the next user action from the server-authoritative route state."""

    if not isinstance(route, dict) or not route:
        return ""
    route_name = str(route.get("name") or "这条学习路线").strip()
    state = str(route.get("state") or route.get("progress_status") or "").strip()
    stages = [
        stage
        for stage in route.get("stages") or []
        if isinstance(stage, dict)
    ]
    stages.sort(key=lambda item: (item.get("order", 0), str(item.get("stage_id") or "")))
    current_stage_id = str(route.get("current_stage_id") or "")
    current_stage = next(
        (stage for stage in stages if str(stage.get("stage_id") or "") == current_stage_id),
        None,
    )
    if state == "awaiting_route_confirmation":
        return f"如果这条学习路线符合你的目标，请回复：我确认学习路线：{route_name}"
    if state == "awaiting_stage_confirmation":
        first_stage = stages[0] if stages else {}
        stage_name = str(first_stage.get("name") or "当前阶段").strip()
        return f"请告诉我你目前所在阶段，例如：我目前在：{stage_name}"
    if state == "active":
        stage_name = str((current_stage or {}).get("name") or "当前阶段").strip()
        return f"请先完成{stage_name}的课程；完成后回复：我已完成：{stage_name}"
    if state == "completed":
        return f"你已完成{route_name}；告诉我新的学习目标，我会继续规划下一步。"
    return ""


def _dify_node_id(item: dict[str, Any]) -> str:
    data = item.get("data")
    if isinstance(data, dict) and data.get("node_id"):
        return str(data["node_id"])
    return str(item.get("node_id") or "")


def _answer_phase_for_node(node_id: str) -> str | None:
    if node_id == PROGRESS_ANSWER_NODE_ID:
        return "progress"
    if node_id == QUICK_ANSWER_NODE_ID or node_id.startswith(ANSWER_NODE_PREFIX):
        return "final"
    return None


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
    if any(
        key in outputs
        for key in (
            "intent",
            "candidate_course_ids",
            "candidate_route_id",
            "expert_insight",
            "expert_course_ids",
            "final_answer",
        )
    ):
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
) -> tuple[dict[str, Any], bool, str]:
    recommendation: dict[str, Any] = {}
    enqueue_summary = False
    route_guidance = ""
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
        if (
            final_status != "failed"
            and not recommendation.get("course_cards")
            and not recommendation.get("learning_route")
        ):
            fallback_decision = infer_recommendation_decision(
                work_session,
                query=query,
                decision=decision,
            )
            if fallback_decision is not None:
                fallback_recommendation = validate_recommendation(
                    work_session,
                    fallback_decision,
                    user_hash=user_hash,
                    query=query,
                )
                if fallback_recommendation.get("course_cards") or fallback_recommendation.get("learning_route"):
                    recommendation = fallback_recommendation
        snapshot = get_snapshot(work_session)
        names = public_catalog_names(snapshot)
        fallbacks = public_name_fallbacks(
            snapshot,
            decision=decision,
            recommendation=recommendation,
        )
        # The note is a single, server-gated composite card.  A missing or
        # invalid model note must not make a valid course recommendation look
        # incomplete to the user.
        if final_status != "failed" and recommendation.get("course_cards"):
            recommendation["expert_insight"] = _validated_next_step_advice(
                recommendation,
                snapshot=snapshot,
                catalog_names=names,
                fallbacks=fallbacks,
            )
        route_guidance = _route_followup_guidance(
            recommendation.get("learning_route")
            if final_status != "failed"
            else None
        )
        assistant_content = publicize_internal_references(
            "".join(answer_parts),
            names,
            fallbacks,
        )
        if route_guidance and route_guidance not in assistant_content:
            assistant_content = (
                assistant_content.rstrip() + "\n\n" + route_guidance
                if assistant_content.strip()
                else route_guidance
            )
        assistant_db.content = assistant_content
        assistant_db.status = final_status
        assistant_db.dify_task_id = dify_task_id or None
        assistant_db.dify_message_id = dify_message_id or None
        assistant_db.first_token_ms = first_token_ms
        assistant_db.total_ms = total_ms
        assistant_db.token_usage = int(usage_metadata.get("total_tokens") or 0)
        assistant_db.retriever_resources = resources
        for field in ("recommendation_note", "expert_insight"):
            if field in recommendation:
                recommendation[field] = publicize_internal_references(
                    str(recommendation[field] or ""),
                    names,
                    fallbacks,
                )
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
    return recommendation, enqueue_summary, route_guidance


def prepare_business_chat(
    session: Session,
    *,
    user_id: str,
    user_hash: str,
    query: str,
    conversation_id: str | None,
    request_id: str,
    user_display_name: str = "",
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
    redaction_rules = load_public_redaction_rules(session)
    query = redaction_rules.sanitize_text(query)
    snapshot = get_snapshot(session)
    catalog_names = public_catalog_names(snapshot)

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

    profile = session.get(BusinessProfile, user_hash)
    if profile is None:
        profile = BusinessProfile(
            user_hash=user_hash,
            user_id=user_id,
            display_name=user_display_name,
        )
        session.add(profile)
    else:
        if profile.user_id != user_id:
            profile.user_id = user_id
        if user_display_name and profile.display_name != user_display_name:
            profile.display_name = user_display_name

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
    configured_style = get_style_prompt(session, "business_answer").strip()
    context.inputs.update(
        {
            "business_output_style": "\n\n".join(
                value
                for value in (
                    configured_style,
                    PUBLIC_REFERENCE_PRIVACY_INSTRUCTION,
                )
                if value
            ),
        }
    )
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
        emit = SSEEmitter(request_id)
        started = time.monotonic()
        first_token_ms: int | None = None
        answer_parts: list[str] = []
        dify_task_id = ""
        dify_message_id = ""
        actual_dify_conversation_id = prepared["dify_conversation_id"]
        resources: list[dict[str, Any]] = []
        usage_metadata: dict[str, Any] = {}
        decision: RecommendationDecision | None = None
        query_split_fallback = False
        final_status = "completed"
        error_to_emit: ServiceError | None = None
        enqueue_summary = False
        route_guidance = ""
        task_announced = False
        final_redactor = StreamingPublicRedactor(
            redaction_rules.replacements,
            catalog_names=catalog_names,
        )
        final_prefix_deduper: StreamingExactPrefixDeduper | None = None
        answer_phase = "unknown"
        progress_finished = False
        progress_answer_emitted = False
        progress_answer_parts: list[str] = []

        def final_deduper() -> StreamingExactPrefixDeduper:
            nonlocal final_prefix_deduper
            if final_prefix_deduper is None:
                final_prefix_deduper = StreamingExactPrefixDeduper(
                    "".join(progress_answer_parts)
                )
            return final_prefix_deduper

        def switch_answer_phase(phase: str) -> list[bytes]:
            nonlocal answer_phase
            if phase == answer_phase:
                return []
            events: list[bytes] = []
            if phase == "progress":
                answer_phase = phase
                events.append(
                    emit(
                        "status",
                        {
                            "code": "progress_answer_started",
                            "message": "已开始返回即时提示",
                        },
                    )
                )
            elif phase == "final":
                was_progress = answer_phase == "progress"
                answer_phase = phase
                if was_progress:
                    events.append(
                        emit(
                            "status",
                            {
                                "code": "final_answer_started",
                                "message": "已开始返回最终答案",
                            },
                        )
                    )
            return events

        yield emit(
            "request_started",
            {
                "request_id": request_id,
                "conversation_id": prepared["conversation_id"],
                "message_id": prepared["assistant_message_id"],
            },
        )
        yield emit("status", {"code": "retrieving", "message": "正在检索相关资料…"})

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
                node_id = _dify_node_id(item)
                node_phase = _answer_phase_for_node(node_id)
                dify_task_id = str(item.get("task_id") or dify_task_id)
                dify_message_id = str(item.get("message_id") or dify_message_id)
                actual_dify_conversation_id = str(
                    item.get("conversation_id") or actual_dify_conversation_id
                )
                if dify_task_id and not task_announced:
                    task_announced = True
                    yield emit(
                        "status",
                        {
                            "code": "task_started",
                            "message": "已开始生成",
                            "task_id": dify_task_id,
                        },
                    )

                if event_name in {"node_started", "workflow_node_started"} and node_phase:
                    for event in switch_answer_phase(node_phase):
                        yield event

                if event_name in {"message", "agent_message"}:
                    if node_phase:
                        for event in switch_answer_phase(node_phase):
                            yield event
                    elif answer_phase == "progress" and progress_finished:
                        for event in switch_answer_phase("final"):
                            yield event
                    elif answer_phase == "unknown":
                        # Older Dify versions may omit node start events for a
                        # simple one-answer path. Keep that path compatible.
                        answer_phase = "final"
                    raw_delta = str(item.get("answer") or "")
                    if raw_delta:
                        if first_token_ms is None:
                            first_token_ms = int((time.monotonic() - started) * 1000)
                        if answer_phase == "progress":
                            # The progress node is a fixed, service-controlled
                            # Answer node. Sanitize each chunk directly so its
                            # short text is not held by the streaming redactor
                            # tail and can reach the client immediately.
                            delta = publicize_internal_references(
                                redaction_rules.sanitize_text(
                                    raw_delta,
                                    remove_urls=True,
                                ),
                                catalog_names,
                            )
                        else:
                            delta = final_redactor.feed(raw_delta)
                        if delta:
                            if answer_phase == "final":
                                delta = final_deduper().feed(delta)
                                if delta:
                                    answer_parts.append(delta)
                            elif answer_phase == "progress":
                                progress_answer_parts.append(delta)
                            if delta:
                                yield emit("answer_delta", {"delta": delta})
                # Dify 1.13.x emits ``node_finished`` for Chatflow node
                # completion (older clients used ``workflow_node_finished``).
                # Both forms carry the node outputs used for recommendation
                # validation; accept either so course cards/routes are not
                # silently lost when the deployed Dify version uses the
                # canonical event name.
                elif event_name in {"workflow_node_finished", "node_finished"}:
                    if node_id == PROGRESS_ANSWER_NODE_ID:
                        progress_finished = True
                        # A few older Dify builds expose the node completion
                        # event but omit node_started. This still lets the
                        # following answer message be classified correctly.
                        for event in switch_answer_phase("progress"):
                            yield event
                        progress_output = (item.get("data") or {}).get("outputs") or {}
                        raw_progress = progress_output.get("answer")
                        if (
                            not progress_answer_emitted
                            and isinstance(raw_progress, str)
                            and raw_progress
                        ):
                            progress_answer_emitted = True
                            if first_token_ms is None:
                                first_token_ms = int((time.monotonic() - started) * 1000)
                            delta = publicize_internal_references(
                                redaction_rules.sanitize_text(raw_progress, remove_urls=True),
                                catalog_names,
                            )
                            if delta:
                                progress_answer_parts.append(delta)
                                yield emit("answer_delta", {"delta": delta})
                    elif node_phase == "final":
                        for event in switch_answer_phase("final"):
                            yield event
                    data = item.get("data") or {}
                    if str(data.get("node_id") or "") == "query_split_normalize":
                        outputs = data.get("outputs") or {}
                        query_split_fallback = str(outputs.get("fallback_used") or "").lower() == "true"
                    parsed = _decision_from_outputs(data.get("outputs"))
                    if parsed:
                        decision = parsed
                elif event_name == "message_end":
                    metadata = item.get("metadata") or {}
                    raw_resources = metadata.get("retriever_resources") or []
                    if isinstance(raw_resources, list):
                        resources = [
                            publicize_internal_references_in_value(
                                redaction_rules.sanitize_value(
                                    value,
                                    remove_urls=True,
                                ),
                                catalog_names,
                            )
                            for value in raw_resources
                            if isinstance(value, dict)
                        ]
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
            final_redactor.set_fallback_names(
                public_name_fallbacks(snapshot, decision=decision)
            )
            final_delta = final_redactor.finish()
            final_delta = publicize_internal_references(
                final_delta,
                catalog_names,
                public_name_fallbacks(snapshot, decision=decision),
            )
            final_delta = final_deduper().feed(final_delta)
            final_delta += final_deduper().finish()
            if final_delta:
                answer_parts.append(final_delta)
                yield emit("answer_delta", {"delta": final_delta})
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
                recommendation, enqueue_summary, route_guidance = _persist_business_result(
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
                # The threshold is only known after the final assistant result
                # has been persisted.  Queue after that transaction commits so
                # the worker never observes an incomplete conversation.
                if enqueue_summary and final_status == "completed":
                    try:
                        from .tasks import summarize_conversation

                        summarize_conversation.delay(prepared["conversation_id"])
                    except Exception:
                        logger.exception("failed to enqueue summary task")
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
                if query_split_fallback:
                    record_event(
                        level="WARNING",
                        category="workflow",
                        component="business_chat",
                        event_code="MIXED_QUERY_SPLIT_FALLBACK",
                        status="fallback",
                        message="问题结构化拆分失败，已使用双知识库回退检索",
                        request_id=request_id,
                        actor_hash=user_hash,
                        resource_type="conversation",
                        resource_id=prepared["conversation_id"],
                    )
                lock_context.__exit__(None, None, None)

        if resources:
            yield emit("retriever_resources", {"resources": resources})
        if recommendation.get("learning_route"):
            yield emit("learning_route", recommendation["learning_route"])
        if recommendation.get("course_cards"):
            yield emit("course_cards", {"items": recommendation["course_cards"]})
        course_cards = recommendation.get("course_cards")
        expert_insight = str(recommendation.get("expert_insight") or "").strip()
        if isinstance(course_cards, list) and course_cards and expert_insight:
            # Exactly one combined note follows the complete card batch.
            yield emit("course_recommendation_note", {"content": expert_insight})
        if route_guidance:
            # Route guidance stays on the existing answer channel; no new SSE
            # event or payload is introduced.
            yield emit("answer_delta", {"delta": route_guidance})
        if error_to_emit:
            yield emit(
                "error",
                {
                    "code": error_to_emit.code,
                    "message": error_to_emit.message,
                    "request_id": request_id,
                    "retryable": error_to_emit.retryable,
                },
            )
            return
        yield emit(
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


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        raw = urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4))
        payload = __import__("json").loads(raw)
        return datetime.fromisoformat(payload["created_at"]), str(payload["id"])
    except Exception as exc:
        raise ServiceError("VALIDATION_ERROR", "分页游标无效", 422, False) from exc


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = __import__("json").dumps(
        {"created_at": created_at.isoformat(), "id": row_id}, separators=(",", ":")
    ).encode("utf-8")
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def list_user_conversations(
    session: Session, user_hash: str, *, cursor: str | None = None, limit: int = 20
) -> dict[str, Any]:
    statement = select(BusinessConversation).where(BusinessConversation.user_hash == user_hash)
    boundary = _decode_cursor(cursor)
    if boundary:
        created_at, row_id = boundary
        statement = statement.where(
            (BusinessConversation.created_at < created_at)
            | ((BusinessConversation.created_at == created_at) & (BusinessConversation.id < row_id))
        )
    rows = list(session.scalars(statement.order_by(BusinessConversation.created_at.desc(), BusinessConversation.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    catalog_names = public_catalog_names(get_snapshot(session))
    redaction_rules = load_public_redaction_rules(session)
    items = [
        {
            "conversation_id": row.id,
            "title": publicize_internal_references(
                redaction_rules.sanitize_text(row.title),
                catalog_names,
            ),
            "last_active_at": row.last_active_at,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return {
        "items": items,
        "next_cursor": _encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None,
    }


def list_conversation_messages(
    session: Session, user_hash: str, conversation_id: str, *, cursor: str | None = None, limit: int = 20
) -> dict[str, Any]:
    conversation = session.scalar(
        select(BusinessConversation).where(
            BusinessConversation.id == conversation_id,
            BusinessConversation.user_hash == user_hash,
        )
    )
    if conversation is None:
        raise ServiceError("CONVERSATION_NOT_FOUND", "历史对话不存在", 404, False)
    statement = select(BusinessMessage).where(BusinessMessage.conversation_id == conversation_id)
    boundary = _decode_cursor(cursor)
    if boundary:
        created_at, row_id = boundary
        statement = statement.where(
            (BusinessMessage.created_at > created_at)
            | ((BusinessMessage.created_at == created_at) & (BusinessMessage.id > row_id))
        )
    messages = list(session.scalars(statement.order_by(BusinessMessage.created_at.asc(), BusinessMessage.id.asc()).limit(limit + 1)))
    has_more = len(messages) > limit
    messages = messages[:limit]
    snapshot = get_snapshot(session)
    catalog_names = public_catalog_names(snapshot)
    redaction_rules = load_public_redaction_rules(session)

    def _current_recommendation(value: dict[str, Any] | None) -> dict[str, Any]:
        """Never return expired or now-disabled signed course links from history."""
        recommendation = dict(value or {})
        cards: list[dict[str, Any]] = []
        for stored_card in recommendation.get("course_cards") or []:
            if not isinstance(stored_card, dict):
                continue
            course_id = str(stored_card.get("course_id") or "")
            course = (snapshot.get("courses") or {}).get(course_id)
            if not isinstance(course, dict):
                continue
            card = {key: item for key, item in course.items() if key != "official_url"}
            card["redirect_url"] = (
                f"{get_settings().public_base_url.rstrip('/')}/r/"
                f"{create_redirect_token(course_id, user_hash)}"
            )
            cards.append(card)
        if "course_cards" in recommendation:
            recommendation["course_cards"] = cards
        fallbacks = public_name_fallbacks(snapshot, recommendation=recommendation)
        for field in ("recommendation_note", "expert_insight"):
            if field in recommendation:
                recommendation[field] = publicize_internal_references(
                    str(recommendation[field] or ""),
                    catalog_names,
                    fallbacks,
                )
        return recommendation

    items = [
        {
            "message_id": row.id,
            "role": row.role,
            "content": publicize_internal_references(
                redaction_rules.sanitize_text(
                    row.content,
                    remove_urls=True,
                ),
                catalog_names,
                public_name_fallbacks(snapshot, recommendation=row.recommendation),
            ),
            "status": row.status,
            "recommendation": redaction_rules.sanitize_value(
                _current_recommendation(row.recommendation)
            ),
            "retriever_resources": publicize_internal_references_in_value(
                redaction_rules.sanitize_value(
                    row.retriever_resources,
                    remove_urls=True,
                ),
                catalog_names,
                public_name_fallbacks(snapshot, recommendation=row.recommendation),
            ),
            "created_at": row.created_at,
        }
        for row in messages
    ]
    return {
        "items": items,
        "next_cursor": _encode_cursor(messages[-1].created_at, messages[-1].id) if has_more and messages else None,
    }


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
