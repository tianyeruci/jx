from __future__ import annotations

import hashlib
import json
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from .course_import import _course_text, _route_text
from .models import CleaningDocument, CleaningFile, CourseDirection
from .sanitization import sanitize_for_knowledge
from .schemas import CourseRecord, RouteRecord

REVIEW_HEADERS = [
    "item_id",
    "expected_revision",
    "记录类型",
    "名称",
    "批准",
    "规范方向",
    "课程描述或路线目标",
    "适用人群",
    "课程亮点（JSON数组）",
    "学习目标（JSON数组）",
    "路线阶段与课程顺序（JSON）",
]


def _review_values(document: CleaningDocument) -> list[Any]:
    audit = document.audit_json or {}
    draft = audit.get("catalog_draft") or {}
    ai = audit.get("ai_candidate") or {}
    kind = str(audit.get("catalog_kind") or "")
    description = (
        ai.get("description")
        if kind == "course"
        else ai.get("route_target")
    ) or (
        draft.get("description") if kind == "course" else draft.get("target")
    )
    return [
        document.id,
        document.review_revision,
        "课程" if kind == "course" else "学习路线",
        draft.get("name") or document.title,
        "否",
        ai.get("direction_candidate") or draft.get("direction") or "",
        description or "",
        ai.get("audience") or draft.get("audience") or "",
        json.dumps(
            ai.get("highlights") or draft.get("highlights") or [],
            ensure_ascii=False,
        ),
        json.dumps(
            ai.get("learning_outcomes")
            or draft.get("learning_outcomes")
            or [],
            ensure_ascii=False,
        ),
        json.dumps(draft.get("stages") or [], ensure_ascii=False),
    ]


def export_catalog_review(session, *, batch_id: str, file_id: str) -> bytes:
    file_row = session.scalar(
        select(CleaningFile).where(
            CleaningFile.id == file_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    if file_row is None or file_row.batch.strategy_code != "course_route_catalog":
        raise ValueError("课程目录清洗文件不存在")
    documents = list(
        session.scalars(
            select(CleaningDocument)
            .where(CleaningDocument.file_id == file_id)
            .order_by(CleaningDocument.sequence)
        )
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "审核"
    sheet.append(REVIEW_HEADERS)
    for document in documents:
        sheet.append(_review_values(document))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:K{max(2, len(documents) + 1)}"
    sheet.column_dimensions["A"].hidden = True
    sheet.column_dimensions["B"].hidden = True
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _json_list(value: Any, field: str) -> list[str]:
    if value in (None, ""):
        return []
    raw = json.loads(str(value))
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{field}必须是字符串JSON数组")
    return [item.strip() for item in raw if item.strip()]


def _json_stages(value: Any) -> list[dict[str, Any]]:
    raw = json.loads(str(value or "[]"))
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("路线阶段与课程顺序必须是JSON对象数组")
    return raw


def _approved_direction(session, value: str) -> str:
    normalized = value.strip()
    directions = list(
        session.scalars(
            select(CourseDirection).where(
                CourseDirection.enabled.is_(True),
                CourseDirection.review_status == "approved",
            )
        )
    )
    for direction in directions:
        if normalized == direction.name or normalized in (direction.aliases or []):
            return direction.name
    code = "direction_" + hashlib.sha256(
        normalized.casefold().encode("utf-8")
    ).hexdigest()[:12]
    existing = session.get(CourseDirection, code)
    if existing is not None and existing.name != normalized:
        raise ValueError("方向规范名称发生哈希冲突")
    if existing is None:
        session.add(
            CourseDirection(
                code=code,
                name=normalized,
                aliases=[],
                enabled=True,
                review_status="approved",
                approved_by="catalog_review_workbook",
            )
        )
    return normalized


def import_catalog_review(
    session,
    *,
    batch_id: str,
    file_id: str,
    content: bytes,
) -> dict[str, int]:
    file_row = session.scalar(
        select(CleaningFile).where(
            CleaningFile.id == file_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    if file_row is None or file_row.batch.strategy_code != "course_route_catalog":
        raise ValueError("课程目录清洗文件不存在")
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) != 1:
            raise ValueError("审核工作簿只能包含一个工作表")
        rows = list(workbook.active.iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows or [str(value or "") for value in rows[0]] != REVIEW_HEADERS:
        raise ValueError("审核工作簿表头或版本不匹配")
    documents = {
        item.id: item
        for item in session.scalars(
            select(CleaningDocument).where(CleaningDocument.file_id == file_id)
        )
    }
    seen: set[str] = set()
    approved = 0
    excluded = 0
    for row_number, values in enumerate(rows[1:], start=2):
        item_id = str(values[0] or "")
        if not item_id:
            continue
        document = documents.get(item_id)
        if document is None or item_id in seen:
            raise ValueError(f"第{row_number}行item_id无效或重复")
        seen.add(item_id)
        expected_revision = int(values[1] or 0)
        if document.review_revision != expected_revision:
            raise ValueError(f"第{row_number}行条目已变化，请重新导出")
        approve = str(values[4] or "").strip().casefold()
        if approve not in {"是", "yes", "y", "否", "no", "n"}:
            raise ValueError(f"第{row_number}行批准字段只能填是或否")
        if approve in {"否", "no", "n"}:
            document.status = "excluded"
            document.stage = "reviewed"
            document.review_revision += 1
            excluded += 1
            continue
        direction = str(values[5] or "").strip()
        description_or_target = str(values[6] or "").strip()
        if not direction or not description_or_target:
            raise ValueError(f"第{row_number}行批准前必须填写规范方向和描述/目标")
        audit = dict(document.audit_json or {})
        draft = dict(audit.get("catalog_draft") or {})
        kind = str(audit.get("catalog_kind") or "")
        direction = _approved_direction(session, direction)
        if kind == "course":
            draft.update(
                {
                    "direction": direction,
                    "description": description_or_target,
                    "audience": str(values[7] or "").strip(),
                    "highlights": _json_list(values[8], "课程亮点"),
                    "learning_outcomes": _json_list(values[9], "学习目标"),
                    "enabled": True,
                    "review_status": "approved",
                }
            )
            record = CourseRecord.model_validate(draft)
            document.cleaned_markdown = sanitize_for_knowledge(_course_text(record))
            document.metadata_json = {
                **(document.metadata_json or {}),
                "document_type": "course",
                "course_id": record.course_id,
                "direction": record.direction,
                "stage": record.stage,
                "source_platform": record.source_platform,
                "series_name": record.series_name or "",
                "status": "active",
                "review_status": "approved",
            }
        elif kind == "route":
            draft.update(
                {
                    "direction": direction,
                    "target": description_or_target,
                    "stages": _json_stages(values[10]),
                    "enabled": True,
                    "review_status": "approved",
                }
            )
            record = RouteRecord.model_validate(draft)
            document.cleaned_markdown = sanitize_for_knowledge(_route_text(record))
            document.metadata_json = {
                **(document.metadata_json or {}),
                "document_type": "learning_route",
                "route_id": record.route_id,
                "direction": record.direction,
                "status": "active",
                "review_status": "approved",
            }
        else:
            raise ValueError(f"第{row_number}行记录类型无效")
        audit["catalog_record"] = record.model_dump(mode="json")
        audit["human_review"] = {
            "approved": True,
            "source": "review_workbook",
        }
        document.audit_json = audit
        document.status = "pending"
        document.stage = "pending"
        document.error_code = None
        document.error_message = None
        document.retryable = True
        document.review_revision += 1
        approved += 1
    if seen != set(documents):
        raise ValueError("审核工作簿必须完整覆盖当前文件的全部条目")
    if approved:
        # The publication worker must not inherit the stale terminal review
        # state; callers poll the file/batch status immediately after import.
        file_row.status = "processing"
        file_row.outcome = "processing"
        file_row.stage = "publishing"
        file_row.batch.status = "processing"
    elif excluded:
        file_row.status = "excluded"
        file_row.outcome = "excluded"
        file_row.stage = "reviewed"
        file_row.batch.status = "completed"
    session.commit()
    return {"approved": approved, "excluded": excluded}


__all__ = ["export_catalog_review", "import_catalog_review"]
