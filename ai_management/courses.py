from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cache import cache
from .config import get_settings
from .course_route_catalog import _collection_header
from .errors import ServiceError
from .models import (
    BusinessMessage,
    Course,
    CourseCollection,
    CourseCollectionMember,
    LearningRoute,
    LearningRouteProgress,
)
from .schemas import RecommendationDecision
from .services.admin_settings import get_catalog_revision
from .timeutil import business_today

COURSE_SNAPSHOT_KEY = "ai:course:snapshot"
COURSE_SNAPSHOT_VERSION = 2
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

CANONICAL_STAGE_ORDER = {
    "新手必看": 1,
    "入门阶段": 2,
    "高手进阶": 3,
}
_STAGE_ALIASES = {"高手阶段": "高手进阶"}
_SHORT_AFFIRMATIVE_REPLIES = {
    "好",
    "好的",
    "好呀",
    "可以",
    "可以的",
    "行",
    "行的",
    "确认",
    "确定",
    "是",
    "是的",
    "就这条",
    "选这个",
    "开始吧",
}


def _explicit_route_confirmation(query: str) -> bool:
    """Accept only an affirmative route-selection statement, never a question."""

    normalized = _normalize_text(query)
    if not normalized or "吗" in query or "？" in query or "?" in query:
        return False
    return any(
        marker in normalized
        for marker in (
            "我确认选择",
            "确认选择",
            "确认学习路线",
            "选择学习路线",
            "我选择",
            "我选",
            "决定选择",
            "采用这条",
            "开始这条",
        )
    )


def _explicit_stage_confirmation(query: str, stage: dict[str, Any]) -> bool:
    """Return true only for a user's explicit confirmation of a known stage.

    A later turn such as "我确认我目前在入门阶段" often has no useful
    retrieval result of its own.  It must nevertheless be actionable when the
    same user already selected exactly one official route.  Do not infer a
    stage merely because a name happens to occur: require a confirmation/state
    phrase and an exact published stage name or ID.
    """

    normalized = _normalize_text(query)
    if not normalized or "吗" in query or "？" in query or "?" in query:
        return False
    has_confirmation = any(
        token in normalized for token in ("确认", "目前在", "现在在", "我在", "处于", "当前是")
    )
    if not has_confirmation:
        return False
    stage_id = _normalize_text(str(stage.get("stage_id") or ""))
    stage_name = _normalize_text(str(stage.get("name") or ""))
    aliases = {
        stage_name,
        _normalize_text(_STAGE_ALIASES.get(str(stage.get("name") or ""), "")),
    }
    return bool(
        (stage_id and stage_id in normalized)
        or any(item and item in normalized for item in aliases)
    )


def _explicit_stage_completion(query: str, stage: dict[str, Any]) -> bool:
    """Return true for an unambiguous statement that a published stage is done."""

    normalized = _normalize_text(query)
    if not normalized or "吗" in query or "？" in query or "?" in query:
        return False
    if not any(
        marker in normalized
        for marker in ("已完成", "已经完成", "完成了", "我完成", "跳过", "略过")
    ):
        return False
    stage_id = _normalize_text(str(stage.get("stage_id") or ""))
    stage_name = _normalize_text(str(stage.get("name") or ""))
    aliases = {
        stage_name,
        _normalize_text(_STAGE_ALIASES.get(str(stage.get("name") or ""), "")),
    }
    return bool(
        (stage_id and stage_id in normalized)
        or any(item and item in normalized for item in aliases)
    )


def _is_short_affirmative_reply(query: str) -> bool:
    """Accept a terse yes only after the server proves its conversation context."""

    normalized = _normalize_text(query)
    if not normalized or "吗" in query or "？" in query or "?" in query:
        return False
    return normalized in _SHORT_AFFIRMATIVE_REPLIES


def _is_short_stage_reply(query: str, stage: dict[str, Any]) -> bool:
    """Recognize an exact stage-name reply, never infer a stage from a vague yes."""

    normalized = _normalize_text(query)
    if not normalized or "吗" in query or "？" in query or "?" in query:
        return False
    stage_id = _normalize_text(str(stage.get("stage_id") or ""))
    stage_name = _normalize_text(str(stage.get("name") or ""))
    aliases = {
        stage_id,
        stage_name,
        _normalize_text(_STAGE_ALIASES.get(str(stage.get("name") or ""), "")),
    }
    return normalized in {item for item in aliases if item}


def _is_route_related_query(query: str, snapshot: dict[str, Any]) -> bool:
    """Keep persisted route state from hijacking unrelated business QA."""

    normalized = _normalize_text(query)
    if any(
        token in normalized for token in _ROUTE_HINTS + ("阶段", "进度", "完成", "重学", "重新开始")
    ):
        return True
    return any(
        (name := _normalize_text(str(route.get("name") or ""))) and name in normalized
        for route in (snapshot.get("routes") or {}).values()
        if isinstance(route, dict)
    )


