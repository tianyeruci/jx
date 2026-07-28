from __future__ import annotations

import json
from typing import Any

SCHEMA_VERSION = "2.0"


class SSEEmitter:
    """Create a single, monotonic event stream for one API request."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.sequence = 0

    def __call__(self, name: str, data: Any | None = None) -> bytes:
        self.sequence += 1
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "request_id": self.request_id,
            "sequence": self.sequence,
        }
        if isinstance(data, dict):
            payload.update(data)
        elif data is not None:
            payload["items"] = data
        return event(name, payload)


def event(name: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")
