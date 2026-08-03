from __future__ import annotations

import hashlib
import json
import re
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from .course_import import _course_text, _route_text
from .models import CleaningDocument, CleaningFile, CourseDirection
from .sanitization import sanitize_for_knowledge
from .schemas import CourseRecord, RouteRecord

STRUCTURE_REVIEW_HEADERS = [
    "item_id",
    "expected_revision",
    "记录类型",
    "最终名称",
    "名称候选(JSON)",
    "最终合集",
    "合集候选(JSON)",
    "源行号(JSON)",
    "结构冲突(JSON)",
    "处理",
]

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


def _structure_values(document: CleaningDocument) -> list[Any]:
    audit = document.audit_json or {}
    draft = audit.get("catalog_draft") or {}
    conflicts = audit.get("structure_conflicts") or []
    candidates = draft.get("name_candidates") or [draft.get("name") or document.title]
    series_candidates = draft.get("series_candidates") or []
    final_name = "" if any(
        str(item.get("code")) == "duplicate_link_name_conflict"
        for item in conflicts
        if isinstance(item, dict)
    ) else (draft.get("name") or document.title)
    final_series = "" if any(
        str(item.get("code")) == "duplicate_link_series_conflict"
        for item in conflicts
        if isinstance(item, dict)
    ) else (draft.get("series_name") or "")
    return [
        document.id,
        document.review_revision,
        "课程" if audit.get("catalog_kind") == "course" else "学习路线",
        final_name,
        json.dumps(candidates, ensure_ascii=False),
        final_series,
        json.dumps(series_candidates, ensure_ascii=False),
        json.dumps(audit.get("source_rows") or [], ensure_ascii=False),
        json.dumps(conflicts, ensure_ascii=False),
        "确认",
    ]


def _structure_phase(file_row: CleaningFile, documents: list[CleaningDocument]) -> bool:
    return file_row.stage == "catalog_structure_review" or any(
        item.stage == "catalog_structure_review"
        or item.error_code in {
            "CATALOG_STRUCTURE_REVIEW_REQUIRED",
            "CATALOG_STRUCTURE_REVIEW_BLOCKED",
        }
        for item in documents
    )


def _read_review_rows(content: bytes) -> list[list[Any]]:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) != 1:
            raise ValueError("审核工作簿只能包含一个工作表")
        sheet = workbook.active
        sheet.reset_dimensions()
        return [
            list(row)
            for row in sheet.iter_rows(values_only=True)
            if any(value not in (None, "") for value in row)
        ]
    finally:
        workbook.close()


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
    structure_phase = _structure_phase(file_row, documents)
    headers = STRUCTURE_REVIEW_HEADERS if structure_phase else REVIEW_HEADERS
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "审核"
    sheet.append(headers)
    for document in documents:
        sheet.append(
            _structure_values(document) if structure_phase else _review_values(document)
        )
    sheet.freeze_panes = "A2"
    last_column = "J" if structure_phase else "K"
    sheet.auto_filter.ref = f"A1:{last_column}{max(2, len(documents) + 1)}"
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


