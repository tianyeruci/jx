from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import yaml
from sqlalchemy import select

from knowledge_cleaner.dify import DifyKnowledgeClient

from .config import get_settings
from .courses import rebuild_snapshot
from .services.admin_settings import bump_catalog_revision
from .db import SessionLocal
from .models import Course, ExternalDeletionTask, LearningRoute
from .schemas import CourseRecord, RouteRecord
from .services.observability import record_event


@dataclass(slots=True)
class Catalog:
    courses: list[CourseRecord]
    routes: list[RouteRecord]


def _generated_id(prefix: str, *parts: Any) -> str:
    normalized = "|".join(
        unicodedata.normalize("NFKC", str(part)).strip().casefold()
        for part in parts
    )
    if not normalized.replace("|", ""):
        raise ValueError(f"无法为缺少名称的 {prefix} 生成稳定ID")
    return f"{prefix}_{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def _catalog_blocks(text: str) -> list[dict[str, Any]]:
    blocks = re.findall(r"```ya?ml\s*\n(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if not blocks:
        raise ValueError("课程资料中没有找到 ```yaml 数据块")
    values: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        raw = yaml.safe_load(block)
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 个 YAML 数据块必须是对象")
        values.append(dict(raw))
    return values


def _prepare_course(raw: dict[str, Any]) -> dict[str, Any]:
    raw = dict(raw)
    raw.pop("record_type", None)
    if not raw.get("course_id"):
        raw["course_id"] = _generated_id("course", raw.get("direction", ""), raw.get("name", ""))
    raw.setdefault("source_platform", "小鹅通")
    return raw


def _prepare_route(raw: dict[str, Any]) -> dict[str, Any]:
    raw = dict(raw)
    raw.pop("record_type", None)
    if not raw.get("route_id"):
        raw["route_id"] = _generated_id("route", raw.get("direction", ""), raw.get("name", ""))
    stages = raw.get("stages")
    if isinstance(stages, list):
        for position, stage in enumerate(stages, start=1):
            if not isinstance(stage, dict):
                continue
            stage.setdefault("order", position)
            if not stage.get("stage_id"):
                stage["stage_id"] = f"{raw['route_id']}_stage_{stage['order']}"
    return raw


def _resolve_route_course_names(
    route_values: list[dict[str, Any]], *, name_to_id: dict[str, str]
) -> None:
    for raw in route_values:
        for stage in raw.get("stages") or []:
            if isinstance(stage, dict) and isinstance(stage.get("course_ids"), list):
                stage["course_ids"] = [
                    name_to_id.get(str(value).strip(), str(value).strip())
                    for value in stage["course_ids"]
                ]


def load_catalog(path: Path) -> Catalog:
    text = path.read_text(encoding="utf-8-sig")
    return load_catalog_text(text)


def load_catalog_text(text: str) -> Catalog:
    course_values: list[dict[str, Any]] = []
    route_values: list[dict[str, Any]] = []
    for index, raw in enumerate(_catalog_blocks(text), start=1):
        record_type = raw.get("record_type", "")
        if record_type == "course":
            course_values.append(_prepare_course(raw))
        elif record_type == "route":
            route_values.append(_prepare_route(raw))
        else:
            raise ValueError(f"第 {index} 个 YAML 数据块 record_type 必须是 course 或 route")
    courses = [CourseRecord.model_validate(raw) for raw in course_values]
    name_to_ids: dict[str, list[str]] = {}
    for course in courses:
        name_to_ids.setdefault(course.name.strip(), []).append(course.course_id)
    ambiguous_names = {
        name: ids for name, ids in name_to_ids.items() if len(ids) > 1
    }
    if ambiguous_names:
        raise ValueError(
            f"课程名称重复，路线不能按名称解析：{sorted(ambiguous_names)}"
        )
    name_to_id = {name: ids[0] for name, ids in name_to_ids.items()}
    _resolve_route_course_names(route_values, name_to_id=name_to_id)
    routes = [RouteRecord.model_validate(raw) for raw in route_values]
    validate_catalog(courses, routes)
    return Catalog(courses=courses, routes=routes)


def load_course_records_text(
    text: str,
    *,
    known_course_ids: set[str] | None = None,
    validate_prerequisite_references: bool = True,
) -> list[CourseRecord]:
    values = _catalog_blocks(text)
    raw_courses = [_prepare_course(item) for item in values if item.get("record_type") == "course"]
    if len(raw_courses) != len(values):
        raise ValueError("课程信息文件只能包含 record_type: course")
    courses = [CourseRecord.model_validate(item) for item in raw_courses]
    validate_catalog(
        courses,
        [],
        known_course_ids=known_course_ids,
        validate_prerequisite_references=validate_prerequisite_references,
    )
    for course in courses:
        if not course.stage.strip():
            raise ValueError(f"课程 {course.course_id} 缺少必填 stage")
    return courses


def load_route_records_text(
    text: str, *, known_courses: dict[str, str]
) -> list[RouteRecord]:
    values = _catalog_blocks(text)
    raw_routes = [_prepare_route(item) for item in values if item.get("record_type") == "route"]
    if len(raw_routes) != len(values):
        raise ValueError("学习路线文件只能包含 record_type: route")
    names: dict[str, list[str]] = {}
    for course_id, name in known_courses.items():
        names.setdefault(name.strip(), []).append(course_id)
    ambiguous = {name for name, ids in names.items() if len(ids) > 1}
    if ambiguous:
        # A route can still use explicit IDs, but never a name that resolves to
        # more than one course.
        for raw in raw_routes:
            for stage in raw.get("stages") or []:
                for value in stage.get("course_ids", []) if isinstance(stage, dict) else []:
                    if str(value).strip() in ambiguous:
                        raise ValueError(f"路线课程名称不唯一，请改用 course_id：{value}")
    _resolve_route_course_names(
        raw_routes,
        name_to_id={name: ids[0] for name, ids in names.items() if len(ids) == 1},
    )
    routes = [RouteRecord.model_validate(item) for item in raw_routes]
    validate_catalog([], routes, known_course_ids=set(known_courses))
    return routes


def validate_catalog(
    courses: list[CourseRecord],
    routes: list[RouteRecord],
    *,
    known_course_ids: set[str] | None = None,
    validate_prerequisite_references: bool = True,
) -> None:
    course_ids = [item.course_id for item in courses]
    route_ids = [item.route_id for item in routes]
    if len(course_ids) != len(set(course_ids)):
        raise ValueError("课程 course_id 重复")
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("路线 route_id 重复")
    urls = [str(item.official_url) for item in courses]
    if len(urls) != len(set(urls)):
        raise ValueError("课程正式链接重复")
    known_courses = set(course_ids) | (known_course_ids or set())
    for course in courses:
        if course.course_id in course.prerequisite_course_ids:
            raise ValueError(f"课程 {course.course_id} 不能把自己设为前置课程")
        if validate_prerequisite_references:
            unknown_prerequisites = set(course.prerequisite_course_ids) - known_courses
            if unknown_prerequisites:
                raise ValueError(
                    f"课程 {course.course_id} 引用了不存在的前置课程：{sorted(unknown_prerequisites)}"
                )
    for route in routes:
        stage_ids = [stage.stage_id for stage in route.stages]
        stage_orders = [stage.order for stage in route.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError(f"路线 {route.route_id} 的 stage_id 重复")
        if len(stage_orders) != len(set(stage_orders)):
            raise ValueError(f"路线 {route.route_id} 的阶段顺序重复")
        if stage_orders != sorted(stage_orders):
            raise ValueError(f"路线 {route.route_id} 的阶段必须按顺序书写")
        if stage_orders != list(range(1, len(stage_orders) + 1)):
            raise ValueError(f"路线 {route.route_id} 的阶段顺序必须从1连续递增")
        route_course_ids = [
            course_id
            for stage in route.stages
            for course_id in stage.course_ids
        ]
        if len(route_course_ids) != len(set(route_course_ids)):
            raise ValueError(f"路线 {route.route_id} 的课程不能跨阶段重复")
        unknown = {
            course_id
            for stage in route.stages
            for course_id in stage.course_ids
            if course_id not in known_courses
        }
        if unknown:
            raise ValueError(f"路线 {route.route_id} 引用了不存在课程：{sorted(unknown)}")


def catalog_summary(catalog: Catalog) -> dict[str, Any]:
    return {
        "courses": len(catalog.courses),
        "enabled_courses": sum(item.enabled and item.review_status == "approved" for item in catalog.courses),
        "routes": len(catalog.routes),
        "enabled_routes": sum(item.enabled and item.review_status == "approved" for item in catalog.routes),
        "course_ids": [item.course_id for item in catalog.courses],
        "route_ids": [item.route_id for item in catalog.routes],
    }


def sync_cleaning_catalog_record(
    session,
    *,
    strategy_code: str,
    payload: dict[str, Any],
    dify_document_id: str,
    source_file: str,
) -> str:
    """Apply one Dify-verified catalogue document to the authority DB.

    The caller must commit before deleting the returned old Dify document.  A
    failed database transaction therefore leaves the previously authoritative
    document in place and lets the worker delete the just-created copy.
    """
    if strategy_code == "course_information":
        item = CourseRecord.model_validate(payload)
        if item.course_id in item.prerequisite_course_ids:
            raise ValueError(f"课程 {item.course_id} 不能把自己设为前置课程")
        known_course_ids = set(session.scalars(select(Course.course_id)))
        validate_catalog([item], [], known_course_ids=known_course_ids)
        row = session.get(Course, item.course_id)
        old_document_id = row.dify_document_id if row else ""
        if row is None:
            row = Course(course_id=item.course_id)
            session.add(row)
        row.name = item.name
        row.direction = item.direction
        row.description = item.description
        row.official_url = str(item.official_url)
        row.stage = item.stage
        row.source_platform = item.source_platform or "小鹅通"
        row.series_name = item.series_name or None
        row.published_at = item.published_at
        row.prerequisite_course_ids = list(item.prerequisite_course_ids)
        if row.first_imported_at is None:
            row.first_imported_at = datetime.now(timezone.utc)
        row.enabled = item.enabled
        row.review_status = item.review_status
        row.optional_data = {
            "audience": item.audience,
            "highlights": item.highlights,
            "learning_outcomes": item.learning_outcomes,
            "special_prerequisites": item.special_prerequisites,
            "stage_tags": item.stage_tags,
            "prerequisite_course_ids": item.prerequisite_course_ids,
        }
        row.source_file = source_file
        row.dify_document_id = dify_document_id or None
        return old_document_id or ""

    if strategy_code == "learning_route":
        item = RouteRecord.model_validate(payload)
        known_course_ids = set(
            session.scalars(
                select(Course.course_id).where(
                    Course.enabled.is_(True), Course.review_status == "approved"
                )
            )
        )
        validate_catalog([], [item], known_course_ids=known_course_ids)
        row = session.get(LearningRoute, item.route_id)
        old_document_id = row.dify_document_id if row else ""
        if row is None:
            row = LearningRoute(route_id=item.route_id)
            session.add(row)
        row.name = item.name
        row.direction = item.direction
        row.target = item.target
        row.enabled = item.enabled
        row.review_status = item.review_status
        row.stages = [stage.model_dump(mode="json") for stage in item.stages]
        row.source_file = source_file
        row.dify_document_id = dify_document_id or None
        return old_document_id or ""

    return ""


def _course_text(course: CourseRecord) -> str:
    lines = [
        f"# {course.name}",
        "",
        f"- 课程ID：{course.course_id}",
        f"- 所属方向：{course.direction}",
        f"- 课程难度阶段：{course.stage}",
        f"- 来源平台：{course.source_platform}",
        f"- 主要内容或解决的问题：{course.description}",
    ]
    optional = (
        ("适合人群", course.audience),
        ("课程重点", "；".join(course.highlights)),
        ("学习结果", "；".join(course.learning_outcomes)),
        ("特殊前置条件", "；".join(course.special_prerequisites)),
        ("阶段标签", "；".join(course.stage_tags)),
        ("所属系列", course.series_name or ""),
        ("首次发布时间", course.published_at.isoformat() if course.published_at else ""),
        ("前置课程ID", "、".join(course.prerequisite_course_ids)),
    )
    lines.extend(f"- {name}：{value}" for name, value in optional if value)
    # Official URLs are intentionally database-only.  The model returns a
    # signed server-side redirect card and must never see a raw course URL.
    return "\n".join(lines)


def _route_text(route: RouteRecord) -> str:
    lines = [
        f"# {route.name}",
        "",
        f"- 路线ID：{route.route_id}",
        f"- 所属方向：{route.direction}",
        f"- 适用目标：{route.target}",
    ]
    for stage in sorted(route.stages, key=lambda item: item.order):
        lines.extend(
            [
                "",
                f"## {stage.order}. {stage.name}",
                f"- 阶段ID：{stage.stage_id}",
                f"- 课程ID：{'、'.join(stage.course_ids)}",
            ]
        )
    return "\n".join(lines)


def _catalog_document_name(title: str, stable_id: str) -> str:
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title)
    safe_title = re.sub(r"\s+", " ", safe_title).strip(" ._") or "catalog"
    if len(safe_title) > 60:
        safe_title = f"{safe_title[:40]}…{safe_title[-19:]}"
    return f"{safe_title}__{stable_id}.md"


def _remove_or_disable(
    client: DifyKnowledgeClient,
    document_id: str,
) -> None:
    try:
        client.delete_document(document_id)
        return
    except Exception:
        client.disable_document(document_id)


def _queue_cleanup(document_id: str, dataset_id: str, error: Exception) -> None:
    with SessionLocal() as session:
        session.add(
            ExternalDeletionTask(
                kind="dify_document",
                external_id=document_id,
                payload={"dataset_id": dataset_id},
                attempts=0,
                last_error=str(error)[:1000],
            )
        )
        session.commit()


def publish_catalog(catalog: Catalog, source_file: Path) -> dict[str, Any]:
    settings = get_settings()
    if not settings.dify_knowledge_api_key or not settings.dify_course_dataset_id:
        raise ValueError("发布前必须配置 DIFY_KNOWLEDGE_API_KEY 和 DIFY_COURSE_DATASET_ID")
    client = DifyKnowledgeClient(
        settings.knowledge_base_url,
        settings.dify_knowledge_api_key,
        settings.dify_course_dataset_id,
        timeout=180,
    )
    created_ids: list[str] = []
    replacements: dict[tuple[str, str], str] = {}
    old_ids: list[str] = []
    try:
        fields = client.ensure_metadata_names(
            [
                "document_type",
                "course_id",
                "route_id",
                "direction",
                "stage",
                "source_platform",
                "series_name",
                "status",
            ]
        )
        with SessionLocal() as session:
            existing_courses = {item.course_id: item for item in session.scalars(select(Course))}
            existing_routes = {item.route_id: item for item in session.scalars(select(LearningRoute))}

        for course in catalog.courses:
            previous = existing_courses.get(course.course_id)
            if previous and previous.dify_document_id:
                old_ids.append(previous.dify_document_id)
            if not course.enabled or course.review_status != "approved":
                replacements[("course", course.course_id)] = ""
                continue
            document_id, batch = client.create_document(
                _catalog_document_name(course.name, course.course_id),
                _course_text(course),
            )
            created_ids.append(document_id)
            client.wait_for_indexing(batch, timeout_seconds=1800)
            client.ensure_document_has_segments(document_id)
            client.set_document_metadata(
                document_id,
                {
                    "document_type": "course",
                    "course_id": course.course_id,
                    "direction": course.direction,
                    "stage": course.stage,
                    "source_platform": course.source_platform,
                    "series_name": course.series_name or "",
                    "status": "active" if course.enabled and course.review_status == "approved" else "inactive",
                },
                fields,
            )
            client.verify_document_metadata(
                document_id,
                {
                    "document_type": "course",
                    "course_id": course.course_id,
                    "direction": course.direction,
                    "stage": course.stage,
                    "source_platform": course.source_platform or "小鹅通",
                    "series_name": course.series_name or "",
                    "status": "active" if course.enabled and course.review_status == "approved" else "inactive",
                },
            )
            replacements[("course", course.course_id)] = document_id

        for route in catalog.routes:
            previous = existing_routes.get(route.route_id)
            if previous and previous.dify_document_id:
                old_ids.append(previous.dify_document_id)
            if not route.enabled or route.review_status != "approved":
                replacements[("route", route.route_id)] = ""
                continue
            document_id, batch = client.create_document(
                _catalog_document_name(route.name, route.route_id),
                _route_text(route),
            )
            created_ids.append(document_id)
            client.wait_for_indexing(batch, timeout_seconds=1800)
            client.ensure_document_has_segments(document_id)
            client.set_document_metadata(
                document_id,
                {
                    "document_type": "learning_route",
                    "route_id": route.route_id,
                    "direction": route.direction,
                    "status": "active" if route.enabled and route.review_status == "approved" else "inactive",
                },
                fields,
            )
            client.verify_document_metadata(
                document_id,
                {
                    "document_type": "learning_route",
                    "route_id": route.route_id,
                    "direction": route.direction,
                    "status": "active" if route.enabled and route.review_status == "approved" else "inactive",
                },
            )
            replacements[("route", route.route_id)] = document_id

        with SessionLocal() as session:
            for item in catalog.courses:
                row = session.get(Course, item.course_id)
                if row is None:
                    row = Course(course_id=item.course_id)
                    session.add(row)
                row.name = item.name
                row.direction = item.direction
                row.description = item.description
                row.official_url = str(item.official_url)
                row.stage = item.stage
                row.source_platform = item.source_platform or "小鹅通"
                row.series_name = item.series_name or None
                row.published_at = item.published_at
                row.prerequisite_course_ids = list(item.prerequisite_course_ids)
                if row.first_imported_at is None:
                    row.first_imported_at = datetime.now(timezone.utc)
                row.enabled = item.enabled
                row.review_status = item.review_status
                row.optional_data = {
                    "audience": item.audience,
                    "highlights": item.highlights,
                    "learning_outcomes": item.learning_outcomes,
                    "special_prerequisites": item.special_prerequisites,
                    "stage_tags": item.stage_tags,
                    "prerequisite_course_ids": item.prerequisite_course_ids,
                }
                row.source_file = source_file.name
                row.dify_document_id = replacements[("course", item.course_id)] or None

            for item in catalog.routes:
                row = session.get(LearningRoute, item.route_id)
                if row is None:
                    row = LearningRoute(route_id=item.route_id)
                    session.add(row)
                row.name = item.name
                row.direction = item.direction
                row.target = item.target
                row.enabled = item.enabled
                row.review_status = item.review_status
                row.stages = [stage.model_dump(mode="json") for stage in item.stages]
                row.source_file = source_file.name
                row.dify_document_id = replacements[("route", item.route_id)] or None
            bump_catalog_revision(session)
            session.commit()
            rebuild_snapshot(session)

        for old_id in dict.fromkeys(old_ids):
            if old_id not in created_ids:
                try:
                    _remove_or_disable(client, old_id)
                except Exception as exc:
                    _queue_cleanup(old_id, client.dataset_id, exc)
        summary = catalog_summary(catalog)
        record_event(
            level="INFO",
            category="operation",
            component="course_catalog",
            event_code="COURSE_CATALOG_PUBLISHED",
            status="succeeded",
            message="课程与路线目录发布完成",
            resource_type="source_file",
            resource_id=source_file.name,
            details={
                **summary,
                "created_documents": len(created_ids),
                "retired_documents": len(set(old_ids)),
            },
        )
        return summary
    except Exception as exc:
        for document_id in created_ids:
            try:
                _remove_or_disable(client, document_id)
            except Exception as cleanup_exc:
                _queue_cleanup(document_id, client.dataset_id, cleanup_exc)
        record_event(
            level="ERROR",
            category="error",
            component="course_catalog",
            event_code="COURSE_CATALOG_PUBLISH_FAILED",
            status="failed",
            message=f"{type(exc).__name__}: {str(exc)[:800]}",
            resource_type="source_file",
            resource_id=source_file.name,
            details={"created_documents": len(created_ids)},
        )
        raise
    finally:
        client.close()
