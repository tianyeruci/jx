from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api.routers.admin import router as admin_router
from .api.routers.admin_logs import router as admin_logs_router
from .api.routers.admin_management import router as admin_management_router
from .api.routers.business import router as business_router
from .api.routers.entry import router as entry_router
from .api.routers.health import router as health_router
from .api.routers.knowledge_admin import router as knowledge_admin_router
from .api.routers.tools import router as tools_router
from .cleaning import (
    maintain_conversion_markdown_artifacts,
)
from .cleaning import (
    router as cleaning_router,
)
from .config import get_settings
from .db import create_schema
from .errors import ServiceError
from .security import user_hash
from .services.observability import record_event
from .services.observability.logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.upload_root.mkdir(parents=True, exist_ok=True)
    artifact_cleanup = maintain_conversion_markdown_artifacts(
        delete_all=not settings.save_conversion_markdown
    )
    logger.info(
        "conversion markdown artifact maintenance completed enabled=%s removed=%s retained=%s",
        settings.save_conversion_markdown,
        artifact_cleanup["removed"],
        artifact_cleanup["retained"],
    )
    if settings.app_env != "production":
        create_schema()
    yield


async def service_error_handler(
    request: Request,
    exc: ServiceError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    if exc.status_code >= 500 or exc.code in {"UNAUTHORIZED", "DIFY_ERROR"}:
        record_event(
            level="ERROR" if exc.status_code >= 500 else "WARNING",
            category="error" if exc.status_code >= 500 else "security",
            component="api",
            event_code=exc.code,
            status="failed",
            message=exc.message,
            request_id=request_id,
            resource_type="http_request",
            resource_id=request.url.path,
            details={"method": request.method, "status_code": exc.status_code},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": request_id,
            "retryable": exc.retryable,
        },
    )


async def request_id_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.exception(
            "unhandled request error",
            extra={"request_id": request.state.request_id, "duration_ms": duration_ms},
        )
        record_event(
            level="ERROR",
            category="error",
            component="api",
            event_code="UNHANDLED_EXCEPTION",
            status="failed",
            message=f"{type(exc).__name__}: {str(exc)[:800]}",
            request_id=request.state.request_id,
            resource_type="http_request",
            resource_id=request.url.path,
            details={"method": request.method, "exception_type": type(exc).__name__},
            duration_ms=duration_ms,
        )
        raise
    response.headers["X-Request-Id"] = request.state.request_id
    raw_user_id = request.headers.get("X-User-Id")
    anonymous = user_hash(raw_user_id) if raw_user_id else ""
    logger.info(
        "request_accepted request_id=%s method=%s path=%s status=%s "
        "accept_ms=%s user_hash=%s",
        request.state.request_id,
        request.method,
        request.url.path,
        response.status_code,
        int((time.monotonic() - started) * 1000),
        anonymous,
    )
    return response


def create_app() -> FastAPI:
    application = FastAPI(
        title="jx-now AI Management Service",
        version="0.2.1",
        lifespan=lifespan,
    )
    application.add_exception_handler(ServiceError, service_error_handler)
    application.middleware("http")(request_id_middleware)
    for router in (
        business_router,
        tools_router,
        entry_router,
        admin_router,
        admin_management_router,
        admin_logs_router,
        cleaning_router,
        knowledge_admin_router,
        health_router,
    ):
        application.include_router(router)
    return application


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("ai_management.main:app", host="0.0.0.0", port=8000, reload=False)