def _import_semantic_review(
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
    rows = _read_review_rows(content)
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
        draft_before = (document.audit_json or {}).get("catalog_draft") or {}
        if str(values[3] or "").strip() != str(
            draft_before.get("name") or document.title
        ).strip():
            raise ValueError("课程名称必须在结构审核阶段确定，不能在语义审核阶段修改")
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
        catalog_record = record.model_dump(mode="json")
        # Collection provenance is an internal contract and intentionally is
        # not part of the public CourseRecord schema. Preserve it alongside
        # the validated record so the atomic publisher can upsert the
        # CourseCollection/Member rows in the same transaction.
        if kind == "course":
            for key in (
                "collection_id",
                "collection_name",
                "collection_order",
                "collection_entry",
                "collection_declared_lessons",
                "collection_total_minutes",
                "collection_raw_title",
                "collection_entry_course_id",
                "collection_warnings",
            ):
                if key in draft:
                    catalog_record[key] = draft[key]
        audit["catalog_record"] = catalog_record
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


def _import_structure_review(
    session,
    *,
    batch_id: str,
    file_id: str,
    content: bytes,
) -> dict[str, int | str | None]:
    file_row = session.scalar(
        select(CleaningFile).where(
            CleaningFile.id == file_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    if file_row is None or file_row.batch.strategy_code != "course_route_catalog":
        raise ValueError("课程目录清洗文件不存在")
    rows = _read_review_rows(content)
    if not rows or [str(value or "") for value in rows[0]] != STRUCTURE_REVIEW_HEADERS:
        raise ValueError("结构审核工作簿表头或版本不匹配")
    documents = {
        item.id: item
        for item in session.scalars(
            select(CleaningDocument).where(CleaningDocument.file_id == file_id)
        )
    }
    seen: set[str] = set()
    resolved = 0
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
        action = str(values[9] or "").strip().casefold()
        audit = dict(document.audit_json or {})
        draft = dict(audit.get("catalog_draft") or {})
        if action in {"排除", "exclude", "no", "否"}:
            document.status = "excluded"
            document.stage = "reviewed"
            audit["human_structure_review"] = {
                "action": "exclude",
                "source": "structure_review_workbook",
            }
            document.audit_json = audit
            document.review_revision += 1
            excluded += 1
            continue
        if action not in {"确认", "confirm", "yes", "是", ""}:
            raise ValueError("结构审核处理字段只能填确认或排除")
        final_name = str(values[3] or "").strip()
        if not final_name:
            raise ValueError(f"第{row_number}行必须填写最终课程名称")
        if re.match(r"^\s*[1-9]\d*\s*[/／]", final_name):
            raise ValueError("最终课程名称不能包含顺序前缀，请删除数字/前缀")
        candidate_series = draft.get("series_candidates") or []
        final_series = str(values[5] or "").strip()
        if len(candidate_series) > 1 and not final_series:
            raise ValueError(f"第{row_number}行必须填写最终课程合集")
        draft["name"] = final_name
        if final_series:
            draft["series_name"] = final_series
        audit["catalog_draft"] = draft
        audit["resolved_structure_conflicts"] = audit.get("structure_conflicts") or []
        audit["structure_conflicts"] = []
        audit["human_structure_review"] = {
            "action": "confirm",
            "source": "structure_review_workbook",
            "final_name": final_name,
            "final_series_name": final_series,
        }
        blocks = dict(audit.get("model_blocks") or {})
        if "b001" in blocks:
            blocks["b001"] = f"课程名称：{final_name}"
        audit["model_blocks"] = blocks
        document.audit_json = audit
        document.title = final_name[:300]
        document.cleaned_markdown = f"# {final_name}\n\n课程资料等待模型补全和审核后发布。"
        document.metadata_json = {
            **(document.metadata_json or {}),
            "series_name": final_series or draft.get("series_name") or "",
        }
        document.status = "awaiting_model"
        document.stage = "awaiting_model"
        document.error_code = None
        document.error_message = None
        document.retryable = False
        document.review_revision += 1
        resolved += 1
    if seen != set(documents):
        raise ValueError("结构审核工作簿必须完整覆盖当前文件的全部条目")
    if resolved:
        file_row.status = "processing"
        file_row.outcome = "processing"
        file_row.stage = "model_planning"
        file_row.batch.status = "processing"
    elif excluded:
        file_row.status = "excluded"
        file_row.outcome = "excluded"
        file_row.stage = "reviewed"
        file_row.batch.status = "completed"
    session.commit()
    task_id: str | None = None
    if resolved:
        from .workers.tasks.model_cleaning import maybe_plan_model_task

        task_id = maybe_plan_model_task(batch_id)
    return {
        "review_phase": "semantic",
        "resolved": resolved,
        "approved": 0,
        "excluded": excluded,
        "model_task_id": task_id,
    }


def import_catalog_review(
    session,
    *,
    batch_id: str,
    file_id: str,
    content: bytes,
) -> dict[str, Any]:
    rows = _read_review_rows(content)
    headers = [str(value or "") for value in rows[0]] if rows else []
    if headers == STRUCTURE_REVIEW_HEADERS:
        return _import_structure_review(
            session,
            batch_id=batch_id,
            file_id=file_id,
            content=content,
        )
    if headers == REVIEW_HEADERS:
        return _import_semantic_review(
            session,
            batch_id=batch_id,
            file_id=file_id,
            content=content,
        )
    raise ValueError("审核工作簿表头或版本不匹配")


__all__ = ["export_catalog_review", "import_catalog_review"]
