"""Small Dify Chatflow API client with incremental SSE decoding."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

import httpx


class DifyAPIError(RuntimeError):
    """Raised when Dify rejects or interrupts an API request."""


def input_fields(parameters: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten Dify's user_input_form into a variable-to-config mapping."""
    fields: dict[str, dict[str, Any]] = {}
    for entry in parameters.get("user_input_form", []):
        if not isinstance(entry, dict):
            continue
        for config in entry.values():
            if isinstance(config, dict) and config.get("variable"):
                fields[str(config["variable"])] = config
    return fields


def normalize_and_validate_inputs(
    raw_inputs: dict[str, Any], fields: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """Trim accidental key whitespace and reject missing published inputs."""
    normalized: dict[str, Any] = {}
    renamed: list[str] = []
    for raw_key, value in raw_inputs.items():
        key = raw_key.strip() if isinstance(raw_key, str) else raw_key
        if key in normalized and key != raw_key:
            raise ValueError(f"字段名清理空格后发生重复：{key}")
        normalized[key] = value
        if key != raw_key:
            renamed.append(f"{raw_key!r} → {key!r}")

    missing = [
        name
        for name, config in fields.items()
        if config.get("required") and (name not in normalized or normalized[name] in (None, ""))
    ]
    if missing:
        raise ValueError(f"缺少 Chatflow 必填 inputs：{', '.join(missing)}")
    return normalized, renamed


def explain_dify_error(error: str) -> str:
    """Turn common Dify API diagnostics into actionable test-page text."""
    normalized = error.lower()
    if "workflow not published" in normalized:
        return (
            "这个 Chatflow 还没有发布，Dify 因此拒绝 API 调用。"
            "请登录 Dify 控制台，打开当前“测试”应用并点击“发布/更新”，然后重新发送。"
        )
    if "unauthorized" in normalized or "invalid api key" in normalized:
        return "Dify API Key 无效或已被撤销，请更新本地 .env 后重启测试页。"
    if "not found" in normalized and "conversation" in normalized:
        return "该历史对话不存在，或不属于当前测试用户。请新建对话后重试。"
    return error


class SSEDecoder:
    """Incrementally decode SSE events from arbitrarily split byte chunks."""

    def __init__(self) -> None:
        self._buffer = ""
        self._data_lines: list[str] = []

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer += chunk.decode("utf-8", errors="replace")
        events: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line == "":
                if self._data_lines:
                    events.append("\n".join(self._data_lines))
                    self._data_lines.clear()
            elif line.startswith("data:"):
                self._data_lines.append(line[5:].lstrip())
        return events

    def finish(self) -> list[str]:
        if self._buffer:
            line = self._buffer.rstrip("\r")
            if line.startswith("data:"):
                self._data_lines.append(line[5:].lstrip())
            self._buffer = ""
        if not self._data_lines:
            return []
        event = "\n".join(self._data_lines)
        self._data_lines.clear()
        return [event]


def decode_sse_json(chunks: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    decoder = SSEDecoder()
    for chunk in chunks:
        for raw_event in decoder.feed(chunk):
            if raw_event == "[DONE]":
                return
            try:
                yield json.loads(raw_event)
            except json.JSONDecodeError:
                continue
    for raw_event in decoder.finish():
        if raw_event != "[DONE]":
            try:
                yield json.loads(raw_event)
            except json.JSONDecodeError:
                pass


class DifyClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.timeout = httpx.Timeout(timeout, connect=10.0)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        # Streaming responses are not buffered by httpx. Read only the error
        # body so the caller gets Dify's actual diagnostic message.
        if not response.is_closed:
            response.read()
        try:
            payload = response.json()
            detail = payload.get("message") or payload.get("code") or response.text
        except (ValueError, AttributeError):
            detail = response.text
        raise DifyAPIError(f"Dify 请求失败（HTTP {response.status_code}）：{detail}")

    def get_app_info(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/info", headers=self.headers)
            self._raise_for_status(response)
            return response.json()

    def get_parameters(self) -> dict[str, Any]:
        """Return the published app input schema used for local validation."""
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/parameters", headers=self.headers)
            self._raise_for_status(response)
            return response.json()

    def list_conversations(self, user: str, limit: int = 20) -> list[dict[str, Any]]:
        params = {"user": user, "limit": limit, "sort_by": "-updated_at"}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/conversations", headers=self.headers, params=params)
            self._raise_for_status(response)
            return response.json().get("data", [])

    def get_messages(self, conversation_id: str, user: str, limit: int = 100) -> list[dict[str, Any]]:
        params = {"conversation_id": conversation_id, "user": user, "limit": limit}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/messages", headers=self.headers, params=params)
            self._raise_for_status(response)
            return response.json().get("data", [])

    def stream_chat(
        self,
        *,
        query: str,
        user: str,
        conversation_id: str,
        inputs: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        payload = {
            "inputs": inputs,
            "query": query,
            "response_mode": "streaming",
            "conversation_id": conversation_id,
            "user": user,
            "auto_generate_name": True,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat-messages",
                    headers={**self.headers, "Accept": "text/event-stream"},
                    json=payload,
                ) as response:
                    self._raise_for_status(response)
                    yield from decode_sse_json(response.iter_bytes())
        except httpx.TimeoutException as exc:
            raise DifyAPIError("Dify 响应超时，请检查工作流或模型服务。") from exc
        except httpx.RequestError as exc:
            raise DifyAPIError(f"无法连接 Dify：{exc}") from exc
