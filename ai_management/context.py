from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .cache import cache
from .config import get_settings
from .courses import get_snapshot
from .models import (
    BusinessConversation,
    BusinessMessage,
    BusinessProfile,
    LearningRouteProgress,
)
from .timeutil import BUSINESS_TIMEZONE, business_today


@dataclass(slots=True)
class BuiltContext:
    inputs: dict[str, str]
    unsummarized_message_ids: list[str]
    user_turns: int
    body_chars: int


def _route_authority_inputs(
    session: Session, progress_json: list[dict[str, object]]
) -> tuple[str, str]:
    """Compact server authority for Dify; IDs/order are never model-owned."""

    snapshot = get_snapshot(session)
    routes = snapshot.get("routes") or {}
    courses = snapshot.get("courses") or {}
    states: list[dict[str, object]] = []
    focused: list[dict[str, object]] = []
    for item in progress_json:
        route_id = str(item.get("route_id") or "")
        route = routes.get(route_id)
        if not isinstance(route, dict):
            continue
        stages = sorted(route.get("stages") or [], key=lambda value: value.get("order", 0))
        current_stage_id = str(item.get("current_stage_id") or "")
        states.append(
            {
                "route_id": route_id,
                "route_name": route.get("name") or "",
                "status": item.get("status") or "",
                "current_stage_id": current_stage_id or None,
                "current_stage_name": next(
                    (stage.get("name") for stage in stages if str(stage.get("stage_id") or "") == current_stage_id),
                    None,
                ),
            }
        )
        # Full per-course authority is useful only where route state is
        # unambiguous.  Multiple active routes must be clarified first.
        focused.append(
            {
                "route_id": route_id,
                "route_name": route.get("name") or "",
                "stages": [
                    {
                        "stage_id": stage.get("stage_id"),
                        "name": stage.get("name"),
                        "order": stage.get("order"),
                        "course_ids": list(stage.get("course_ids") or []),
                        "courses": [
                            {
                                "course_id": course_id,
                                "name": (courses.get(course_id) or {}).get("name") or "",
                                "order": index,
                                "direction": (courses.get(course_id) or {}).get("direction") or "",
                                "description": str((courses.get(course_id) or {}).get("description") or "")[:180],
                            }
                            for index, course_id in enumerate(stage.get("course_ids") or [], start=1)
                            if isinstance(courses.get(course_id), dict)
                            and courses[course_id].get("enabled")
                            and courses[course_id].get("review_status") == "approved"
                        ],
                    }
                    for stage in stages
                ],
            }
        )
    authority: dict[str, object] = {"routes": focused if len(focused) == 1 else []}
    authority_json = json.dumps(authority, ensure_ascii=False)
    # Preserve membership, names and official order under the Dify input
    # budget; only optional semantic summaries are allowed to shrink.
    if len(authority_json) > 24_000:
        for route in authority["routes"]:  # type: ignore[index]
            for stage in route.get("stages", []):  # type: ignore[union-attr]
                for course in stage.get("courses", []):
                    course["description"] = ""
        authority_json = json.dumps(authority, ensure_ascii=False)
    return (
        json.dumps({"selected_routes": states, "ambiguous": len(states) > 1}, ensure_ascii=False),
        authority_json,
    )


def messages_after_summary(
    session: Session,
    conversation: BusinessConversation,
    *,
    exclude_message_id: str | None = None,
    limit: int | None = None,
    newest: bool = False,
) -> list[BusinessMessage]:
    conditions = [
        BusinessMessage.conversation_id == conversation.id,
        BusinessMessage.status.in_(["completed", "stopped"]),
    ]
    cursor = None
    if conversation.summary_through_message_id:
        cursor = session.get(BusinessMessage, conversation.summary_through_message_id)
    if cursor is not None and cursor.conversation_id == conversation.id:
        conditions.append(
            or_(
                BusinessMessage.created_at > cursor.created_at,
                and_(
                    BusinessMessage.created_at == cursor.created_at,
                    BusinessMessage.id > cursor.id,
                ),
            )
        )
    if exclude_message_id:
        conditions.append(BusinessMessage.id != exclude_message_id)
    order = (
        (BusinessMessage.created_at.desc(), BusinessMessage.id.desc())
        if newest
        else (BusinessMessage.created_at.asc(), BusinessMessage.id.asc())
    )
    statement = select(BusinessMessage).where(*conditions).order_by(*order)
    if limit is not None:
        statement = statement.limit(limit)
    messages = list(session.scalars(statement))
    if newest:
        messages.reverse()
    return messages


