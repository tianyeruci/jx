from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..errors import ServiceError
from ..models import AdminSetting

TOOL_KINDS = {"moments", "writing"}
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 500
MAX_REQUEST_LENGTH = 12000
MAX_ID_LENGTH = 100

DEFAULT_EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "moments": [
        {
            "id": "moments-life",
            "title": "介绍生活",
            "description": "把一段具体生活经历写成自然、有真实感的朋友圈内容。",
            "request": "请围绕主题“最近开始坚持晨间散步”写一条朋友圈：领域是个人生活方式与日常记录，受众是熟悉我的朋友和同事，目标是分享真实变化与一点可共鸣的感受。请基于这个主题组织具体场景、细节和收束句，语气自然克制，不夸张、不鸡汤、不编造未提供的经历，控制在 120 字以内。",
            "enabled": True,
            "sort_order": 0,
        },
        {
            "id": "moments-product",
            "title": "介绍产品",
            "description": "用不强推的方式介绍一个产品，让读者理解它适合谁。",
            "request": "请围绕产品“一个帮助小团队整理客户反馈的笔记工具”写一条朋友圈：领域是效率工具介绍，受众是正在管理客户反馈的创业者和产品经理，目标是说明我为什么使用、它解决了什么问题以及适合什么人。请只使用已知信息，不虚构功能、数据或用户评价，避免硬广和夸张承诺，语气像真实体验分享，控制在 160 字以内。",
            "enabled": True,
            "sort_order": 1,
        },
        {
            "id": "moments-thought",
            "title": "分享思考",
            "description": "围绕一个工作或生活观察，写出清晰而不说教的思考。",
            "request": "请围绕主题“很多计划不是输在执行，而是没有先定义完成”写一条朋友圈：领域是工作方法与个人思考，受众是希望提升工作效率的朋友和同事，目标是分享一个清晰、有启发但不说教的观点。请用一个具体观察引出结论，保留适度个人口吻，不引用无法核实的数据，不堆砌术语，控制在 180 字以内。",
            "enabled": True,
            "sort_order": 2,
        },
    ],
    "writing": [
        {
            "id": "writing-summary",
            "title": "工作总结",
            "description": "把零散工作进展整理为面向团队的结构化总结。",
            "request": "请写一份本周工作总结：领域是产品项目协作，受众是直属主管和项目成员，目标是让读者快速了解已完成事项、关键结果、当前风险和下周计划。已知事项包括：完成需求访谈 6 次，整理出 3 类高频问题；推进方案评审但还有一个数据口径未确认；下周计划是完成原型修改并安排第二轮评审。请按“本周完成—问题与风险—下周计划”组织，表达客观具体，不夸大成果，不补写未知数据。",
            "enabled": True,
            "sort_order": 0,
        },
        {
            "id": "writing-notice-email",
            "title": "通知邮件",
            "description": "写一封信息完整、行动要求明确的工作通知邮件。",
            "request": "请写一封项目评审会议通知邮件：领域是团队项目协作，受众是需要参加评审的产品、设计和研发同事，目标是明确会议时间、议题、参会准备和反馈方式。已知信息：会议时间为本周四 15:00，形式为线上会议，议题是确认首页改版方案，参会人需提前阅读方案并准备一个风险问题。会议链接暂不提供，请保留“会议链接待补充”占位。语气专业简洁，包含邮件主题、正文和明确的行动项，不虚构未提供的信息。",
            "enabled": True,
            "sort_order": 1,
        },
        {
            "id": "writing-polish",
            "title": "改写润色",
            "description": "在不改变原意的前提下，让一段文字更清楚、自然、适合目标读者。",
            "request": "请改写并润色下面这段工作说明：领域是内部产品协作，受众是非技术岗位的业务同事，目标是在不改变事实和行动要求的前提下，让表达更清楚、友好、易执行。原文：“这个需求目前技术那边还没排期，大家先别一直问什么时候能做，等确定了再说。”请保留“尚未排期”和“确定后同步”的事实，避免责备语气，输出一版可直接发到工作群的文字，控制在 100 字以内。",
            "enabled": True,
            "sort_order": 2,
        },
    ],
}


def validate_kind(kind: str) -> str:
    if kind not in TOOL_KINDS:
        raise ServiceError("VALIDATION_ERROR", "未知的工具类型", 404, False)
    return kind


def _key(kind: str) -> str:
    return f"tool_examples:{validate_kind(kind)}"


def _copy_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return deepcopy(items)


def _sort(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: int(item["sort_order"]))


def _get_setting(session: Session, kind: str) -> AdminSetting | None:
    key = _key(kind)
    for attempt in range(3):
        try:
            return session.get(AdminSetting, key)
        except OperationalError as exc:
            session.rollback()
            if "locked" not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
    return None


def _stored_items(session: Session, kind: str) -> tuple[list[dict[str, Any]], bool]:
    row = _get_setting(session, kind)
    if row is None:
        return _copy_items(DEFAULT_EXAMPLES[kind]), False
    value = row.value if isinstance(row.value, dict) else {}
    items = value.get("items")
    return (_copy_items(items) if isinstance(items, list) else []), True


