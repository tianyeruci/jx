from __future__ import annotations

import re
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "课程"
    sheet.append(
        [
            "课程名称",
            "课程链接",
            "阶段",
            "学习路线",
            "系列名称",
            "课程序号",
            "课程描述",
            "未知列",
        ]
    )
    sheet.append(
        [
            "定位入门",
            "https://example.com/course/1?utm_source=test",
            "新手必看",
            "创业路线",
            "定位系列",
            1,
            "从客户问题开始定位。",
            "只审计",
        ]
    )
    sheet.append(
        [
            "定位入门（重复行）",
            "https://example.com/course/1",
            "新手必看",
            "创业路线",
            "定位系列",
            1,
            "",
            "只审计",
        ]
    )
    sheet.append(
        [
            "产品验证",
            "https://example.com/course/2",
            "入门阶段",
            "创业路线",
            "定位系列",
            2,
            "",
            "",
        ]
    )
    workbook.save(path)


def test_course_route_catalog_parses_unique_links_and_never_exposes_urls(tmp_path):
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = tmp_path / "catalog.xlsx"
    _workbook(path)
    catalog = parse_course_route_catalog(path, existing_course_ids_by_url={})

    assert len(catalog.courses) == 2
    assert len(catalog.routes) == 1
    assert catalog.courses[0].course_id.startswith("course_")
    assert catalog.courses[0].normalized_url == "https://example.com/course/1"
    assert catalog.courses[0].review_status == "pending_review"
    assert catalog.routes[0].review_status == "pending_review"
    assert "未知列" in catalog.audit.unknown_columns
    assert catalog.audit.duplicate_link_groups == 1
    assert any(
        item["code"] == "duplicate_link_field_conflict"
        for item in catalog.audit.warnings
    )

    for item in [*catalog.courses, *catalog.routes]:
        assert "https://" not in item.model_text
        assert "utm_source" not in item.model_text


def test_course_route_catalog_reuses_existing_id_by_normalized_link(tmp_path):
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = tmp_path / "catalog.xlsx"
    _workbook(path)
    catalog = parse_course_route_catalog(
        path,
        existing_course_ids_by_url={
            "https://example.com/course/1": "course_existing",
        },
    )

    assert catalog.courses[0].course_id == "course_existing"


def test_course_can_belong_to_multiple_routes_and_source_order_is_preserved(
    tmp_path,
):
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = tmp_path / "multi-route.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "课程名称",
            "课程链接",
            "阶段",
            "学习路线",
            "路线序号",
            "阶段序号",
            "课程序号",
        ]
    )
    sheet.append(
        [
            "共同基础课",
            "https://example.com/common",
            "第二阶段",
            "路线B",
            2,
            2,
            2,
        ]
    )
    sheet.append(
        [
            "共同基础课",
            "https://example.com/common",
            "第一阶段",
            "路线A",
            1,
            1,
            2,
        ]
    )
    sheet.append(
        [
            "先修课",
            "https://example.com/first",
            "第一阶段",
            "路线A",
            1,
            1,
            1,
        ]
    )
    workbook.save(path)

    catalog = parse_course_route_catalog(path, existing_course_ids_by_url={})

    assert len(catalog.courses) == 2
    assert [item.name for item in catalog.routes] == ["路线A", "路线B"]
    route_a = catalog.routes[0]
    assert route_a.stages[0]["name"] == "第一阶段"
    assert route_a.stages[0]["course_ids"] == [
        next(item.course_id for item in catalog.courses if item.name == "先修课"),
        next(item.course_id for item in catalog.courses if item.name == "共同基础课"),
    ]
    common = next(item for item in catalog.courses if item.name == "共同基础课")
    assert common.route_name == "路线A；路线B"


def test_course_route_catalog_rejects_multiple_nonempty_sheets(tmp_path):
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = tmp_path / "catalog.xlsx"
    _workbook(path)
    workbook = __import__("openpyxl").load_workbook(path)
    extra = workbook.create_sheet("第二张")
    extra.append(["课程名称"])
    extra.append(["不允许"])
    workbook.save(path)

    try:
        parse_course_route_catalog(path, existing_course_ids_by_url={})
    except ValueError as exc:
        assert "一个非空工作表" in str(exc)
    else:
        raise AssertionError("multiple non-empty sheets must fail")


def test_course_route_strategy_sends_each_course_and_route_to_model(
    tmp_path,
    service_db,
):
    from ai_management.cleaning import STRATEGY_REGISTRY

    path = tmp_path / "catalog.xlsx"
    _workbook(path)
    documents = STRATEGY_REGISTRY["course_route_catalog"].clean(
        path,
        "administrative-name.xlsx",
    )

    assert len(documents) == 3
    assert all(item.disposition == "review_required" for item in documents)
    assert any(
        item.error_code == "CATALOG_STRUCTURE_REVIEW_REQUIRED"
        for item in documents
    )
    assert all((item.audit or {}).get("model_blocks") for item in documents)
    assert all(
        "https://" not in str((item.audit or {}).get("model_blocks"))
        for item in documents
    )


