from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_session
from ...errors import ServiceError
from ...models import SystemEventLog
from ...security import require_admin_token, require_internal_token
from ...services.observability import event_to_dict

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
