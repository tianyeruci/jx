from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ...cache import cache
from ...db import get_session
from ...errors import ServiceError
from ...models import (
    BusinessConversation,
    BusinessMessage,
    BusinessProfile,
    Course,
    CourseClickDaily,
    ExternalDeletionTask,
    LearningRoute,
    LearningRouteProgress,
)
from ...security import require_admin_token, require_internal_token, user_hash
from ...services.observability import record_event
from ...timeutil import business_today

router = APIRouter(
    prefix="/internal/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_internal_token), Depends(require_admin_token)],
)


@router.get("/conversations")
def admin_conversations(
    user_id: str | None = Query(default=None, max_length=255),
    user_name: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    statement = select(BusinessConversation).order_by(BusinessConversation.last_active_at.desc()).limit(limit)
    if user_id:
        statement = statement.where(BusinessConversation.user_hash == user_hash(user_id.strip()))
    elif user_name and user_name.strip():
        statement = statement.join(
            BusinessProfile,
            BusinessProfile.user_hash == BusinessConversation.user_hash,
        ).where(BusinessProfile.display_name.ilike(f"%{user_name.strip()}%"))
    rows = list(session.scalars(statement))
    profile_names = {
        item.user_hash: item.display_name
        for item in session.scalars(
            select(BusinessProfile).where(BusinessProfile.user_hash.in_([row.user_hash for row in rows]))
        )
    } if rows else {}
    return {
        "items": [
            {
                "conversation_id": row.id,
                "user_hash": row.user_hash,
                "user_name": profile_names.get(row.user_hash, ""),
                "title": row.title,
                "summary": row.summary,
                "summary_gap": row.summary_gap,
                "last_active_at": row.last_active_at,
            }
            for row in rows
        ],
        "next_cursor": None,
    }


@router.get("/conversations/{conversation_id}")
def admin_conversation_detail(
    conversation_id: str,
    session: Session = Depends(get_session),
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
    profile = session.get(BusinessProfile, conversation.user_hash)
    return {
        "conversation_id": conversation.id,
        "user_hash": conversation.user_hash,
        "user_name": profile.display_name if profile else "",
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
                "retriever_resources": item.retriever_resources,
                "feedback": item.feedback,
                "dify_message_id": item.dify_message_id,
                "dify_task_id": item.dify_task_id,
                "created_at": item.created_at,
            }
            for item in messages
        ],
    }


@router.get("/course-clicks")
def admin_course_clicks(
    course_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(CourseClickDaily)
    if course_id:
        query = query.where(CourseClickDaily.course_id == course_id)
    if from_date:
        query = query.where(CourseClickDaily.click_date >= from_date)
    if to_date:
        query = query.where(CourseClickDaily.click_date <= to_date)
    rows = list(session.scalars(query))
    course_names = {row.course_id: row.name for row in session.scalars(select(Course))}
    merged: dict[tuple[str, date], dict[str, Any]] = {
        (row.course_id, row.click_date): {
            "course_id": row.course_id,
            "course_name": course_names.get(row.course_id, ""),
            "date": row.click_date,
            "clicks": row.clicks,
            "unique_users": row.unique_users,
        }
        for row in rows
    }
    today = business_today()
    if (from_date is None or from_date <= today) and (
        to_date is None or to_date >= today
    ):
        course_query = select(Course.course_id)
        if course_id:
            course_query = course_query.where(Course.course_id == course_id)
        for live_course_id in session.scalars(course_query):
            clicks, unique_users = cache.click_counts(
                live_course_id,
                today.isoformat(),
            )
            if clicks or unique_users:
                key = (live_course_id, today)
                current = merged.get(key)
                merged[key] = {
                    "course_id": live_course_id,
                    "course_name": course_names.get(live_course_id, ""),
                    "date": today,
                    # Redis counters contain the whole day's count. Use the larger
                    # value when a periodic flush has already created today's row.
                    "clicks": max(clicks, int((current or {}).get("clicks") or 0)),
                    "unique_users": max(
                        unique_users,
                        int((current or {}).get("unique_users") or 0),
                    ),
                    "live": True,
                }
    all_items = sorted(
        merged.values(),
        key=lambda item: (item["date"], item["course_id"]),
        reverse=True,
    )
    total = len(all_items)
    start = (page - 1) * limit
    course_totals: dict[str, dict[str, Any]] = {}
    for item in all_items:
        aggregate = course_totals.setdefault(
            item["course_id"],
            {"course_id": item["course_id"], "course_name": item.get("course_name", ""), "clicks": 0, "unique_users": 0},
        )
        aggregate["clicks"] += int(item["clicks"])
        aggregate["unique_users"] += int(item["unique_users"])
    return {
        "items": all_items[start : start + limit],
        "page": page,
        "limit": limit,
        "total": total,
        "total_clicks": sum(int(item["clicks"]) for item in all_items),
        "total_unique_users": sum(
            int(item["unique_users"]) for item in all_items
        ),
        "course_totals": list(course_totals.values()),
    }


@router.delete("/users/{raw_user_id}/ai-data")
def delete_user_ai_data(
    raw_user_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Delete user AI data while preserving aggregate course metrics and logs."""
    subject_hash = user_hash(raw_user_id.strip())
    conversations = list(
        session.scalars(
            select(BusinessConversation)
            .where(BusinessConversation.user_hash == subject_hash)
            .options(selectinload(BusinessConversation.segments))
        )
    )
    external_tasks = 0
    for conversation in conversations:
        for segment in conversation.segments:
            session.add(
                ExternalDeletionTask(
                    kind="dify_conversation",
                    external_id=segment.dify_conversation_id,
                    payload={"user": segment.dify_user},
                    attempts=0,
                )
            )
            external_tasks += 1
        session.delete(conversation)
    session.execute(delete(LearningRouteProgress).where(LearningRouteProgress.user_hash == subject_hash))
    session.execute(delete(BusinessProfile).where(BusinessProfile.user_hash == subject_hash))
    session.commit()
    cache.delete(f"ai:profile:{subject_hash}")
    cache.delete(f"ai:route-progress:{subject_hash}")
    record_event(
        level="INFO", category="operation", component="admin_api",
        event_code="USER_AI_DATA_DELETED", status="succeeded", message="用户 AI 数据已清除",
        actor_hash="admin", resource_type="user", resource_id=subject_hash,
        details={"conversation_count": len(conversations), "external_deletions": external_tasks},
    )
    return {"deleted": True, "conversation_count": len(conversations), "external_deletions_queued": external_tasks}


@router.get("/catalog")
def admin_catalog(session: Session = Depends(get_session)) -> dict[str, Any]:
    courses = list(session.scalars(select(Course).order_by(Course.course_id)))
    routes = list(
        session.scalars(select(LearningRoute).order_by(LearningRoute.route_id))
    )
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
