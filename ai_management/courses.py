from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import date, datetime, timezone
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
_SNAPSHOT_PARSE_SAMPLES_MS: list[float] = []
_ROUTE_HINTS = (
    "学习路线",
    "学习路径",
    "路线",
    "系统学习",
    "从哪里开始",
    "怎么开始",
    "先学",
    "按什么顺序",
)
_COURSE_HINTS = ("课程", "学习", "入门", "推荐", "从零")
_FRAGMENT_STOPWORDS = {
    "学习",
    "系统",
    "课程",
    "路线",
    "推荐",
    "开始",
    "哪里",
    "什么",
    "怎么",
    "从零",
    "相关",
}


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
        "stage": course.stage or "",
        "source_platform": course.source_platform or "小鹅通",
        "series_name": course.series_name or "",
        "published_at": course.published_at.isoformat() if course.published_at else "",
        "first_imported_at": course.first_imported_at.isoformat() if course.first_imported_at else "",
        "prerequisite_course_ids": course.prerequisite_course_ids or [],
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


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3)


def rebuild_snapshot(session: Session) -> dict[str, Any]:
    started = time.perf_counter()
    courses = list(
        session.scalars(
            select(Course).where(Course.enabled.is_(True), Course.review_status == "approved")
        )
    )
    routes = list(
        session.scalars(
            select(LearningRoute).where(
                LearningRoute.enabled.is_(True), LearningRoute.review_status == "approved"
            )
        )
    )
    active_course_ids = {item.course_id for item in courses}
    routes = [
        route
        for route in routes
        if all(
            course_id in active_course_ids
            for stage in (route.stages or [])
            for course_id in (stage.get("course_ids", []) if isinstance(stage, dict) else [])
        )
    ]
    snapshot = {
        "courses": {item.course_id: _course_dict(item) for item in courses},
        "routes": {item.route_id: _route_dict(item) for item in routes},
    }
    cache.atomic_json_swap(COURSE_SNAPSHOT_KEY, snapshot)
    serialized_bytes = len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    snapshot["metrics"] = {
        "effective_records": len(courses) + len(routes),
        "serialized_bytes": serialized_bytes,
        "swap_ms": round((time.perf_counter() - started) * 1000, 3),
        "parse_p95_ms": _p95(_SNAPSHOT_PARSE_SAMPLES_MS),
    }
    return snapshot


def get_snapshot(session: Session) -> dict[str, Any]:
    started = time.perf_counter()
    snapshot = cache.get_json(COURSE_SNAPSHOT_KEY)
    if isinstance(snapshot, dict) and "courses" in snapshot and "routes" in snapshot:
        _SNAPSHOT_PARSE_SAMPLES_MS.append((time.perf_counter() - started) * 1000)
        del _SNAPSHOT_PARSE_SAMPLES_MS[:-100]
        return snapshot
    return rebuild_snapshot(session)


def _normalize_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value or "").lower()


def _text_fragments(value: str) -> list[str]:
    normalized = _normalize_text(value)
    if len(normalized) < 2:
        return []
    fragments: set[str] = {normalized}
    upper = min(len(normalized), 8)
    for size in range(upper, 1, -1):
        for start in range(0, len(normalized) - size + 1):
            fragment = normalized[start : start + size]
            if fragment in _FRAGMENT_STOPWORDS:
                continue
            if len(fragment) == 2 and not fragment.isascii():
                continue
            fragments.add(fragment)
    return sorted(fragments, key=len, reverse=True)


def _score_query_match(query: str, *texts: str) -> int:
    query_text = _normalize_text(query)
    if not query_text:
        return 0
    score = 0
    matched: set[str] = set()
    for text in texts:
        normalized = _normalize_text(text)
        if not normalized:
            continue
        if normalized in query_text:
            score += max(8, len(normalized) * 3)
            matched.add(normalized)
        for fragment in _text_fragments(normalized):
            if fragment in matched or fragment not in query_text:
                continue
            if len(fragment) >= 4 or fragment.isascii():
                score += len(fragment) * 2
                matched.add(fragment)
            if len(matched) >= 5:
                break
    return score


