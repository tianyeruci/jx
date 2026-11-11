from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_session
from ...errors import ServiceError
from ...models import SystemEventLog
from ...security import require_admin_token, require_internal_token
from ...services.observability import event_to_dict, sanitize_details
from ...timeutil import BUSINESS_TIMEZONE

router = APIRouter(
    prefix="/internal/v1/admin",
    tags=["admin-logs"],
    dependencies=[Depends(require_internal_token), Depends(require_admin_token)],
)


@router.get("/logs")
def list_system_logs(
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    level: str | None = None,
    component: str | None = None,
    event_code: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
    resource_id: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if from_time:
        filters.append(SystemEventLog.occurred_at >= from_time)
    if to_time:
        filters.append(SystemEventLog.occurred_at <= to_time)
    if level:
        filters.append(SystemEventLog.level == level.upper())
    if component:
        filters.append(SystemEventLog.component == component)
    if event_code:
        filters.append(SystemEventLog.event_code == event_code)
    if status:
        filters.append(SystemEventLog.status == status)
    if request_id:
        filters.append(SystemEventLog.request_id == request_id)
    if resource_id:
        filters.append(SystemEventLog.resource_id == resource_id)
    total = session.scalar(select(func.count(SystemEventLog.id)).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(SystemEventLog)
            .where(*filters)
            .order_by(SystemEventLog.occurred_at.desc(), SystemEventLog.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    )
    return {
        "items": [event_to_dict(item) for item in rows],
        "page": page,
        "limit": limit,
        "total": total,
    }


@router.get("/logs/{log_id}")
def get_system_log(
    log_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    event = session.get(SystemEventLog, log_id)
    if event is None:
        raise ServiceError("NOT_FOUND", "日志不存在", 404, False)
    return event_to_dict(event, include_details=True)


@router.get("/pdf-conversion-issues")
def list_pdf_conversion_issues(
    kb_code: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    status: str | None = Query(default=None, pattern="^(open|resolved)$"),
    severity: str | None = Query(default=None, pattern="^(warning|blocked)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """List redacted PDF conversion issues stored in the common event log."""

    filters = [
        SystemEventLog.category == "pdf_conversion",
        SystemEventLog.event_code.in_(["PDF_CONVERSION_WARNING", "PDF_CONVERSION_BLOCKED"]),
    ]
    if from_time:
        filters.append(SystemEventLog.occurred_at >= from_time)
    if to_time:
        filters.append(SystemEventLog.occurred_at <= to_time)
    if status:
        filters.append(SystemEventLog.status == status)
    if severity:
        filters.append(SystemEventLog.event_code == f"PDF_CONVERSION_{severity.upper()}")
    candidates = list(
        session.scalars(
            select(SystemEventLog)
            .where(*filters)
            .order_by(SystemEventLog.occurred_at.desc(), SystemEventLog.id.desc())
            .limit(5000)
        )
    )
    if kb_code:
        candidates = [
            item
            for item in candidates
            if str((item.details or {}).get("knowledge_base_code") or "") == kb_code
        ]
    total = len(candidates)
    start = (page - 1) * limit
    return {
        "items": [
            event_to_dict(item, include_details=True) for item in candidates[start : start + limit]
        ],
        "page": page,
        "limit": limit,
        "total": total,
    }


@router.post("/pdf-conversion-issues/{issue_id}/resolve")
def resolve_pdf_conversion_issue(
    issue_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    event = session.get(SystemEventLog, issue_id)
    if event is None or event.category != "pdf_conversion":
        raise ServiceError("NOT_FOUND", "PDF 转换问题不存在", 404, False)
    event.status = "resolved"
    details = dict(event.details or {})
    details["resolution"] = sanitize_details(
        {
            "note": str(payload.get("note") or "")[:200],
            "resolved_at": datetime.now(BUSINESS_TIMEZONE).isoformat(),
        }
    )
    event.details = sanitize_details(details)
    session.commit()
    session.refresh(event)
    return event_to_dict(event, include_details=True)
