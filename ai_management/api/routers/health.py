from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...cache import cache
from ...config import get_settings
from ...db import get_session
from ...dify import DifyAppClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(session: Session = Depends(get_session)) -> JSONResponse:
    settings = get_settings()
    database_ok = False
    try:
        session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        pass
    redis_ok = cache.ping()
    apps_configured = bool(
        settings.dify_business_app_api_key
        and settings.dify_moments_app_api_key
        and settings.dify_writing_app_api_key
        and settings.dify_summary_profile_app_api_key
    )
    knowledge_configured = bool(
        settings.dify_knowledge_api_key
        and settings.dify_general_dataset_id
        and settings.dify_course_dataset_id
    )
    configured = apps_configured and knowledge_configured
    dify_reachable = False
    if apps_configured:
        cached_dify_health = cache.get_json("ai:health:dify")
        if isinstance(cached_dify_health, dict):
            dify_reachable = bool(cached_dify_health.get("reachable"))
        else:
            try:
                DifyAppClient(
                    settings.dify_url,
                    settings.dify_business_app_api_key,
                    timeout_seconds=3,
                ).parameters()
                dify_reachable = True
            except Exception:
                dify_reachable = False
            cache.set_json(
                "ai:health:dify",
                {"reachable": dify_reachable},
                ttl=30,
            )
    ok = (
        database_ok
        and (redis_ok or settings.app_env != "production")
        and configured
        and dify_reachable
    )
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not_ready",
            "database": database_ok,
            "redis": redis_ok,
            "dify_apps_configured": apps_configured,
            "dify_knowledge_configured": knowledge_configured,
            "dify_reachable": dify_reachable,
        },
    )
