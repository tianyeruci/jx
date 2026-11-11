from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ServiceError(Exception):
    code: str
    message: str
    status_code: int = 400
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def dify_error(message: str, *, timeout: bool = False, rate_limited: bool = False) -> ServiceError:
    if rate_limited:
        return ServiceError("DIFY_RATE_LIMITED", "AI服务请求过多，请稍后重试", 429, True)
    if timeout:
        return ServiceError("DIFY_TIMEOUT", "AI服务响应超时，请稍后重试", 504, True)
    return ServiceError("DIFY_UNAVAILABLE", "AI服务暂时不可用，请稍后重试", 502, True)
