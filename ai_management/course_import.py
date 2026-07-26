from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from knowledge_cleaner.dify import DifyKnowledgeClient

from .config import get_settings
from .courses import rebuild_snapshot
from .db import SessionLocal
from .models import Course, ExternalDeletionTask, LearningRoute
from .schemas import CourseRecord, RouteRecord


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


def load_catalog(path: Path) -> Catalog:
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.findall(r"```ya?ml\s*\n(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if not blocks:
        raise ValueError("课程资料中没有找到 ```yaml 数据块")
    course_values: list[dict[str, Any]] = []
    route_values: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        raw = yaml.safe_load(block)
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 个 YAML 数据块必须是对象")
        record_type = raw.pop("record_type", "")
        if record_type == "course":
            if not raw.get("course_id"):
                raw["course_id"] = _generated_id(
                    "course",
                    raw.get("direction", ""),
                    raw.get("name", ""),
                )
            course_values.append(raw)
        elif record_type == "route":
            if not raw.get("route_id"):
                raw["route_id"] = _generated_id(
                    "route",
                    raw.get("direction", ""),
                    raw.get("name", ""),
                )
            stages = raw.get("stages")
            if isinstance(stages, list):
                for position, stage in enumerate(stages, start=1):
                    if not isinstance(stage, dict):
                        continue
                    stage.setdefault("order", position)
                    if not stage.get("stage_id"):
                        stage["stage_id"] = (
                            f"{raw['route_id']}_stage_{stage['order']}"
                        )
            route_values.append(raw)
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
    for raw in route_values:
        for stage in raw.get("stages") or []:
            if isinstance(stage, dict) and isinstance(stage.get("course_ids"), list):
                stage["course_ids"] = [
                    name_to_id.get(str(value).strip(), str(value).strip())
                    for value in stage["course_ids"]
                ]
    routes = [RouteRecord.model_validate(raw) for raw in route_values]
    validate_catalog(courses, routes)
    return Catalog(courses=courses, routes=routes)


def validate_catalog(courses: list[CourseRecord], routes: list[RouteRecord]) -> None:
    course_ids = [item.course_id for item in courses]
    route_ids = [item.route_id for item in routes]
    if len(course_ids) != len(set(course_ids)):
        raise ValueError("课程 course_id 重复")
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("路线 route_id 重复")
    urls = [str(item.official_url) for item in courses]
    if len(urls) != len(set(urls)):
        raise ValueError("课程正式链接重复")
    known_courses = set(course_ids)
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


def _course_text(course: CourseRecord) -> str:
    lines = [
        f"# {course.name}",
        "",
        f"- 课程ID：{course.course_id}",
        f"- 所属方向：{course.direction}",
        f"- 主要内容或解决的问题：{course.description}",
    ]
    optional = (
        ("适合人群", course.audience),
        ("课程重点", "；".join(course.highlights)),
        ("学习结果", "；".join(course.learning_outcomes)),
        ("特殊前置条件", "；".join(course.special_prerequisites)),
        ("阶段标签", "；".join(course.stage_tags)),
    )
    lines.extend(f"- {name}：{value}" for name, value in optional if value)
    # The model never receives this URL as its final source of truth, but the
    # normalized catalog remains complete for administrators and exact queries.
    lines.append(f"- 官方链接：{course.official_url}")
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
                "review_status",
                "source_file",
            ]
        )
        with SessionLocal() as session:
            existing_courses = {item.course_id: item for item in session.scalars(select(Course))}
            existing_routes = {item.route_id: item for item in session.scalars(select(LearningRoute))}

        for course in catalog.courses:
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
                    "review_status": course.review_status,
                    "source_file": source_file.name,
                },
                fields,
            )
            replacements[("course", course.course_id)] = document_id
            previous = existing_courses.get(course.course_id)
            if previous and previous.dify_document_id:
                old_ids.append(previous.dify_document_id)

        for route in catalog.routes:
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
                    "review_status": route.review_status,
                    "source_file": source_file.name,
                },
                fields,
            )
            replacements[("route", route.route_id)] = document_id
            previous = existing_routes.get(route.route_id)
            if previous and previous.dify_document_id:
                old_ids.append(previous.dify_document_id)

        with SessionLocal() as session:
            incoming_course_ids = {item.course_id for item in catalog.courses}
            incoming_route_ids = {item.route_id for item in catalog.routes}
            for row in session.scalars(select(Course)):
                if row.course_id not in incoming_course_ids:
                    row.enabled = False
            for row in session.scalars(select(LearningRoute)):
                if row.route_id not in incoming_route_ids:
                    row.enabled = False

            for item in catalog.courses:
                row = session.get(Course, item.course_id)
                if row is None:
                    row = Course(course_id=item.course_id)
                    session.add(row)
                row.name = item.name
                row.direction = item.direction
                row.description = item.description
                row.official_url = str(item.official_url)
                row.enabled = item.enabled
                row.review_status = item.review_status
                row.optional_data = {
                    "audience": item.audience,
                    "highlights": item.highlights,
                    "learning_outcomes": item.learning_outcomes,
                    "special_prerequisites": item.special_prerequisites,
                    "stage_tags": item.stage_tags,
                }
                row.source_file = source_file.name
                row.dify_document_id = replacements[("course", item.course_id)]

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
                row.dify_document_id = replacements[("route", item.route_id)]
            session.commit()
            rebuild_snapshot(session)

        for old_id in dict.fromkeys(old_ids):
            if old_id not in created_ids:
                try:
                    _remove_or_disable(client, old_id)
                except Exception as exc:
                    _queue_cleanup(old_id, client.dataset_id, exc)
        return catalog_summary(catalog)
    except Exception:
        for document_id in created_ids:
            try:
                _remove_or_disable(client, document_id)
            except Exception as exc:
                _queue_cleanup(document_id, client.dataset_id, exc)
        raise
    finally:
        client.close()
