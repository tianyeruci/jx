from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .events import safe_exception_traceback


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "conversation_id",
            "task_id",
            "file_id",
            "batch_id",
            "event_code",
            "component",
            "duration_ms",
        ):
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = safe_exception_traceback(record.exc_info[1], limit=8000)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