def test_course_route_catalog_resets_stale_xlsx_dimension_and_preserves_collection_order(tmp_path):
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = tmp_path / "stale-dimension.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["课程名称", "课程链接", "阶段", "学习路线"])
    sheet.append(["课程合集", "", "入门阶段", "路线A"])
    sheet.append(["2/第二门", "https://example.com/2", "入门阶段", "路线A"])
    sheet.append(["1/第一门", "https://example.com/1", "入门阶段", "路线A"])
    sheet.append(["无编号课程", "https://example.com/3", "入门阶段", "路线A"])
    sheet.append(["第二组", "", "进阶阶段", "路线A"])
    sheet.append(["1/重启第一门", "https://example.com/4", "进阶阶段", "路线A"])
    workbook.save(path)

    rewritten = tmp_path / "stale-dimension-rewritten.xlsx"
    with ZipFile(path) as source, ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                xml = content.decode("utf-8")
                xml = re.sub(
                    r'<dimension ref="[^"]+"',
                    '<dimension ref="A1"',
                    xml,
                    count=1,
                )
                content = xml.encode("utf-8")
            target.writestr(info, content)

    catalog = parse_course_route_catalog(rewritten, existing_course_ids_by_url={})

    assert len(catalog.courses) == 4
    assert [item.name for item in catalog.courses] == [
        "第一门",
        "第二门",
        "无编号课程",
        "重启第一门",
    ]
    assert catalog.courses[0].series_name == "课程合集"
    assert catalog.courses[3].series_name == "第二组"
    assert catalog.audit.group_headers == 2
    assert catalog.audit.effective_groups == 2


def test_course_route_catalog_reuses_course_id_across_routes_and_flags_name_conflict(tmp_path):
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = tmp_path / "multi-route-conflict.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["课程名称", "课程链接", "阶段", "学习路线"])
    sheet.append(["合集", "", "入门阶段", "路线A"])
    sheet.append(["1/课程甲", "https://example.com/shared", "入门阶段", "路线A"])
    sheet.append(["2/课程乙", "https://example.com/shared", "入门阶段", "路线B"])
    workbook.save(path)

    catalog = parse_course_route_catalog(path, existing_course_ids_by_url={})

    assert len(catalog.courses) == 1
    assert len(catalog.routes) == 2
    assert len(catalog.courses[0].name_candidates) == 2
    assert any(
        conflict["code"] == "duplicate_link_name_conflict"
        for conflict in catalog.courses[0].structure_conflicts
    )
    assert catalog.routes[0].stages[0]["course_ids"] == catalog.routes[1].stages[0]["course_ids"]


def test_course_route_catalog_prefers_specific_title_over_collection_label(tmp_path):
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = tmp_path / "collection-title-conflict.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["课程名称", "课程链接", "阶段", "学习路线"])
    sheet.append(["【合集】1讲（2小时）", None, "入门阶段", "路线A"])
    sheet.append(["【合集】1讲（2小时）", "https://example.com/shared", "入门阶段", "路线A"])
    sheet.append(["具体课程标题", "https://example.com/shared", "入门阶段", "路线A"])
    workbook.save(path)

    catalog = parse_course_route_catalog(path, existing_course_ids_by_url={})

    course = catalog.courses[0]
    assert course.name == "具体课程标题"
    assert course.series_name == "【合集】1讲（2小时）"
    assert not any(
        conflict["code"] == "duplicate_link_name_conflict"
        for conflict in course.structure_conflicts
    )
    assert any(
        warning["code"] == "duplicate_link_name_auto_resolved"
        for warning in catalog.audit.warnings
    )


def test_real_catalog_sample_matches_verified_baseline():
    from ai_management.course_route_catalog import parse_course_route_catalog

    path = Path("文件清洗样本/课程信息与学习路线/课程信息与学习路线.xlsx")
    catalog = parse_course_route_catalog(path, existing_course_ids_by_url={})

    assert len(catalog.courses) == 105
    assert len(catalog.routes) == 5
    assert catalog.audit.source_rows == 137
    assert catalog.audit.group_headers == 29
    assert catalog.audit.effective_groups == 28
    assert catalog.audit.duplicate_link_groups == 2
    assert catalog.audit.invalid_rows == 1
    assert all(not item.name.startswith(tuple(f"{n}/" for n in range(1, 20))) for item in catalog.courses)
    assert not any(
        conflict["code"] == "duplicate_link_name_conflict"
        for item in catalog.courses
        for conflict in item.structure_conflicts
    )
