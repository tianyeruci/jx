from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import SessionLocal
from ...models import SystemEventLog

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s,;)]{2,}")
_LONG_ERROR_RE = re.compile(r"\s{2,}")

_REDACTED_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "api_key",
    "authorization",
    "content",
    "answer",
    "query",
    "source_text",
    "source_material",
    "filename",
    "body",
)


def sanitize_details(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:50]:
            key = str(raw_key)[:100]
            if any(part in key.lower() for part in _REDACTED_KEY_PARTS):
                result[key] = "[redacted]"
            else:
                result[key] = sanitize_details(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [sanitize_details(item, depth=depth + 1) for item in list(value)[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]


def safe_exception_message(
    exc: BaseException,
    *,
    sensitive_values: tuple[str, ...] = (),
    limit: int = 600,
) -> str:
    """Return a useful, bounded error summary without source payloads."""

    message = str(exc).replace("\x00", " ").strip()
    for value in sensitive_values:
        if value:
            message = message.replace(value, "[redacted]")
    message = _PATH_RE.sub("[path]", message)
    message = _LONG_ERROR_RE.sub(" ", message)
    if "\n" in message:
        message = message.splitlines()[0].strip()
    if len(message) > limit:
        return message[:limit] + "…"
    return message


def exception_details(
    exc: BaseException,
    *,
    phase: str,
    sensitive_values: tuple[str, ...] = (),
    **context: Any,
) -> dict[str, Any]:
    """Build redacted diagnostic details for error events."""

    return {
        **context,
        "phase": phase,
        "exception_type": type(exc).__name__,
        "exception_message": safe_exception_message(
            exc,
            sensitive_values=sensitive_values,
        ),
    }


def record_event(
    *,
    level: str,
    category: str,
    component: str,
    event_code: str,
    status: str,
    message: str,
    request_id: str = "",
    actor_hash: str = "",
    resource_type: str = "",
    resource_id: str = "",
    details: Mapping[str, Any] | None = None,
    duration_ms: int | None = None,
    session: Session | None = None,
) -> SystemEventLog | None:
    event = SystemEventLog(
        level=level.upper()[:16],
        category=category[:32],
        component=component[:64],
        event_code=event_code[:100],
        status=status[:32],
        request_id=request_id[:64],
        actor_hash=actor_hash[:64],
        resource_type=resource_type[:64],
        resource_id=resource_id[:100],
        message=message[:1000],
        details=sanitize_details(details or {}),
        duration_ms=duration_ms,
        build_version=get_settings().build_version[:100],
    )
    if session is not None:
        session.add(event)
        return event
    try:
        with SessionLocal() as owned_session:
            owned_session.add(event)
            owned_session.commit()
            owned_session.refresh(event)
            return event
    except Exception:
        logger.exception("failed to persist system event", extra={"event_code": event_code})
        return None


def event_to_dict(event: SystemEventLog, *, include_details: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "log_id": event.id,
        "occurred_at": event.occurred_at,
        "level": event.level,
        "category": event.category,
        "component": event.component,
        "event_code": event.event_code,
        "status": event.status,
        "request_id": event.request_id,
        "actor_hash": event.actor_hash,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "message": event.message,
        "duration_ms": event.duration_ms,
        "build_version": event.build_version,
    }
    if include_details:
        result["details"] = event.details or {}
    return result
