from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db import get_session
from ...config import get_settings
from ...errors import ServiceError
from ...schemas import RetentionSettingsRequest, StyleSettingUpdateRequest
from ...security import require_admin_token, require_internal_token
from ...services.admin_settings import get_history_retention_days, set_history_retention_days
from ...services.observability import record_event
from ...services.style_settings import (
    STYLE_DEFAULTS,
    list_style_settings,
    style_prompt_fingerprint,
    update_style_setting,
)

router = APIRouter(
    prefix="/internal/v1/admin",
    tags=["admin-management"],
    dependencies=[Depends(require_internal_token), Depends(require_admin_token)],
)


@router.get("/settings/retention")
def get_retention_settings(session: Session = Depends(get_session)) -> dict[str, int]:
    settings = get_settings()
    return {
        "history_retention_days": get_history_retention_days(session),
        "cleanup_hour": settings.cleanup_hour,
        "cleanup_minute": settings.cleanup_minute,
    }


@router.put("/settings/retention")
def put_retention_settings(
    payload: RetentionSettingsRequest,
    session: Session = Depends(get_session),
) -> dict[str, int]:
    value = set_history_retention_days(session, payload.history_retention_days)
    record_event(
        level="INFO",
        category="operation",
        component="admin_management_api",
        event_code="HISTORY_RETENTION_UPDATED",
        status="succeeded",
        message="历史保留天数已更新",
        resource_type="admin_setting",
        resource_id="history_retention",
        details={"history_retention_days": value},
    )
    settings = get_settings()
    return {
        "history_retention_days": value,
        "cleanup_hour": settings.cleanup_hour,
        "cleanup_minute": settings.cleanup_minute,
    }


@router.get("/style-settings")
def get_style_settings(session: Session = Depends(get_session)) -> dict[str, list[dict[str, object]]]:
    return {
        "items": [
            {
                "code": row.code,
                "label": STYLE_DEFAULTS[row.code]["label"],
                "enabled": row.enabled,
                "style_prompt": row.style_prompt,
                "version": row.version,
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
            }
            for row in list_style_settings(session)
        ]
    }


@router.put("/style-settings/{style_code}")
def put_style_setting(
    style_code: str,
    payload: StyleSettingUpdateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if style_code not in STYLE_DEFAULTS:
        raise ServiceError("VALIDATION_ERROR", "未知的风格配置编码", 422, False)
    try:
        row = update_style_setting(
            session,
            code=style_code,
            enabled=payload.enabled,
            style_prompt=payload.style_prompt,
        )
    except ValueError as exc:
        raise ServiceError("VALIDATION_ERROR", str(exc), 422, False) from exc
    record_event(
        level="INFO",
        category="operation",
        component="admin_management_api",
        event_code="STYLE_SETTING_UPDATED",
        status="succeeded",
        message="输出风格配置已更新",
        resource_type="style_setting",
        resource_id=style_code,
        details={
            "enabled": row.enabled,
            "version": row.version,
            "length": len(row.style_prompt),
            "sha256": style_prompt_fingerprint(row.style_prompt),
        },
    )
    return {
        "code": row.code,
        "label": STYLE_DEFAULTS[row.code]["label"],
        "enabled": row.enabled,
        "style_prompt": row.style_prompt,
        "version": row.version,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }
