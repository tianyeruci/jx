from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cache import cache
from .config import get_settings
from .errors import ServiceError
from .models import Course, LearningRoute, LearningRouteProgress
from .schemas import RecommendationDecision
from .timeutil import business_today

COURSE_SNAPSHOT_KEY = "ai:course:snapshot"


def _course_dict(course: Course) -> dict[str, Any]:
    return {
        "course_id": course.course_id,
        "name": course.name,
        "direction": course.direction,
        "description": course.description,
        "official_url": course.official_url,
        "enabled": course.enabled,
        "review_status": course.review_status,
        **(course.optional_data or {}),
    }


def _route_dict(route: LearningRoute) -> dict[str, Any]:
    return {
        "route_id": route.route_id,
        "name": route.name,
        "direction": route.direction,
        "target": route.target,
        "stages": route.stages or [],
        "enabled": route.enabled,
        "review_status": route.review_status,
    }


def rebuild_snapshot(session: Session) -> dict[str, Any]:
    snapshot = {
        "courses": {item.course_id: _course_dict(item) for item in session.scalars(select(Course))},
        "routes": {item.route_id: _route_dict(item) for item in session.scalars(select(LearningRoute))},
    }
    cache.atomic_json_swap(COURSE_SNAPSHOT_KEY, snapshot)
    return snapshot


def get_snapshot(session: Session) -> dict[str, Any]:
    snapshot = cache.get_json(COURSE_SNAPSHOT_KEY)
    if isinstance(snapshot, dict) and "courses" in snapshot and "routes" in snapshot:
        return snapshot
    return rebuild_snapshot(session)


def _sign(payload: bytes) -> str:
    secret = get_settings().user_hmac_secret.encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def create_redirect_token(course_id: str, user_hash: str, ttl_seconds: int = 60 * 60 * 24 * 30) -> str:
    body = json.dumps(
        {"course_id": course_id, "user_hash": user_hash, "exp": int(time.time()) + ttl_seconds},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    return f"{encoded}.{_sign(body)}"


def parse_redirect_token(token: str) -> dict[str, Any]:
    try:
        encoded, signature = token.rsplit(".", 1)
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if not hmac.compare_digest(signature, _sign(body)):
            raise ValueError("signature")
        payload = json.loads(body)
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise ServiceError("VALIDATION_ERROR", "课程跳转链接无效或已过期", 400, False) from exc


def validate_recommendation(
    session: Session,
    decision: RecommendationDecision,
    *,
    user_hash: str,
    query: str,
) -> dict[str, Any]:
    snapshot = get_snapshot(session)
    courses = snapshot["courses"]
    routes = snapshot["routes"]
    valid_course_records: dict[str, dict[str, Any]] = {}
    for course_id in dict.fromkeys(decision.candidate_course_ids):
        course = courses.get(course_id)
        if not course or not course.get("enabled") or course.get("review_status") != "approved":
            continue
        valid_course_records[course_id] = course

    route = routes.get(decision.candidate_route_id) if decision.candidate_route_id else None
    if route and (not route.get("enabled") or route.get("review_status") != "approved"):
        route = None
    if route:
        route_course_ids = {
            course_id
            for stage in route.get("stages", [])
            for course_id in stage.get("course_ids", [])
        }
        if any(
            course_id not in courses
            or not courses[course_id].get("enabled")
            or courses[course_id].get("review_status") != "approved"
            for course_id in route_course_ids
        ):
            route = None

    progress_applied = False
    action = decision.progress_action or {}
    evidence = str(action.get("evidence_quote") or "")
    progress = None
    if route:
        progress = session.scalar(
            select(LearningRouteProgress).where(
                LearningRouteProgress.user_hash == user_hash,
                LearningRouteProgress.route_id == route["route_id"],
            )
        )
    if route and evidence and evidence in query and action.get("route_id") == route.get("route_id"):
        stages = sorted(route.get("stages", []), key=lambda item: item.get("order", 0))
        if action.get("type") == "confirm_route" and stages:
            if progress is None:
                progress = LearningRouteProgress(
                    user_hash=user_hash,
                    route_id=route["route_id"],
                    current_stage_id=stages[0]["stage_id"],
                    status="active",
                )
                session.add(progress)
            else:
                progress.status = "active"
            progress.confirmed_at = datetime.now(timezone.utc)
            progress_applied = True
        elif action.get("type") == "confirm_stage_completed" and progress:
            current_index = next(
                (
                    index
                    for index, stage in enumerate(stages)
                    if stage.get("stage_id") == progress.current_stage_id
                    and stage.get("stage_id") == action.get("stage_id")
                ),
                -1,
            )
            if current_index >= 0:
                if current_index + 1 < len(stages):
                    progress.current_stage_id = stages[current_index + 1]["stage_id"]
                    progress.status = "active"
                else:
                    progress.status = "completed"
                progress.confirmed_at = datetime.now(timezone.utc)
                progress_applied = True

    if route and decision.intent == "route_recommendation":
        stages = sorted(route.get("stages", []), key=lambda item: item.get("order", 0))
        current_stage_id = (
            progress.current_stage_id
            if progress and progress.status in {"active", "paused"}
            else (stages[0].get("stage_id") if stages else "")
        )
        allowed_course_ids = {
            course_id
            for stage in stages
            if stage.get("stage_id") == current_stage_id
            for course_id in stage.get("course_ids", [])
        }
        valid_course_records = {
            course_id: course
            for course_id, course in valid_course_records.items()
            if course_id in allowed_course_ids
        }
        route = {
            **route,
            "current_stage_id": current_stage_id,
            "progress_status": progress.status if progress else "not_started",
        }

    valid_courses: list[dict[str, Any]] = []
    for course_id, course in valid_course_records.items():
        card = {key: value for key, value in course.items() if key != "official_url"}
        token = create_redirect_token(course_id, user_hash)
        card["redirect_url"] = f"{get_settings().public_base_url.rstrip('/')}/r/{token}"
        valid_courses.append(card)

    if progress_applied:
        cache.delete(f"ai:route-progress:{user_hash}")

    return {
        "intent": decision.intent,
        "course_cards": valid_courses,
        "learning_route": route,
        "expert_insight": decision.expert_insight if valid_courses or route else "",
        "progress_applied": progress_applied,
    }


def record_click(session: Session, token: str) -> str:
    payload = parse_redirect_token(token)
    snapshot = get_snapshot(session)
    course = snapshot["courses"].get(payload["course_id"])
    if not course or not course.get("enabled") or course.get("review_status") != "approved":
        raise ServiceError("RECOMMENDATION_INVALID", "课程已下架或不可访问", 404, False)
    cache.increment_click(
        payload["course_id"],
        business_today().isoformat(),
        payload["user_hash"],
    )
    return str(course["official_url"])
