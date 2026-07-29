from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...cache import cache
from ...config import get_settings
from ...courses import record_click
from ...db import get_session
from ...dify import DifyAppClient
from ...security import require_internal_token

router = APIRouter(tags=["entry"])


@router.get(
    "/internal/v1/entry-config",
    dependencies=[Depends(require_internal_token)],
)
def entry_config() -> dict[str, Any]:
    settings = get_settings()
    definitions = [
        (
            "business_advisor",
            "商业顾问",
            settings.dify_business_app_api_key,
            {"history": True, "feedback": True, "course_recommendation": True},
        ),
        (
            "moments_advisor",
            "朋友圈顾问",
            settings.dify_moments_app_api_key,
            {"history": False, "feedback": False, "course_recommendation": False},
        ),
        (
            "writing_advisor",
            "日常写作",
            settings.dify_writing_app_api_key,
            {"history": False, "feedback": False, "course_recommendation": False},
        ),
    ]
    result: list[dict[str, Any]] = []
    for code, name, key, features in definitions:
        cache_key = f"ai:entry-config:{code}"
        parameters = cache.get_json(cache_key)
        if not isinstance(parameters, dict):
            parameters = {}
            try:
                parameters = DifyAppClient(
                    settings.dify_url,
                    key,
                    timeout_seconds=5,
                ).parameters()
                cache.set_json(cache_key, parameters, ttl=300)
            except Exception:
                pass
        result.append(
            {
                "code": code,
                "name": name,
                "opening_statement": parameters.get("opening_statement") or "",
                "suggested_questions": parameters.get("suggested_questions") or [],
                **features,
            }
        )
    return {"entries": result}


@router.get(
    "/r/{token}",
    response_class=RedirectResponse,
    status_code=302,
    responses={302: {"description": "课程正式链接跳转"}},
)
def redirect_course(
    token: str,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    return RedirectResponse(record_click(session, token), status_code=302)