def _explicit_route_restart(query: str, route: dict[str, Any]) -> bool:
    normalized = _normalize_text(query)
    if not normalized or "吗" in query or "？" in query or "?" in query:
        return False
    name = _normalize_text(str(route.get("name") or ""))
    return bool(
        name
        and name in normalized
        and any(token in normalized for token in ("重新开始", "重学", "再学一遍", "重新学习"))
    )


def _stage_confirmation_hypothesis_text(stage_name: str) -> str:
    """Return the exact single-stage hypothesis appended to a route reply."""

    return f"我先按你从{stage_name}开始安排；如果符合，请回复“好的”确认。"


def _previous_completed_assistant(
    session: Session, *, conversation_id: str | None
) -> BusinessMessage | None:
    if not conversation_id:
        return None
    return session.scalar(
        select(BusinessMessage)
        .where(
            BusinessMessage.conversation_id == conversation_id,
            BusinessMessage.role == "assistant",
            BusinessMessage.status == "completed",
        )
        .order_by(BusinessMessage.created_at.desc(), BusinessMessage.id.desc())
    )


def _context_route_from_message(
    previous: BusinessMessage | None,
    *,
    state: str,
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve a validated route only from one server-persisted reply."""

    recommendation = previous.recommendation if previous else None
    learning_route = (
        recommendation.get("learning_route") if isinstance(recommendation, dict) else None
    )
    if not isinstance(learning_route, dict) or learning_route.get("state") != state:
        return None
    route_id = str(learning_route.get("route_id") or "")
    route = (snapshot.get("routes") or {}).get(route_id)
    if not isinstance(route, dict):
        return None
    if not route.get("enabled") or route.get("review_status") != "approved":
        return None
    return route


def _context_route_for_state(
    session: Session,
    *,
    conversation_id: str | None,
    state: str,
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the single official route the immediately previous reply asked about.

    A short reply such as ``好的`` is meaningful only when it follows this
    server-generated, validated route state in the same local conversation.
    Never recover a route from a profile, an arbitrary old message, or a model
    supplied identifier alone.
    """

    previous = _previous_completed_assistant(
        session,
        conversation_id=conversation_id,
    )
    return _context_route_from_message(previous, state=state, snapshot=snapshot)


def _context_stage_hypothesis(
    session: Session,
    *,
    conversation_id: str | None,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the one stage explicitly hypothesized in the previous reply.

    A bare affirmative is never mapped across every stage.  It is accepted only
    when the immediately preceding server reply used our deterministic
    single-stage hypothesis text, so older open-ended stage prompts cannot be
    silently reinterpreted as a first-stage selection.
    """

    previous = _previous_completed_assistant(
        session,
        conversation_id=conversation_id,
    )
    route = _context_route_from_message(
        previous,
        state="awaiting_stage_confirmation",
        snapshot=snapshot,
    )
    if not route or previous is None:
        return None
    content = str(previous.content or "")
    matches = [
        stage
        for stage in _sorted_route_stages(route)
        if (stage_name := str(stage.get("name") or "").strip())
        and _stage_confirmation_hypothesis_text(stage_name) in content
    ]
    if len(matches) != 1:
        return None
    return route, matches[0]


def _progress_confirmation_decision(
    session: Session,
    *,
    user_hash: str,
    query: str,
    conversation_id: str | None = None,
) -> RecommendationDecision | None:
    """Recover an explicit route-progress action without trusting an LLM.

    Dify is still used to answer the question.  This narrow recovery is only
    for a route already selected by the same user.  It avoids losing a valid
    stage confirmation just because the short follow-up did not retrieve the
    route document again.  Ambiguous matches deliberately return ``None``.
    """

    snapshot = get_snapshot(session)

    # Route confirmation is a deterministic user action.  The short answer
    # often causes the workflow to omit the retrieved route (or to return a
    # generic clarification), so resolve the unique published route name
    # directly from the authoritative snapshot before consulting any model
    # output.  Ambiguous names deliberately fall through to the normal flow.
    if _explicit_route_confirmation(query):
        normalized_query = _normalize_text(query)
        route_matches = [
            (route_id, route)
            for route_id, route in (snapshot.get("routes") or {}).items()
            if isinstance(route, dict)
            and route.get("enabled")
            and route.get("review_status") == "approved"
            and (
                (route_name := _normalize_text(str(route.get("name") or "")))
                and route_name in normalized_query
            )
        ]
        if len(route_matches) == 1:
            route_id, _route = route_matches[0]
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=str(route_id),
                progress_action={
                    "type": "confirm_route",
                    "route_id": str(route_id),
                    "evidence_quote": query,
                },
            )

    context_route = _context_route_for_state(
        session,
        conversation_id=conversation_id,
        state="awaiting_route_confirmation",
        snapshot=snapshot,
    )
    if context_route and _is_short_affirmative_reply(query):
        route_id = str(context_route.get("route_id") or "")
        progress = session.scalar(
            select(LearningRouteProgress).where(
                LearningRouteProgress.user_hash == user_hash,
                LearningRouteProgress.route_id == route_id,
            )
        )
        if progress is None:
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=route_id,
                progress_action={
                    "type": "confirm_route",
                    "route_id": route_id,
                    "evidence_quote": query,
                },
            )

    progresses = list(
        session.scalars(
            select(LearningRouteProgress).where(
                LearningRouteProgress.user_hash == user_hash,
                LearningRouteProgress.status.in_(("pending_stage", "active")),
            )
        )
    )
    completion_matches: list[tuple[LearningRouteProgress, dict[str, Any], dict[str, Any]]] = []
    matches: list[tuple[LearningRouteProgress, dict[str, Any], dict[str, Any]]] = []
    for progress in progresses:
        route = snapshot["routes"].get(progress.route_id)
        if not route:
            continue
        for stage in _sorted_route_stages(route):
            if _explicit_stage_completion(query, stage):
                completion_matches.append((progress, route, stage))
            if _explicit_stage_confirmation(query, stage):
                matches.append((progress, route, stage))

    # A learner may answer the stage question with a completed first (or later)
    # stage.  The exact named stage is the user's confirmation; once it is the
    # only pending-route match, advance from it instead of requiring a redundant
    # "I am currently at ..." turn.
    if len(completion_matches) == 1:
        progress, route, stage = completion_matches[0]
        if progress.status == "pending_stage" or (
            progress.status == "active"
            and str(progress.current_stage_id or "") == str(stage.get("stage_id") or "")
        ):
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=str(route["route_id"]),
                progress_action={
                    "type": "confirm_stage_completed",
                    "route_id": str(route["route_id"]),
                    "stage_id": str(stage["stage_id"]),
                    "evidence_quote": query,
                },
            )

    if len(matches) == 1:
        progress, route, stage = matches[0]
        if progress.status in {"pending_stage", "active"}:
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=str(route["route_id"]),
                progress_action={
                    "type": "confirm_current_stage",
                    "route_id": str(route["route_id"]),
                    "stage_id": str(stage["stage_id"]),
                    "evidence_quote": query,
                },
            )

    context_route = _context_route_for_state(
        session,
        conversation_id=conversation_id,
        state="awaiting_stage_confirmation",
        snapshot=snapshot,
    )
    if context_route:
        route_id = str(context_route.get("route_id") or "")
        progress = next(
            (
                item
                for item in progresses
                if item.route_id == route_id and item.status == "pending_stage"
            ),
            None,
        )
        stage_matches = [
            stage
            for stage in _sorted_route_stages(context_route)
            if _is_short_stage_reply(query, stage)
        ]
        if progress is not None and len(stage_matches) == 1:
            stage = stage_matches[0]
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=route_id,
                progress_action={
                    "type": "confirm_current_stage",
                    "route_id": route_id,
                    "stage_id": str(stage["stage_id"]),
                    "evidence_quote": query,
                },
            )

    context_hypothesis = _context_stage_hypothesis(
        session,
        conversation_id=conversation_id,
        snapshot=snapshot,
    )
    if context_hypothesis and _is_short_affirmative_reply(query):
        route, stage = context_hypothesis
        route_id = str(route.get("route_id") or "")
        progress = next(
            (
                item
                for item in progresses
                if item.route_id == route_id and item.status == "pending_stage"
            ),
            None,
        )
        if progress is not None:
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=route_id,
                progress_action={
                    "type": "confirm_current_stage",
                    "route_id": route_id,
                    "stage_id": str(stage["stage_id"]),
                    "evidence_quote": query,
                },
            )
    return None


def _route_revisit_decision(
    session: Session,
    *,
    user_hash: str,
    query: str,
    snapshot: dict[str, Any],
) -> RecommendationDecision | None:
    """Recover a selected route for a later route/progress question.

    This is intentionally user-level (not conversation-level) but only runs
    for route-related wording.  Exact route-name mentions win; otherwise a
    single selected route is safe.  Multiple selected routes require the
    normal clarification path instead of guessing.
    """

    if not _is_route_related_query(query, snapshot):
        return None
    progresses = [
        item
        for item in session.scalars(
            select(LearningRouteProgress).where(
                LearningRouteProgress.user_hash == user_hash,
                LearningRouteProgress.status.in_(("pending_stage", "active", "completed")),
            )
        )
        if (route := (snapshot.get("routes") or {}).get(item.route_id))
        and route.get("enabled")
        and route.get("review_status") == "approved"
    ]
    if not progresses:
        return None
    normalized = _normalize_text(query)
    exact = [
        item
        for item in progresses
        if (name := _normalize_text(str((snapshot["routes"][item.route_id]).get("name") or "")))
        and name in normalized
    ]
    if len(exact) == 1:
        progress = exact[0]
    elif len(progresses) == 1:
        progress = progresses[0]
    else:
        return None
    route = snapshot["routes"][progress.route_id]
    # Internal no-op marker prevents the model-route inference gate from
    # discarding a route that was selected from durable user progress.
    action: dict[str, Any] = {"type": "resume_route", "route_id": progress.route_id}
    if progress.status == "completed" and _explicit_route_restart(query, route):
        action = {
            "type": "restart_route",
            "route_id": progress.route_id,
            "evidence_quote": query,
        }
    return RecommendationDecision(
        intent="route_recommendation",
        candidate_route_id=progress.route_id,
        progress_action=action,
    )


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
        "series_name": course.series_name or None,
        "published_at": course.published_at.isoformat() if course.published_at else None,
        "first_imported_at": course.first_imported_at.isoformat()
        if course.first_imported_at
        else None,
        "prerequisite_course_ids": course.prerequisite_course_ids or [],
    }


def canonical_stage_name(value: Any) -> str:
    name = str(value or "").strip()
    return _STAGE_ALIASES.get(name, name)


def _ordered_stage_dicts(stages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    values = [dict(item) for item in (stages or []) if isinstance(item, dict)]
    values.sort(
        key=lambda item: (
            (
                0,
                CANONICAL_STAGE_ORDER[canonical_stage_name(item.get("name"))],
                int(item.get("order") or 0),
            )
            if canonical_stage_name(item.get("name")) in CANONICAL_STAGE_ORDER
            else (1, int(item.get("order") or 0), str(item.get("name") or ""))
        ),
    )
    ordered: list[dict[str, Any]] = []
    for position, item in enumerate(values, start=1):
        item["name"] = canonical_stage_name(item.get("name"))
        item["order"] = position
        ordered.append(item)
    return ordered


def _route_dict(route: LearningRoute) -> dict[str, Any]:
    return {
        "route_id": route.route_id,
        "name": route.name,
        "direction": route.direction,
        "target": route.target,
        "stages": _ordered_stage_dicts(route.stages),
        "enabled": route.enabled,
        "review_status": route.review_status,
    }


def _collection_dict(
    collection: CourseCollection, members: list[CourseCollectionMember]
) -> dict[str, Any]:
    ordered = sorted(members, key=lambda item: (item.order_rank, item.course_id))
    return {
        "collection_id": collection.collection_id,
        "name": collection.name,
        "raw_title": collection.raw_title,
        "declared_lessons": collection.declared_lessons,
        "total_minutes": collection.total_minutes,
        "entry_course_id": collection.entry_course_id,
        "course_ids": [item.course_id for item in ordered],
        "member_orders": [item.order_rank for item in ordered],
        "enabled": collection.enabled,
        "review_status": collection.review_status,
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
    collection_rows = list(
        session.scalars(
            select(CourseCollection).where(
                CourseCollection.enabled.is_(True),
                CourseCollection.review_status == "approved",
            )
        )
    )
    collection_ids = {item.collection_id for item in collection_rows}
    collection_members = (
        list(
            session.scalars(
                select(CourseCollectionMember).where(
                    CourseCollectionMember.collection_id.in_(collection_ids)
                )
            )
        )
        if collection_ids
        else []
    )
    members_by_collection: dict[str, list[CourseCollectionMember]] = {}
    for member in collection_members:
        if member.course_id in active_course_ids:
            members_by_collection.setdefault(member.collection_id, []).append(member)
    collections = {
        item.collection_id: _collection_dict(
            item, members_by_collection.get(item.collection_id, [])
        )
        for item in collection_rows
        if members_by_collection.get(item.collection_id)
    }
    if not collections:
        # Backward-compatible read path for catalogs published before the
        # collection tables existed. New publishes fill the authoritative
        # tables, while this fallback keeps direct-course matching available
        # during the additive migration window.
        fallback: dict[str, dict[str, Any]] = {}
        for course in courses:
            raw_series = str(course.series_name or "").strip()
            if not raw_series:
                continue
            parsed_header = _collection_header(raw_series)
            name = parsed_header[0] if parsed_header else raw_series
            collection_id = (
                "collection_"
                + __import__("hashlib").sha256(name.casefold().encode("utf-8")).hexdigest()[:12]
            )
            current = fallback.setdefault(
                collection_id,
                {
                    "collection_id": collection_id,
                    "name": name,
                    "raw_title": raw_series,
                    "declared_lessons": parsed_header[1] if parsed_header else None,
                    "total_minutes": parsed_header[2] if parsed_header else None,
                    "course_ids": [],
                    "enabled": True,
                    "review_status": "approved",
                },
            )
            current["course_ids"].append(course.course_id)
        collections = fallback
    snapshot = {
        "snapshot_version": COURSE_SNAPSHOT_VERSION,
        "catalog_revision": get_catalog_revision(session),
        "courses": {item.course_id: _course_dict(item) for item in courses},
        "routes": {item.route_id: _route_dict(item) for item in routes},
        "collections": collections,
    }
    cache.atomic_json_swap(COURSE_SNAPSHOT_KEY, snapshot)
    serialized_bytes = len(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
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
    revision = get_catalog_revision(session)
    if (
        isinstance(snapshot, dict)
        and snapshot.get("snapshot_version") == COURSE_SNAPSHOT_VERSION
        and "courses" in snapshot
        and "routes" in snapshot
        and "collections" in snapshot
        and snapshot.get("catalog_revision") == revision
    ):
        _SNAPSHOT_PARSE_SAMPLES_MS.append((time.perf_counter() - started) * 1000)
        del _SNAPSHOT_PARSE_SAMPLES_MS[:-100]
        return snapshot
    return rebuild_snapshot(session)


def public_catalog_names(snapshot: dict[str, Any]) -> dict[str, str]:
    """Return approved course/route/stage IDs mapped to display names."""

    names: dict[str, str] = {}
    for course_id, course in (snapshot.get("courses") or {}).items():
        if isinstance(course, dict) and course.get("name"):
            names[str(course_id)] = str(course["name"])
    for route_id, route in (snapshot.get("routes") or {}).items():
        if isinstance(route, dict) and route.get("name"):
            names[str(route_id)] = str(route["name"])
        for stage in _ordered_stage_dicts(route.get("stages") if isinstance(route, dict) else []):
            if stage.get("stage_id") and stage.get("name"):
                names[str(stage["stage_id"])] = str(stage["name"])
    return names


def public_name_fallbacks(
    snapshot: dict[str, Any],
    *,
    decision: RecommendationDecision | None = None,
    recommendation: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Build names used to recover legacy placeholder prose."""

    fallback: dict[str, list[str]] = {"course": [], "route": [], "stage": []}
    course_ids: list[str] = []
    route_id = ""
    if decision is not None:
        course_ids.extend(str(item) for item in decision.candidate_course_ids if str(item))
        route_id = str(decision.candidate_route_id or "")
    if recommendation:
        course_ids.extend(
            str(item.get("course_id") or "")
            for item in recommendation.get("course_cards") or []
            if isinstance(item, dict) and item.get("course_id")
        )
        learning_route = recommendation.get("learning_route")
        if isinstance(learning_route, dict):
            route_id = route_id or str(learning_route.get("route_id") or "")
    routes = snapshot.get("routes") or {}
    route = routes.get(route_id) if route_id else None
    if isinstance(route, dict):
        fallback["route"].append(str(route.get("name") or ""))
        for stage in _ordered_stage_dicts(route.get("stages")):
            if stage.get("name"):
                fallback["stage"].append(str(stage["name"]))
            course_ids.extend(str(item) for item in stage.get("course_ids", []) if str(item))
    courses = snapshot.get("courses") or {}
    for course_id in dict.fromkeys(course_ids):
        course = courses.get(course_id)
        if isinstance(course, dict) and course.get("name"):
            fallback["course"].append(str(course["name"]))
    return fallback


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
    return _ordered_stage_dicts(route.get("stages"))


def _attach_course_collection_names(
    route: dict[str, Any],
    collections: dict[str, dict[str, Any]] | None,
    courses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add display-only course and collection names to each route stage.

    Collection membership is resolved from the in-memory snapshot at response
    time.  The route snapshot itself is left unchanged so this additive field
    does not require a new Redis key or a catalog schema change.
    """

    stages: list[dict[str, Any]] = []
    for source_stage in route.get("stages") or []:
        stage = dict(source_stage)
        course_ids = [str(course_id or "").strip() for course_id in stage.get("course_ids") or []]
        # ``course_ids`` remains the stable machine-facing field.  The
        # parallel ``course_names`` field is intentionally built in exactly
        # the same order (including duplicate source entries) so clients can
        # render a complete route without exposing internal IDs.
        if courses is not None:
            stage["course_names"] = [
                str((courses.get(course_id) or {}).get("name") or "") for course_id in course_ids
            ]
        # Resolve collections from the stage's own course IDs.  A collection
        # may contain courses from another route in legacy/partially imported
        # data; intersecting with this stage prevents those courses from
        # leaking into the route display while preserving the existing
        # parallel name array for old clients.
        stage_course_set = set(course_ids)
        collection_details: list[tuple[int, str, dict[str, Any]]] = []
        stage_position = {course_id: index for index, course_id in enumerate(course_ids)}
        for collection in (collections or {}).values():
            if not isinstance(collection, dict):
                continue
            name = str(collection.get("name") or "").strip()
            if not name:
                continue
            member_ids = [
                str(course_id or "").strip() for course_id in collection.get("course_ids") or []
            ]
            scoped_ids = [course_id for course_id in member_ids if course_id in stage_course_set]
            if not scoped_ids:
                continue
            course_names = [
                str((courses.get(course_id) or {}).get("name") or course_id)
                if courses is not None
                else course_id
                for course_id in scoped_ids
            ]
            first_position = min(stage_position[course_id] for course_id in scoped_ids)
            collection_details.append(
                (
                    first_position,
                    str(collection.get("collection_id") or name),
                    {"name": name, "course_names": course_names},
                )
            )
        collection_details.sort(key=lambda item: (item[0], item[1]))
        stage["course_collections"] = [item[2] for item in collection_details]
        stage["course_collection_names"] = [item[2]["name"] for item in collection_details]
        stages.append(stage)
    return {**route, "stages": stages}


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
    explicit_platform = next(
        (item for item in sorted(platforms, key=len, reverse=True) if item in query), ""
    )
    if explicit_platform:
        matched = [item for item in matched if item.get("source_platform") == explicit_platform]
    series_names = {
        str(item.get("series_name") or "") for item in matched if str(item.get("series_name") or "")
    }
    explicit_series = next(
        (item for item in sorted(series_names, key=len, reverse=True) if item in query), ""
    )
    if explicit_series:
        matched = [item for item in matched if item.get("series_name") == explicit_series]
    return [
        str(item["course_id"])
        for item in sorted(matched, key=_effective_latest_key, reverse=True)[:5]
    ]


def _collection_match_score(query: str, collection: dict[str, Any]) -> int:
    name = str(collection.get("name") or "")
    normalized_query = _normalize_text(query)
    normalized_name = _normalize_text(name)
    if not normalized_query or not normalized_name:
        return 0
    # Accept the common conversational suffix while keeping partial matches
    # conservative; e.g. "AI写作" matches "AI写作课" but not another
    # unrelated collection that merely contains "写作".
    candidates = {normalized_name}
    for suffix in ("课程", "课"):
        if normalized_name.endswith(suffix) and len(normalized_name) > len(suffix):
            candidates.add(normalized_name[: -len(suffix)])
    if normalized_query in candidates or any(
        candidate in normalized_query for candidate in candidates
    ):
        return max(20, len(max(candidates, key=len)) * 3)
    return _score_query_match(query, name) if len(normalized_name) >= 4 else 0


def infer_recommendation_decision(
    session: Session,
    *,
    query: str,
    decision: RecommendationDecision | None = None,
) -> RecommendationDecision | None:
    snapshot = get_snapshot(session)
    courses = snapshot["courses"]
    routes = snapshot["routes"]
    route_like = any(hint in query for hint in _ROUTE_HINTS) or (
        "系统" in query and "学习" in query
    )
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
    route_requested = route_like or bool(decision and decision.intent == "route_recommendation")
    explicit_direct_course = bool(decision and decision.intent == "direct_course")

    latest_ids = _latest_course_ids(query, approved_courses)
    if latest_ids:
        latest_with_published_at = all(
            approved_courses[item].get("published_at") for item in latest_ids
        )
        return RecommendationDecision(
            intent="direct_course",
            candidate_course_ids=latest_ids,
            answer_plan=(
                "按首次发布时间最新" if latest_with_published_at else "按当前目录收录时间最新"
            ),
        )

    # Resolve a unique collection before route scoring. A collection is a
    # bounded direct-course recommendation; it must never silently expand into
    # the larger learning route that happens to contain its members.
    collection_matches: list[tuple[int, dict[str, Any]]] = []
    for collection in (snapshot.get("collections") or {}).values():
        if not collection.get("enabled") or collection.get("review_status") != "approved":
            continue
        ids = [
            course_id
            for course_id in collection.get("course_ids", [])
            if course_id in approved_courses
        ]
        if not ids:
            continue
        score = _collection_match_score(query, collection)
        if score > 0:
            collection_matches.append((score, {**collection, "course_ids": ids}))
    collection_matches.sort(
        key=lambda item: (item[0], str(item[1].get("collection_id") or "")), reverse=True
    )
    if collection_matches:
        best_score, best_collection = collection_matches[0]
        second_score = collection_matches[1][0] if len(collection_matches) > 1 else -1
        if best_score >= 20 and best_score - second_score >= 4:
            return RecommendationDecision(
                intent="direct_course",
                candidate_course_ids=list(best_collection["course_ids"])[:5],
                answer_plan=f"匹配课程合集：{best_collection['name']}",
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
    route_score_threshold = 10 if route_like else 18
    if route_scores:
        best_score, best_route_id = route_scores[0]
        second_score = route_scores[1][0] if len(route_scores) > 1 else -1
        if best_score >= route_score_threshold and best_score - second_score >= 4:
            route = approved_routes[best_route_id]
            return RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id=best_route_id,
            )

    # A route-seeking question must never degrade into a direct-course card
    # merely because the authoritative catalog has several plausible routes.
    # The caller must ask the learner to choose a route first.  Keep an
    # explicit direct-course decision intact: exact course lookup remains a
    # separate user journey even if its wording also mentions a route.
    if (
        route_requested
        and not explicit_direct_course
        and route_scores
        and route_scores[0][0] >= route_score_threshold
    ):
        return RecommendationDecision(
            intent="clarification",
            clarification_question="请先确认你希望学习的官方路线，我再继续确认当前阶段。",
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
        answer_plan=("未找到匹配的官方学习路线，以下为独立课程推荐。" if route_like else ""),
    )


def _sign(payload: bytes) -> str:
    secret = get_settings().user_hmac_secret.encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def create_redirect_token(
    course_id: str, user_hash: str, ttl_seconds: int = 60 * 60 * 24 * 30
) -> str:
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
    conversation_id: str | None = None,
    retrieved_course_ids: set[str] | None = None,
) -> dict[str, Any]:
    # A confirmed route is durable state in the authoritative database.  A
    # terse follow-up that explicitly names the learner's stage must not fail
    # merely because the workflow did not retrieve that route document again.
    # The recovery is intentionally narrower than recommendation inference and
    # accepts only one unambiguous, already-selected official route.
    progress_decision = _progress_confirmation_decision(
        session,
        user_hash=user_hash,
        query=query,
        conversation_id=conversation_id,
    )
    if progress_decision is not None:
        decision = progress_decision

    snapshot = get_snapshot(session)
    if progress_decision is None and not str((decision.progress_action or {}).get("type") or ""):
        revisit_decision = _route_revisit_decision(
            session,
            user_hash=user_hash,
            query=query,
            snapshot=snapshot,
        )
        if revisit_decision is not None:
            decision = revisit_decision
    courses = snapshot["courses"]
    routes = snapshot["routes"]
    valid_course_records: dict[str, dict[str, Any]] = {}
    for course_id in dict.fromkeys(decision.candidate_course_ids):
        # Candidate IDs made by the Dify direct-course branch are merely
        # hypotheses.  When raw retriever metadata is available, require the
        # course to have been retrieved.  Deterministic server fallbacks call
        # this function without the evidence set and keep their own rules.
        if (
            retrieved_course_ids is not None
            and decision.intent == "direct_course"
            and course_id not in retrieved_course_ids
        ):
            continue
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
    if (
        route
        and decision.intent == "route_recommendation"
        and (action.get("type") or "none") == "none"
    ):
        inferred = infer_recommendation_decision(session, query=query, decision=decision)
        if (
            not inferred
            or inferred.intent != "route_recommendation"
            or inferred.candidate_route_id != route.get("route_id")
        ):
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
        action_type = str(action.get("type") or "")
        route_confirmed = _explicit_route_confirmation(query)
        if not route_confirmed and _is_short_affirmative_reply(query):
            context_route = _context_route_for_state(
                session,
                conversation_id=conversation_id,
                state="awaiting_route_confirmation",
                snapshot=snapshot,
            )
            route_confirmed = bool(
                context_route
                and str(context_route.get("route_id") or "") == str(route.get("route_id") or "")
            )

        if action_type == "confirm_route" and stages and route_confirmed:
            if progress is None:
                progress = LearningRouteProgress(
                    user_hash=user_hash,
                    route_id=route["route_id"],
                    current_stage_id=None,
                    status="pending_stage",
                )
                session.add(progress)
            # Reconfirming an existing route must never silently erase
            # progress.  Explicit restart is the only reset operation.
            elif progress.status == "completed":
                pass
            progress.confirmed_at = datetime.now(timezone.utc)
            progress_applied = True
        elif action_type == "restart_route" and progress and progress.status == "completed":
            if _explicit_route_restart(query, route):
                progress.current_stage_id = None
                progress.status = "pending_stage"
                progress.confirmed_at = datetime.now(timezone.utc)
                progress_applied = True
        elif action_type == "confirm_current_stage" and progress:
            confirmed_stage_id = str(action.get("stage_id") or "")
            stage = next(
                (item for item in stages if item.get("stage_id") == confirmed_stage_id),
                None,
            )
            stage_confirmed = bool(stage and _explicit_stage_confirmation(query, stage))
            if stage and not stage_confirmed and _is_short_stage_reply(query, stage):
                context_route = _context_route_for_state(
                    session,
                    conversation_id=conversation_id,
                    state="awaiting_stage_confirmation",
                    snapshot=snapshot,
                )
                stage_confirmed = bool(
                    context_route
                    and str(context_route.get("route_id") or "") == str(route.get("route_id") or "")
                )
            if stage and not stage_confirmed and _is_short_affirmative_reply(query):
                context_hypothesis = _context_stage_hypothesis(
                    session,
                    conversation_id=conversation_id,
                    snapshot=snapshot,
                )
                stage_confirmed = bool(
                    context_hypothesis
                    and str(context_hypothesis[0].get("route_id") or "")
                    == str(route.get("route_id") or "")
                    and str(context_hypothesis[1].get("stage_id") or "") == confirmed_stage_id
                )
            if stage and stage_confirmed:
                progress.current_stage_id = confirmed_stage_id
                progress.status = "active"
                progress.confirmed_at = datetime.now(timezone.utc)
                progress_applied = True
        elif action_type == "confirm_stage_completed" and progress:
            if progress.status == "pending_stage":
                current_index = next(
                    (
                        index
                        for index, stage in enumerate(stages)
                        if stage.get("stage_id") == action.get("stage_id")
                    ),
                    -1,
                )
                current_stage = stages[current_index] if current_index >= 0 else None
                completion_confirmation = bool(
                    current_stage and _explicit_stage_completion(query, current_stage)
                )
            else:
                completion_confirmation = (
                    any(
                        marker in _normalize_text(query)
                        for marker in (
                            "已完成",
                            "已经完成",
                            "完成了",
                            "我完成",
                            "跳过",
                            "略过",
                        )
                    )
                    and "吗" not in query
                    and "？" not in query
                    and "?" not in query
                )
                current_index = next(
                    (
                        index
                        for index, stage in enumerate(stages)
                        if stage.get("stage_id") == progress.current_stage_id
                        and stage.get("stage_id") == action.get("stage_id")
                    ),
                    -1,
                )
            if completion_confirmation and current_index >= 0:
                if current_index + 1 < len(stages):
                    progress.current_stage_id = stages[current_index + 1]["stage_id"]
                    progress.status = "active"
                else:
                    progress.status = "completed"
                progress.confirmed_at = datetime.now(timezone.utc)
                progress_applied = True

    # A short follow-up can be classified by Dify as ``general_qa`` while it
    # still carries the already-selected authoritative route ID.  Once that
    # route has passed the server-side checks above, apply the same complete
    # route decoration and progress/card gating regardless of the model's
    # conversational intent.  Otherwise the response leaks raw stage/course
    # IDs and active learners receive no cards.
    if route:
        stages = sorted(route.get("stages", []), key=lambda item: item.get("order", 0))
        progress_status = progress.status if progress else "pending_route"
        current_stage_id = (
            progress.current_stage_id if progress and progress_status == "active" else None
        )
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
                # Course cards are actionable recommendations: only the
                # learner's exact confirmed stage, in official stage order.
                # next_stage_id remains route navigation metadata only.
                allowed_course_ids.extend(current_stage.get("course_ids", []))
                if current_index + 1 < len(stages):
                    next_stage = stages[current_index + 1]
                    next_stage_id = str(next_stage.get("stage_id") or "") or None
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

    if route:
        route = _attach_course_collection_names(
            route,
            snapshot.get("collections"),
            snapshot.get("courses"),
        )

    valid_courses: list[dict[str, Any]] = []
    # Direct/latest/collection recommendations stay compact.  A confirmed
    # active route is the explicit exception: return every eligible course in
    # its current stage as one batch.
    card_records = list(valid_course_records.items())
    if not (route and str(route.get("state") or "") == "active"):
        card_records = card_records[:5]
    for course_id, course in card_records:
        card = {key: value for key, value in course.items() if key != "official_url"}
        token = create_redirect_token(course_id, user_hash)
        card["redirect_url"] = f"{get_settings().public_base_url.rstrip('/')}/r/{token}"
        valid_courses.append(card)

    expert_insight = str(getattr(decision, "expert_insight", "") or "").strip()
    expert_ids = list(
        dict.fromkeys(
            str(item).strip()
            for item in getattr(decision, "expert_course_ids", [])
            if str(item).strip()
        )
    )
    final_ids = [str(item.get("course_id") or "") for item in valid_courses]
    if expert_insight and valid_courses and expert_ids == final_ids:
        # The model is not allowed to leak internal identifiers or direct
        # links. A failed content gate suppresses only the expert portion;
        # validated course cards remain usable.
        forbidden = set(final_ids) | set(routes) | set(courses)
        if not any(value and value in expert_insight for value in forbidden) and not re.search(
            r"https?://|www\.", expert_insight, flags=re.IGNORECASE
        ):
            expert_insight = re.sub(r"\s+", " ", expert_insight).strip()
            expert_insight = re.sub(
                r"^(?:专家建议|下一步建议)\s*[:：]?\s*", "", expert_insight
            ).strip()
            expert_insight = expert_insight[:80].rstrip()
        else:
            expert_insight = ""
    else:
        expert_insight = ""

    if progress_applied:
        cache.delete(f"ai:route-progress:{user_hash}")

    result = {
        "intent": decision.intent,
        "course_cards": valid_courses,
        "learning_route": route,
        "progress_applied": progress_applied,
    }
    if expert_insight:
        result["expert_insight"] = expert_insight
    return result


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
