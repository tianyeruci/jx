from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from dify_client import DifyAPIError, decode_sse_json

from .errors import ServiceError, dify_error


class DifyAppClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ServiceError("DIFY_UNAVAILABLE", "对应的 Dify 应用 API Key 未配置", 503, False)
        return {"Authorization": f"Bearer {self.api_key}"}

    def _raise(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        if not response.is_closed:
            response.read()
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("message") or payload.get("code") or response.text)
        except Exception:
            detail = response.text
        if response.status_code == 429:
            raise dify_error(detail, rate_limited=True)
        raise ServiceError(
            "DIFY_UNAVAILABLE", "Dify 应用调用失败", 502, response.status_code >= 500
        )

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
            "auto_generate_name": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat-messages",
                    headers={**self.headers, "Accept": "text/event-stream"},
                    json=payload,
                ) as response:
                    self._raise(response)
                    yield from decode_sse_json(response.iter_bytes())
        except ServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise dify_error(str(exc), timeout=True) from exc
        except (httpx.RequestError, DifyAPIError) as exc:
            raise dify_error(str(exc)) from exc

    def stream_workflow(self, *, inputs: dict[str, Any], user: str) -> Iterator[dict[str, Any]]:
        payload = {"inputs": inputs, "response_mode": "streaming", "user": user}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/workflows/run",
                    headers={**self.headers, "Accept": "text/event-stream"},
                    json=payload,
                ) as response:
                    self._raise(response)
                    yield from decode_sse_json(response.iter_bytes())
        except ServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise dify_error(str(exc), timeout=True) from exc
        except (httpx.RequestError, DifyAPIError) as exc:
            raise dify_error(str(exc)) from exc

    def run_workflow(self, *, inputs: dict[str, Any], user: str) -> dict[str, Any]:
        payload = {"inputs": inputs, "response_mode": "blocking", "user": user}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/workflows/run", headers=self.headers, json=payload
                )
                self._raise(response)
                return response.json()
        except ServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise dify_error(str(exc), timeout=True) from exc
        except httpx.RequestError as exc:
            raise dify_error(str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise ServiceError(
                "DIFY_RESPONSE_INVALID",
                "Dify返回了无法解析的响应",
                502,
                True,
            ) from exc

    def stop_chat(self, task_id: str, user: str) -> dict[str, Any]:
        return self._post(f"/chat-messages/{task_id}/stop", {"user": user})

    def stop_workflow(self, task_id: str, user: str) -> dict[str, Any]:
        return self._post(f"/workflows/tasks/{task_id}/stop", {"user": user})

    def feedback(
        self, message_id: str, user: str, rating: str | None, content: str
    ) -> dict[str, Any]:
        return self._post(
            f"/messages/{message_id}/feedbacks",
            {"user": user, "rating": rating, "content": content},
        )

    def delete_conversation(self, conversation_id: str, user: str) -> None:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    "DELETE",
                    f"{self.base_url}/conversations/{conversation_id}",
                    headers=self.headers,
                    json={"user": user},
                )
                self._raise(response)
        except ServiceError:
            raise
        except httpx.RequestError as exc:
            raise dify_error(str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise ServiceError(
                "DIFY_RESPONSE_INVALID",
                "Dify返回了无法解析的响应",
                502,
                True,
            ) from exc

    def parameters(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/parameters", headers=self.headers)
                self._raise(response)
                return response.json()
        except ServiceError:
            raise
        except httpx.RequestError as exc:
            raise dify_error(str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise ServiceError(
                "DIFY_RESPONSE_INVALID",
                "Dify返回了无法解析的响应",
                502,
                True,
            ) from exc

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}{path}", headers=self.headers, json=payload)
                self._raise(response)
                return response.json() if response.content else {}
        except ServiceError:
            raise
        except httpx.RequestError as exc:
            raise dify_error(str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise ServiceError(
                "DIFY_RESPONSE_INVALID",
                "Dify返回了无法解析的响应",
                502,
                True,
            ) from exc


def extract_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
            if text.startswith("json"):
                text = text[4:].lstrip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None