def build_context(
    session: Session,
    conversation: BusinessConversation,
    user_hash: str,
    *,
    exclude_message_id: str | None = None,
) -> BuiltContext:
    settings = get_settings()
    # The model only receives today's raw turns.  Older material is represented
    # by the verified rolling summary, not a seven-day transcript.
    today_start = datetime.combine(business_today(), time.min, tzinfo=BUSINESS_TIMEZONE)
    today_start_utc = today_start.astimezone(ZoneInfo("UTC"))
    conditions = [
        BusinessMessage.conversation_id == conversation.id,
        BusinessMessage.status.in_(["completed", "stopped"]),
        BusinessMessage.created_at >= today_start_utc,
    ]
    if exclude_message_id:
        conditions.append(BusinessMessage.id != exclude_message_id)
    statement = (
        select(BusinessMessage)
        .where(*conditions)
        .order_by(BusinessMessage.created_at.desc(), BusinessMessage.id.desc())
        .limit(settings.context_max_messages)
    )
    messages = list(session.scalars(statement))
    messages.reverse()
    rendered = [f"{'用户' if item.role == 'user' else '助手'}：{item.content}" for item in messages]
    total = 0
    kept: list[str] = []
    for text in reversed(rendered):
        if kept and total + len(text) > settings.context_max_chars:
            break
        kept.append(text)
        total += len(text)
    kept.reverse()

    profile_data = cache.get_json(f"ai:profile:{user_hash}")
    if not isinstance(profile_data, dict):
        profile = session.get(BusinessProfile, user_hash)
        profile_data = profile.confirmed if profile else {}
        cache.set_json(f"ai:profile:{user_hash}", profile_data, ttl=300)

    # Progress is small and drives authorization. Read it from the authority
    # database so a just-completed/restarted route is never hidden by a stale
    # cache entry from an earlier schema that excluded completed rows.
    progresses = list(
        session.scalars(
            select(LearningRouteProgress).where(
                LearningRouteProgress.user_hash == user_hash,
                LearningRouteProgress.status.in_(["pending_stage", "active", "completed"]),
            )
        )
    )
    progress_json = [
        {
            "route_id": item.route_id,
            "current_stage_id": item.current_stage_id,
            "status": item.status,
        }
        for item in progresses
    ]
    cache.set_json(f"ai:route-progress:{user_hash}", progress_json, ttl=300)
    route_state_context_json, authoritative_course_context_json = _route_authority_inputs(
        session, progress_json
    )
    return BuiltContext(
        inputs={
            "local_conversation_id": conversation.id,
            "conversation_summary": conversation.summary or "",
            "recent_history_text": "\n\n".join(kept),
            "profile_json": json.dumps(profile_data, ensure_ascii=False),
            "route_progress_json": json.dumps(progress_json, ensure_ascii=False),
            "route_state_context_json": route_state_context_json,
            "authoritative_course_context_json": authoritative_course_context_json,
        },
        unsummarized_message_ids=[message.id for message in messages],
        user_turns=sum(message.role == "user" for message in messages),
        body_chars=sum(len(message.content) for message in messages),
    )


def should_summarize(context: BuiltContext) -> bool:
    settings = get_settings()
    return (
        context.user_turns >= settings.summary_user_turns
        or context.body_chars >= settings.summary_approx_chars
    )
