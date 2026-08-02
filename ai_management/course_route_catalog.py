from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openpyxl import load_workbook

REQUIRED_COLUMNS = ("课程名称", "课程链接", "阶段", "学习路线")
ALLOWED_COLUMNS = frozenset(
    {
        *REQUIRED_COLUMNS,
        "系列名称",
        "课程序号",
        "阶段序号",
        "路线序号",
        "课程方向",
        "课程描述",
        "解决问题",
        "适用人群",
        "课程亮点",
        "学习目标",
        "路线目标",
        "前置条件",
    }
)
_TRACKING_QUERY_KEYS = frozenset(
    {"spm", "from", "source", "ref", "share", "share_id"}
)
_ORDER_PREFIX_RE = re.compile(r"^\s*([1-9]\d*)\s*[/／]\s*")
MAX_CATALOG_ROWS = 100_000
MAX_CATALOG_COLUMNS = 64


def normalize_course_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"课程链接无效：{raw[:80]}")
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(sorted(query)),
            "",
        )
    )


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def _value(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value).strip()


def _order_value(row: dict[str, Any], key: str, fallback: int) -> tuple[int, float, int]:
    raw = _value(row, key)
    if raw:
        try:
            return (0, float(raw), fallback)
        except ValueError:
            pass
    return (1, float(fallback), fallback)


def _explicit_order(row: dict[str, Any], key: str) -> int | None:
    raw = _value(row, key)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0 or not value.is_integer():
        return None
    return int(value)


def _strip_order_prefix(value: str) -> tuple[str, int | None]:
    match = _ORDER_PREFIX_RE.match(value)
    if match is None:
        return value.strip(), None
    return value[match.end() :].strip(), int(match.group(1))


