from __future__ import annotations

from sqlalchemy.orm import Session

from ..cache import cache
from ..config import get_settings
from ..models import AdminSetting

HISTORY_RETENTION_KEY = "history_retention"
HISTORY_RETENTION_CACHE_KEY = "ai:setting:history_retention_days"


def get_history_retention_days(session: Session) -> int:
    cached = cache.get_json(HISTORY_RETENTION_CACHE_KEY)
    if isinstance(cached, int) and 1 <= cached <= 365:
        return cached
    row = session.get(AdminSetting, HISTORY_RETENTION_KEY)
    configured = (row.value or {}).get("history_retention_days") if row else None
    try:
        value = int(configured)
    except (TypeError, ValueError):
        value = get_settings().history_retention_days
    value = max(1, min(365, value))
    cache.set_json(HISTORY_RETENTION_CACHE_KEY, value, ttl=300)
    return value


def set_history_retention_days(session: Session, value: int) -> int:
    value = max(1, min(365, int(value)))
    row = session.get(AdminSetting, HISTORY_RETENTION_KEY)
    if row is None:
        row = AdminSetting(key=HISTORY_RETENTION_KEY, value={})
        session.add(row)
    row.value = {"history_retention_days": value}
    session.commit()
    cache.set_json(HISTORY_RETENTION_CACHE_KEY, value, ttl=300)
    return value
