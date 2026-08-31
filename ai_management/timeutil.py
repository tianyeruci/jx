from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def now_beijing() -> datetime:
    """Return the single application wall-clock with an explicit Beijing TZ."""
    return datetime.now(BUSINESS_TIMEZONE)


def business_today() -> date:
    return datetime.now(BUSINESS_TIMEZONE).date()


def normalize_beijing(value: datetime) -> datetime:
    """Normalize application timestamps to the Beijing business timezone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=BUSINESS_TIMEZONE)
    return value.astimezone(BUSINESS_TIMEZONE)