@dataclass(slots=True)
class CatalogAudit:
    sheet_name: str
    source_sha256: str
    source_rows: int
    unknown_columns: list[str] = field(default_factory=list)
    duplicate_link_groups: int = 0
    group_headers: int = 0
    effective_groups: int = 0
    invalid_rows: int = 0
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CourseDraft:
    course_id: str
    name: str
    normalized_url: str
    stage: str
    route_name: str
    series_name: str
    direction_candidate: str
    description_candidate: str
    audience_candidate: str
    highlights_candidate: str
    learning_outcomes_candidate: str
    source_rows: list[int]
    review_status: str = "pending_review"
    name_candidates: list[str] = field(default_factory=list)
    series_candidates: list[str] = field(default_factory=list)
    structure_conflicts: list[dict[str, Any]] = field(default_factory=list)
    first_order_rank: int = 0

    @property
    def model_text(self) -> str:
        payload = {
            "course_id": self.course_id,
            "name": self.name,
            "stage": self.stage,
            "route_name": self.route_name,
            "series_name": self.series_name,
            "direction_candidate": self.direction_candidate,
            "description_candidate": self.description_candidate,
            "audience_candidate": self.audience_candidate,
            "highlights_candidate": self.highlights_candidate,
            "learning_outcomes_candidate": self.learning_outcomes_candidate,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@dataclass(slots=True)
class RouteDraft:
    route_id: str
    name: str
    direction_candidate: str
    target_candidate: str
    stages: list[dict[str, Any]]
    source_rows: list[int]
    review_status: str = "pending_review"

    @property
    def model_text(self) -> str:
        payload = {
            "route_id": self.route_id,
            "name": self.name,
            "direction_candidate": self.direction_candidate,
            "target_candidate": self.target_candidate,
            "stages": self.stages,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@dataclass(slots=True)
class CourseRouteCatalogDraft:
    courses: list[CourseDraft]
    routes: list[RouteDraft]
    audit: CatalogAudit


def _nonempty_sheets(path: Path) -> list[tuple[str, list[list[Any]]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheets: list[tuple[str, list[list[Any]]]] = []
    try:
        for sheet in workbook.worksheets:
            # Some spreadsheet writers emit a stale ``<dimension ref="A1">``
            # while the worksheet XML contains the complete table.  openpyxl's
            # read-only iterator trusts that dimension unless it is reset.
            # Resetting keeps streaming/memory bounds while reading the actual
            # worksheet cells instead of silently truncating the source.
            sheet.reset_dimensions()
            rows: list[list[Any]] = []
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if row_number > MAX_CATALOG_ROWS + 1:
                    raise ValueError(
                        f"课程目录数据行超过上限：{MAX_CATALOG_ROWS}"
                    )
                values = list(row)
                if len(values) > MAX_CATALOG_COLUMNS:
                    raise ValueError(
                        f"课程目录列数超过上限：{MAX_CATALOG_COLUMNS}"
                    )
                if any(value not in (None, "") for value in values):
                    rows.append(values)
            if rows:
                sheets.append((sheet.title, rows))
    finally:
        workbook.close()
    return sheets


def _legacy_parse_course_route_catalog(
    path: Path,
    *,
    existing_course_ids_by_url: dict[str, str],
) -> CourseRouteCatalogDraft:
    sheets = _nonempty_sheets(path)
    if len(sheets) != 1:
        raise ValueError("课程目录工作簿必须有且仅有一个非空工作表")
    sheet_name, rows = sheets[0]
    headers = [str(value or "").strip() for value in rows[0]]
    missing = [name for name in REQUIRED_COLUMNS if name not in headers]
    if missing:
        raise ValueError(f"课程目录缺少必需列：{missing}")
    unknown = sorted({name for name in headers if name and name not in ALLOWED_COLUMNS})
    source_rows: list[tuple[int, dict[str, Any]]] = []
    for row_number, values in enumerate(rows[1:], start=2):
        record = {
            header: values[index] if index < len(values) else None
            for index, header in enumerate(headers)
            if header
        }
        if not any(_value(record, key) for key in REQUIRED_COLUMNS):
            continue
        source_rows.append((row_number, record))

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    warnings: list[dict[str, Any]] = []
    for row_number, row in source_rows:
        raw_url = _value(row, "课程链接")
        if not raw_url:
            warnings.append(
                {
                    "code": "missing_link_parent_or_note",
                    "row": row_number,
                    "message": "无链接行仅作系列/说明审计，不生成课程",
                }
            )
            continue
        try:
            normalized_url = normalize_course_url(raw_url)
        except ValueError as exc:
            warnings.append(
                {
                    "code": "invalid_course_url",
                    "row": row_number,
                    "message": str(exc),
                }
            )
            continue
        grouped.setdefault(normalized_url, []).append((row_number, row))

    courses: list[CourseDraft] = []
    course_by_url: dict[str, CourseDraft] = {}
    for normalized_url, members in grouped.items():
        first_row_number, first = members[0]
        name = next(
            (_value(row, "课程名称") for _, row in members if _value(row, "课程名称")),
            "",
        )
        if not name:
            warnings.append(
                {
                    "code": "missing_course_name",
                    "row": first_row_number,
                    "message": "有链接但没有课程名称",
                }
            )
            continue
        route_name = next(
            (_value(row, "学习路线") for _, row in members if _value(row, "学习路线")),
            "",
        )
        stage = next(
            (_value(row, "阶段") for _, row in members if _value(row, "阶段")),
            "",
        )
        course = CourseDraft(
            course_id=existing_course_ids_by_url.get(normalized_url)
            or _stable_id("course", normalized_url),
            name=name,
            normalized_url=normalized_url,
            stage=stage,
            route_name=route_name,
            series_name=next(
                (_value(row, "系列名称") for _, row in members if _value(row, "系列名称")),
                "",
            ),
            direction_candidate=next(
                (_value(row, "课程方向") for _, row in members if _value(row, "课程方向")),
                "",
            ),
            description_candidate=next(
                (
                    _value(row, "课程描述") or _value(row, "解决问题")
                    for _, row in members
                    if _value(row, "课程描述") or _value(row, "解决问题")
                ),
                "",
            ),
            audience_candidate=next(
                (_value(row, "适用人群") for _, row in members if _value(row, "适用人群")),
                "",
            ),
            highlights_candidate=next(
                (_value(row, "课程亮点") for _, row in members if _value(row, "课程亮点")),
                "",
            ),
            learning_outcomes_candidate=next(
                (_value(row, "学习目标") for _, row in members if _value(row, "学习目标")),
                "",
            ),
            source_rows=[row_number for row_number, _ in members],
        )
        courses.append(course)
        course_by_url[normalized_url] = course

        for field_name in ("课程名称", "阶段", "系列名称", "课程方向"):
            distinct = {
                _value(row, field_name)
                for _, row in members
                if _value(row, field_name)
            }
            if len(distinct) > 1:
                warnings.append(
                    {
                        "code": "duplicate_link_field_conflict",
                        "row": first_row_number,
                        "field": field_name,
                        "message": "同一课程链接存在不一致字段，需人工确认",
                    }
                )

    courses.sort(key=lambda item: min(item.source_rows))
    route_members: dict[
        str,
        list[tuple[CourseDraft, int, dict[str, Any]]],
    ] = {}
    route_order: dict[str, tuple[int, float, int]] = {}
    course_routes: dict[str, list[str]] = {}
    for normalized_url, members in grouped.items():
        course = course_by_url.get(normalized_url)
        if course is None:
            continue
        seen_route_names: set[str] = set()
        for row_number, row in members:
            route_name = _value(row, "学习路线")
            if not route_name:
                continue
            route_members.setdefault(route_name, []).append(
                (course, row_number, row)
            )
            route_order[route_name] = min(
                route_order.get(
                    route_name,
                    _order_value(row, "路线序号", row_number),
                ),
                _order_value(row, "路线序号", row_number),
            )
            if route_name not in seen_route_names:
                course_routes.setdefault(course.course_id, []).append(route_name)
                seen_route_names.add(route_name)
    for course in courses:
        memberships = course_routes.get(course.course_id) or []
        course.route_name = "；".join(
            sorted(memberships, key=lambda item: route_order[item])
        )

    routes: list[RouteDraft] = []
    for route_name in sorted(
        route_members,
        key=lambda item: route_order[item],
    ):
        raw_members = route_members[route_name]
        member_by_course: dict[str, tuple[CourseDraft, int, dict[str, Any]]] = {}
        for course, row_number, row in sorted(
            raw_members,
            key=lambda item: _order_value(item[2], "课程序号", item[1]),
        ):
            existing = member_by_course.get(course.course_id)
            if existing is not None:
                if _value(existing[2], "阶段") != _value(row, "阶段"):
                    warnings.append(
                        {
                            "code": "route_course_stage_conflict",
                            "row": row_number,
                            "message": "同一路线中的同一课程出现多个阶段，保留首项待人工确认",
                        }
                    )
                continue
            member_by_course[course.course_id] = (course, row_number, row)
        members = list(member_by_course.values())
        stages: list[dict[str, Any]] = []
        stage_order: dict[str, tuple[int, float, int]] = {}
        for _course, row_number, row in members:
            stage_name = _value(row, "阶段") or "待确认阶段"
            stage_order[stage_name] = min(
                stage_order.get(
                    stage_name,
                    _order_value(row, "阶段序号", row_number),
                ),
                _order_value(row, "阶段序号", row_number),
            )
        stage_names = sorted(stage_order, key=lambda item: stage_order[item])
        for position, stage_name in enumerate(stage_names, start=1):
            stage_courses = [
                (course, row_number, row)
                for course, row_number, row in members
                if (_value(row, "阶段") or "待确认阶段") == stage_name
            ]
            stage_courses.sort(
                key=lambda item: _order_value(
                    item[2],
                    "课程序号",
                    item[1],
                )
            )
            stages.append(
                {
                    "stage_id": _stable_id(
                        "stage", f"{route_name}|{stage_name}|{position}"
                    ),
                    "name": stage_name,
                    "order": position,
                    "course_ids": [item[0].course_id for item in stage_courses],
                    "order_source": "source_columns_or_first_occurrence_pending_review",
                }
            )
        route_rows = sorted({row_number for _, row_number, _ in members})
        target = next(
            (
                _value(row, "路线目标")
                for row_number, row in source_rows
                if row_number in route_rows and _value(row, "路线目标")
            ),
            "",
        )
        direction = next(
            (
                _value(row, "课程方向")
                for _, _, row in members
                if _value(row, "课程方向")
            ),
            "",
        )
        routes.append(
            RouteDraft(
                route_id=_stable_id("route", route_name.strip().casefold()),
                name=route_name,
                direction_candidate=direction,
                target_candidate=target,
                stages=stages,
                source_rows=route_rows,
            )
        )

    return CourseRouteCatalogDraft(
        courses=courses,
        routes=routes,
        audit=CatalogAudit(
            sheet_name=sheet_name,
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            source_rows=len(source_rows),
            unknown_columns=unknown,
            duplicate_link_groups=sum(len(items) > 1 for items in grouped.values()),
            warnings=warnings,
        ),
    )


def parse_course_route_catalog(
    path: Path,
    *,
    existing_course_ids_by_url: dict[str, str],
) -> CourseRouteCatalogDraft:
    """Parse the catalogue without trusting XLSX dimensions or row order hints.

    A non-linked, named row opens a course collection.  Linked rows inherit only
    that collection name; route/stage values remain row-level facts.  Order
    markers are local to contiguous runs so a new ``1/`` sequence after an
    unnumbered course does not reorder the previous run.
    """

    sheets = _nonempty_sheets(path)
    if len(sheets) != 1:
        raise ValueError("课程目录工作簿必须有且仅有一个非空工作表")
    sheet_name, rows = sheets[0]
    headers = [str(value or "").strip() for value in rows[0]]
    missing = [name for name in REQUIRED_COLUMNS if name not in headers]
    if missing:
        raise ValueError(f"课程目录缺少必需列：{missing}")
    unknown = sorted({name for name in headers if name and name not in ALLOWED_COLUMNS})
    warnings: list[dict[str, Any]] = []
    source_rows: list[tuple[int, dict[str, Any]]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    members_by_group: dict[int, list[dict[str, Any]]] = {}
    group_names: dict[int, str] = {}
    group_header_rows: dict[int, int] = {}
    current_group = 0
    next_group = 0

    for row_number, values in enumerate(rows[1:], start=2):
        record = {
            header: values[index] if index < len(values) else None
            for index, header in enumerate(headers)
            if header
        }
        if not any(_value(record, key) for key in REQUIRED_COLUMNS):
            continue
        source_rows.append((row_number, record))
        name = _value(record, "课程名称")
        raw_url = _value(record, "课程链接")
        if not name:
            warnings.append(
                {
                    "code": "invalid_empty_course_name",
                    "row": row_number,
                    "message": "课程名称为空，跳过该行",
                }
            )
            continue
        if not raw_url:
            next_group += 1
            current_group = next_group
            group_names[current_group] = name
            group_header_rows[current_group] = row_number
            members_by_group.setdefault(current_group, [])
            continue
        try:
            normalized_url = normalize_course_url(raw_url)
        except ValueError as exc:
            warnings.append(
                {
                    "code": "invalid_course_url",
                    "row": row_number,
                    "message": str(exc),
                }
            )
            continue
        display_name, name_order = _strip_order_prefix(name)
        if not display_name:
            warnings.append(
                {
                    "code": "invalid_empty_course_name_after_order_prefix",
                    "row": row_number,
                    "message": "移除顺序前缀后课程名称为空，跳过该行",
                }
            )
            continue
        explicit_order = _explicit_order(record, "课程序号")
        raw_explicit_order = _value(record, "课程序号")
        if raw_explicit_order and explicit_order is None and name_order is None:
            warnings.append(
                {
                    "code": "invalid_course_order",
                    "row": row_number,
                    "message": "课程序号必须是正整数，回退到源表顺序",
                }
            )
        member = {
            "row_number": row_number,
            "row": record,
            "normalized_url": normalized_url,
            "name": display_name,
            "name_order": name_order,
            "explicit_order": explicit_order,
            "order_marker": explicit_order if explicit_order is not None else name_order,
            "group_id": current_group,
            "group_name": group_names.get(current_group, ""),
        }
        grouped.setdefault(normalized_url, []).append(member)
        members_by_group.setdefault(current_group, []).append(member)

    ordered_members: list[dict[str, Any]] = []
    for group_id in sorted(members_by_group, key=lambda item: group_header_rows.get(item, 1)):
        group_members = members_by_group[group_id]
        if not group_members:
            if group_id:
                warnings.append(
                    {
                        "code": "empty_course_collection",
                        "row": group_header_rows[group_id],
                        "message": "课程合集标题后没有有效课程，跳过该合集",
                    }
                )
            continue
        segment: list[dict[str, Any]] = []

        def flush_segment() -> None:
            if not segment:
                return
            marker_values = [int(item["order_marker"]) for item in segment]
            seen_markers: set[int] = set()
            for item in segment:
                marker = item["order_marker"]
                if marker in seen_markers:
                    warnings.append(
                        {
                            "code": "duplicate_course_order",
                            "row": item["row_number"],
                            "message": "同一连续顺序段内课程序号重复，需人工审核",
                        }
                    )
                seen_markers.add(marker)
            if marker_values and set(marker_values) != set(range(1, max(marker_values) + 1)):
                warnings.append(
                    {
                        "code": "course_order_gap",
                        "row": segment[0]["row_number"],
                        "message": "连续课程序号存在缺号，保留现有数字顺序",
                    }
                )
            segment.sort(key=lambda item: (int(item["order_marker"]), item["row_number"]))
            ordered_members.extend(segment)
            segment.clear()

        for member in group_members:
            if member["order_marker"] is None:
                flush_segment()
                ordered_members.append(member)
            else:
                segment.append(member)
        flush_segment()

    for order_rank, member in enumerate(ordered_members, start=1):
        member["order_rank"] = order_rank

    courses: list[CourseDraft] = []
    course_by_url: dict[str, CourseDraft] = {}
    for normalized_url, raw_members in grouped.items():
        members = [member for member in raw_members if "order_rank" in member]
        if not members:
            continue
        members.sort(key=lambda item: item["order_rank"])
        name_candidates = list(dict.fromkeys(item["name"] for item in members))
        series_candidates = list(
            dict.fromkeys(
                [item["group_name"] for item in members if item["group_name"]]
                + [
                    _value(item["row"], "系列名称")
                    for item in members
                    if _value(item["row"], "系列名称")
                ]
            )
        )
        conflicts: list[dict[str, Any]] = []
        if len(name_candidates) > 1:
            conflicts.append(
                {
                    "code": "duplicate_link_name_conflict",
                    "candidates": name_candidates,
                    "source_rows": [item["row_number"] for item in members],
                }
            )
            warnings.append(
                {
                    "code": "duplicate_link_name_conflict",
                    "row": members[0]["row_number"],
                    "message": "同一课程链接对应多个课程名称，需结构审核",
                }
            )
        if len(series_candidates) > 1:
            conflicts.append(
                {
                    "code": "duplicate_link_series_conflict",
                    "candidates": series_candidates,
                    "source_rows": [item["row_number"] for item in members],
                }
            )
            warnings.append(
                {
                    "code": "duplicate_link_series_conflict",
                    "row": members[0]["row_number"],
                    "message": "同一课程链接对应多个课程合集，需结构审核",
                }
            )
        course = CourseDraft(
            course_id=existing_course_ids_by_url.get(normalized_url)
            or _stable_id("course", normalized_url),
            name=name_candidates[0],
            normalized_url=normalized_url,
            stage=next(
                (_value(item["row"], "阶段") for item in members if _value(item["row"], "阶段")),
                "",
            ),
            route_name="",
            series_name=series_candidates[0] if series_candidates else "",
            direction_candidate=next(
                (_value(item["row"], "课程方向") for item in members if _value(item["row"], "课程方向")),
                "",
            ),
            description_candidate=next(
                (
                    _value(item["row"], "课程描述") or _value(item["row"], "解决问题")
                    for item in members
                    if _value(item["row"], "课程描述") or _value(item["row"], "解决问题")
                ),
                "",
            ),
            audience_candidate=next(
                (_value(item["row"], "适用人群") for item in members if _value(item["row"], "适用人群")),
                "",
            ),
            highlights_candidate=next(
                (_value(item["row"], "课程亮点") for item in members if _value(item["row"], "课程亮点")),
                "",
            ),
            learning_outcomes_candidate=next(
                (_value(item["row"], "学习目标") for item in members if _value(item["row"], "学习目标")),
                "",
            ),
            source_rows=[item["row_number"] for item in members],
            name_candidates=name_candidates,
            series_candidates=series_candidates,
            structure_conflicts=conflicts,
            first_order_rank=members[0]["order_rank"],
        )
        courses.append(course)
        course_by_url[normalized_url] = course
        for field_name in ("课程名称", "阶段", "系列名称", "课程方向"):
            distinct = {
                _value(item["row"], field_name)
                for item in members
                if _value(item["row"], field_name)
            }
            if len(distinct) > 1:
                warnings.append(
                    {
                        "code": "duplicate_link_field_conflict",
                        "row": members[0]["row_number"],
                        "field": field_name,
                        "message": "同一课程链接存在不一致字段，需人工确认",
                    }
                )

    courses.sort(key=lambda item: item.first_order_rank or min(item.source_rows))
    route_members: dict[str, list[dict[str, Any]]] = {}
    route_order: dict[str, tuple[int, float, int]] = {}
    course_routes: dict[str, list[str]] = {}
    for member in ordered_members:
        course = course_by_url.get(member["normalized_url"])
        if course is None:
            continue
        route_name = _value(member["row"], "学习路线")
        if not route_name:
            continue
        route_member = dict(member)
        route_member["course"] = course
        route_members.setdefault(route_name, []).append(route_member)
        route_order[route_name] = min(
            route_order.get(route_name, _order_value(member["row"], "路线序号", member["order_rank"])),
            _order_value(member["row"], "路线序号", member["order_rank"]),
        )
        course_routes.setdefault(course.course_id, [])
        if route_name not in course_routes[course.course_id]:
            course_routes[course.course_id].append(route_name)
    for course in courses:
        course.route_name = "；".join(
            sorted(course_routes.get(course.course_id, []), key=lambda item: route_order[item])
        )

    routes: list[RouteDraft] = []
    for route_name in sorted(route_members, key=lambda item: route_order[item]):
        member_by_course: dict[str, dict[str, Any]] = {}
        for member in route_members[route_name]:
            course = member["course"]
            existing = member_by_course.get(course.course_id)
            if existing is not None:
                if _value(existing["row"], "阶段") != _value(member["row"], "阶段"):
                    warnings.append(
                        {
                            "code": "route_course_stage_conflict",
                            "row": member["row_number"],
                            "message": "同一路线中的同一课程出现多个阶段，需结构审核",
                        }
                    )
                continue
            member_by_course[course.course_id] = member
        members = list(member_by_course.values())
        stage_order: dict[str, tuple[int, float, int]] = {}
        for member in members:
            stage_name = _value(member["row"], "阶段") or "待确认阶段"
            stage_order[stage_name] = min(
                stage_order.get(
                    stage_name,
                    _order_value(member["row"], "阶段序号", member["order_rank"]),
                ),
                _order_value(member["row"], "阶段序号", member["order_rank"]),
            )
        stages: list[dict[str, Any]] = []
        for position, stage_name in enumerate(sorted(stage_order, key=stage_order.get), start=1):
            stage_courses = [
                member
                for member in members
                if (_value(member["row"], "阶段") or "待确认阶段") == stage_name
            ]
            stage_courses.sort(key=lambda item: item["order_rank"])
            stages.append(
                {
                    "stage_id": _stable_id("stage", f"{route_name}|{stage_name}|{position}"),
                    "name": stage_name,
                    "order": position,
                    "course_ids": [member["course"].course_id for member in stage_courses],
                    "order_source": "source_order_or_course_name_prefix_pending_review",
                }
            )
        route_rows = sorted({member["row_number"] for member in members})
        target = next(
            (
                _value(row, "路线目标")
                for row_number, row in source_rows
                if row_number in route_rows and _value(row, "路线目标")
            ),
            "",
        )
        direction = next(
            (_value(member["row"], "课程方向") for member in members if _value(member["row"], "课程方向")),
            "",
        )
        routes.append(
            RouteDraft(
                route_id=_stable_id("route", route_name.strip().casefold()),
                name=route_name,
                direction_candidate=direction,
                target_candidate=target,
                stages=stages,
                source_rows=route_rows,
            )
        )

    return CourseRouteCatalogDraft(
        courses=courses,
        routes=routes,
        audit=CatalogAudit(
            sheet_name=sheet_name,
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            source_rows=len(source_rows),
            unknown_columns=unknown,
            duplicate_link_groups=sum(len(items) > 1 for items in grouped.values()),
            group_headers=len(group_names),
            effective_groups=sum(bool(items) for items in members_by_group.values()),
            invalid_rows=sum(
                1
                for warning in warnings
                if str(warning.get("code", "")).startswith("invalid_")
            ),
            warnings=warnings,
        ),
    )


__all__ = [
    "CatalogAudit",
    "CourseDraft",
    "CourseRouteCatalogDraft",
    "RouteDraft",
    "normalize_course_url",
    "parse_course_route_catalog",
]
