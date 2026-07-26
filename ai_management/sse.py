from __future__ import annotations

import json
from typing import Any


def event(name: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")

