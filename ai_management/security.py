from __future__ import annotations

import hashlib
import hmac

from fastapi import Header

from .config import get_settings
from .errors import ServiceError


def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    expected = get_settings().internal_api_token
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise ServiceError("UNAUTHORIZED", "内部服务鉴权失败", 401, False)


def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    expected = get_settings().admin_api_token
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise ServiceError("UNAUTHORIZED", "管理员鉴权失败", 401, False)


def require_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    if not x_user_id or not x_user_id.strip():
        raise ServiceError("VALIDATION_ERROR", "缺少 X-User-Id", 422, False)
    if len(x_user_id) > 255:
        raise ServiceError("VALIDATION_ERROR", "X-User-Id 长度无效", 422, False)
    return x_user_id.strip()


def user_hash(user_id: str) -> str:
    secret = get_settings().user_hmac_secret.encode("utf-8")
    return hmac.new(secret, user_id.encode("utf-8"), hashlib.sha256).hexdigest()


def dify_user(user_id: str) -> str:
    return f"u_{user_hash(user_id)[:40]}"