def _sorted_route_stages(route: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(route.get("stages", []), key=lambda item: item.get("order", 0))


def _first_stage_course_ids(route: dict[str, Any]) -> list[str]:
    stages = _sorted_route_stages(route)
    if not stages:
        return []
    return list(dict.fromkeys(stages[0].get("course_ids", [])))


def _effective_latest_key(course: dict[str, Any]) -> tuple[str, str]:
    # ISO date/datetime strings sort chronologically and avoid changing the
    # immutable fallback timestamp whenever administrators edit a catalogue row.
    return (
        str(course.get("published_at") or course.get("first_imported_at") or ""),
        str(course.get("course_id") or ""),
    )


def _latest_course_ids(query: str, courses: dict[str, dict[str, Any]]) -> list[str]:
    if "最新" not in query:
        return []
    matched = list(courses.values())
    platforms = {
        str(item.get("source_platform") or "")
        for item in matched
        if str(item.get("source_platform") or "")
    }
    explicit_platform = next((item for item in sorted(platforms, key=len, reverse=True) if item in query), "")
    if explicit_platform:
        matched = [item for item in matched if item.get("source_platform") == explicit_platform]
    series_names = {
        str(item.get("series_name") or "")
        for item in matched
        if str(item.get("series_name") or "")
    }
    explicit_series = next((item for item in sorted(series_names, key=len, reverse=True) if item in query), "")
    if explicit_series:
        matched = [item for item in matched if item.get("series_name") == explicit_series]
    return [
        str(item["course_id"])
        for item in sorted(matched, key=_effective_latest_key, reverse=True)[:5]
    ]


def infer_recommendation_decision(
    session: Session,
    *,
    query: str,
    decision: RecommendationDecision | None = None,
) -> RecommendationDecision | None:
    snapshot = get_snapshot(session)
    courses = snapshot["courses"]
    routes = snapshot["routes"]
    route_like = any(hint in query for hint in _ROUTE_HINTS) or ("系统" in query and "学习" in query)
    course_like = any(hint in query for hint in _COURSE_HINTS)

    approved_courses = {
        course_id: item
        for course_id, item in courses.items()
        if item.get("enabled") and item.get("review_status") == "approved"
    }
    approved_routes = {
        route_id: item
        for route_id, item in routes.items()
        if item.get("enabled") and item.get("review_status") == "approved"
    }
    candidate_course_ids = [
        course_id
        for course_id in (decision.candidate_course_ids if decision else [])
        if course_id in approved_courses
    ]

    latest_ids = _latest_course_ids(query, approved_courses)
    if latest_ids:
        latest_with_published_at = all(approved_courses[item].get("published_at") for item in latest_ids)
        return RecommendationDecision(
            intent="direct_course",
            candidate_course_ids=latest_ids,
            answer_plan=(
                "按首次发布时间最新" if latest_with_published_at else "按当前目录收录时间最新"
            ),
        )

    route_scores: list[tuple[int, str]] = []
    for route_id, route in approved_routes.items():
        course_ids = [
            course_id
            for stage in _sorted_route_stages(route)
            for course_id in stage.get("course_ids", [])
            if course_id in approved_courses
        ]
        if not course_ids:
            continue
        overlap = len(set(candidate_course_ids) & set(course_ids))
        stage_names = [str(stage.get("name") or "") for stage in _sorted_route_stages(route)]
        course_names = [approved_courses[course_id]["name"] for course_id in course_ids[:6]]
        score = overlap * 30 + _score_query_match(
            query,
            str(route.get("name") or ""),
            str(route.get("direction") or ""),
            str(route.get("target") or ""),
            *stage_names,
            *course_names,
        )
        if route_like:
            score += 5
        if score > 0:
            route_scores.append((score, route_id))
    route_scores.sort(reverse=True)
    if route_scores:
        best_score, best_route_id = route_scores[0]
        second_score = route_scores[1][0] if len(route_scores) > 1 else -1
        if best_score >= (10 if route_like else 18) and best_score - second_score >= 4:
            route = approved_routes[best_route_id]
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=best_route_id,
            )

    if not course_like and not candidate_course_ids:
        return None
    course_scores: list[tuple[int, str]] = []
    for course_id, course in approved_courses.items():
        exact_name_match = _normalize_text(str(course.get("name") or "")) in _normalize_text(query)
        if not str(course.get("stage") or "").strip() and not exact_name_match:
            # Legacy rows without a confirmed global difficulty are still
            # available for an explicit course lookup, never generic matching.
            continue
        score = _score_query_match(
            query,
            str(course.get("name") or ""),
            str(course.get("direction") or ""),
            str(course.get("description") or ""),
        )
        if course_like:
            score += 3
        if course_id in candidate_course_ids:
            score += 20
        if score > 0:
            course_scores.append((score, course_id))
    course_scores.sort(reverse=True)
    if not course_scores:
        return None
    best_score, best_course_id = course_scores[0]
    second_score = course_scores[1][0] if len(course_scores) > 1 else -1
    if best_score < 8 or best_score - second_score < 4:
        return None
    return RecommendationDecision(
        intent="direct_course",
        candidate_course_ids=[best_course_id],
        answer_plan=(
            "未找到匹配的官方学习路线，以下为独立课程推荐。" if route_like else ""
        ),
    )


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

    action = decision.progress_action or {}
    # An LLM-supplied route ID is evidence only, never an authorization to
    # auto-select a weak match.  Explicit confirmation actions are handled
    # below against the selected route/progress state.
    if route and decision.intent == "route_recommendation" and (action.get("type") or "none") == "none":
        inferred = infer_recommendation_decision(session, query=query, decision=decision)
        if not inferred or inferred.intent != "route_recommendation" or inferred.candidate_route_id != route.get("route_id"):
            route = None

    progress_applied = False
    evidence = str(action.get("evidence_quote") or "")
    progress: LearningRouteProgress | None = None
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
                    current_stage_id=None,
                    status="pending_stage",
                )
                session.add(progress)
            else:
                progress.current_stage_id = None
                progress.status = "pending_stage"
            progress.confirmed_at = datetime.now(timezone.utc)
            progress_applied = True
        elif action.get("type") == "confirm_current_stage" and progress:
            confirmed_stage_id = str(action.get("stage_id") or "")
            if any(stage.get("stage_id") == confirmed_stage_id for stage in stages):
                progress.current_stage_id = confirmed_stage_id
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
        progress_status = progress.status if progress else "pending_route"
        current_stage_id = progress.current_stage_id if progress and progress_status == "active" else None
        next_stage_id: str | None = None
        allowed_course_ids: list[str] = []
        if current_stage_id:
            current_index = next(
                (
                    index
                    for index, stage in enumerate(stages)
                    if stage.get("stage_id") == current_stage_id
                ),
                -1,
            )
            if current_index >= 0:
                current_stage = stages[current_index]
                allowed_course_ids.extend(current_stage.get("course_ids", []))
                if current_index + 1 < len(stages):
                    next_stage = stages[current_index + 1]
                    next_stage_id = str(next_stage.get("stage_id") or "") or None
                    allowed_course_ids.extend(next_stage.get("course_ids", []))
        # Do not issue any course card until both the route and the learner's
        # current stage have been explicitly confirmed.  The first stage is a
        # prompt for confirmation, not an implicit enrollment decision.
        valid_course_records = {
            course_id: courses[course_id]
            for course_id in dict.fromkeys(allowed_course_ids)
            if course_id in courses
            and courses[course_id].get("enabled")
            and courses[course_id].get("review_status") == "approved"
        }
        route = {
            **route,
            "current_stage_id": current_stage_id,
            "next_stage_id": next_stage_id,
            "state": {
                "pending_route": "awaiting_route_confirmation",
                "pending_stage": "awaiting_stage_confirmation",
                "active": "active",
                "completed": "completed",
            }.get(progress_status, "awaiting_route_confirmation"),
            "progress_status": progress_status,
            "confirmation_required": progress_status in {"pending_route", "pending_stage"},
        }
    elif decision.intent == "route_recommendation":
        # A route recommendation without a validated route must never fall
        # back to model-supplied course IDs.  Ask for clarification instead.
        valid_course_records = {}

    valid_courses: list[dict[str, Any]] = []
    for course_id, course in list(valid_course_records.items())[:5]:
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
        # Kept as an empty compatibility field.  Course-level "expert
        # insight" is not source material and must not be invented.
        "expert_insight": "",
        "recommendation_note": decision.answer_plan if valid_courses else "",
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