def list_examples(session: Session, kind: str, *, enabled_only: bool) -> list[dict[str, Any]]:
    validate_kind(kind)
    items, _ = _stored_items(session, kind)
    if enabled_only:
        items = [item for item in items if item.get("enabled") is True]
    return _sort(items)


def _validate_item(item: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    required = {"id", "title", "description", "request", "enabled", "sort_order"}
    if not partial and set(item) != required:
        missing = required - set(item)
        extra = set(item) - required
        if missing or extra:
            raise ServiceError("VALIDATION_ERROR", "案例字段不完整或包含未知字段", 422, False)
    result = dict(item)
    if "id" in result:
        value = result["id"]
        if (
            not isinstance(value, str)
            or not value
            or len(value) > MAX_ID_LENGTH
            or not value.replace("-", "").replace("_", "").replace(".", "").isalnum()
            or not value[0].isalnum()
        ):
            raise ServiceError("VALIDATION_ERROR", "案例 ID 不合法", 422, False)
    for field, limit in (
        ("title", MAX_TITLE_LENGTH),
        ("description", MAX_DESCRIPTION_LENGTH),
        ("request", MAX_REQUEST_LENGTH),
    ):
        if field in result:
            value = result[field]
            if (
                not isinstance(value, str)
                or (field != "description" and not value.strip())
                or len(value) > limit
            ):
                raise ServiceError("VALIDATION_ERROR", f"{field} 字段不合法", 422, False)
            result[field] = value.strip() if field != "request" else value.strip()
    if "enabled" in result and not isinstance(result["enabled"], bool):
        raise ServiceError("VALIDATION_ERROR", "enabled 字段不合法", 422, False)
    if "sort_order" in result and (
        not isinstance(result["sort_order"], int)
        or isinstance(result["sort_order"], bool)
        or result["sort_order"] < 0
    ):
        raise ServiceError("VALIDATION_ERROR", "排序值必须是非负整数", 422, False)
    return result


def _save_once(session: Session, kind: str, items: list[dict[str, Any]]) -> None:
    key = _key(kind)
    payload = {"items": _copy_items(items)}
    row = _get_setting(session, kind)
    if row is None:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            session.execute(
                insert(AdminSetting)
                .values(key=key, value=payload)
                .on_conflict_do_nothing(index_elements=[AdminSetting.key])
            )
        elif dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert

            session.execute(
                insert(AdminSetting)
                .values(key=key, value=payload)
                .on_conflict_do_nothing(index_elements=[AdminSetting.key])
            )
        else:
            session.add(AdminSetting(key=key, value=payload))
        session.flush()
        row = _get_setting(session, kind)
    if row is None:
        raise RuntimeError("无法创建案例目录配置")
    row.value = payload
    try:
        session.commit()
    except IntegrityError:
        # Fallback for an unsupported dialect whose first INSERT raced.
        session.rollback()
        row = _get_setting(session, kind)
        if row is None:
            raise
        row.value = payload
        session.commit()


def _save(session: Session, kind: str, items: list[dict[str, Any]]) -> None:
    for attempt in range(3):
        try:
            _save_once(session, kind, items)
            return
        except OperationalError as exc:
            session.rollback()
            if "locked" not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))


def create_example(session: Session, kind: str, item: dict[str, Any]) -> dict[str, Any]:
    validate_kind(kind)
    normalized = _validate_item(item)
    items, _ = _stored_items(session, kind)
    if any(existing.get("id") == normalized["id"] for existing in items):
        raise ServiceError("VALIDATION_ERROR", "案例 ID 已存在", 422, False)
    items.append(normalized)
    _save(session, kind, items)
    return normalized


def update_example(
    session: Session, kind: str, example_id: str, patch: dict[str, Any]
) -> dict[str, Any]:
    validate_kind(kind)
    if not example_id:
        raise ServiceError("VALIDATION_ERROR", "案例 ID 不能为空", 422, False)
    if not patch:
        raise ServiceError("VALIDATION_ERROR", "至少提交一个要修改的字段", 422, False)
    items, _ = _stored_items(session, kind)
    for index, item in enumerate(items):
        if item.get("id") == example_id:
            if "id" in patch and patch["id"] != example_id:
                raise ServiceError("VALIDATION_ERROR", "不允许修改案例 ID", 422, False)
            updated = _validate_item({**item, **patch})
            items[index] = updated
            _save(session, kind, items)
            return updated
    raise ServiceError("NOT_FOUND", "案例不存在", 404, False)


def delete_example(session: Session, kind: str, example_id: str) -> None:
    validate_kind(kind)
    items, _ = _stored_items(session, kind)
    remaining = [item for item in items if item.get("id") != example_id]
    if len(remaining) == len(items):
        raise ServiceError("NOT_FOUND", "案例不存在", 404, False)
    _save(session, kind, remaining)


def restore_defaults(session: Session, kind: str) -> list[dict[str, Any]]:
    validate_kind(kind)
    items, _ = _stored_items(session, kind)
    ids = {str(item.get("id")) for item in items}
    items.extend(_copy_items([item for item in DEFAULT_EXAMPLES[kind] if item["id"] not in ids]))
    _save(session, kind, items)
    return _sort(items)
