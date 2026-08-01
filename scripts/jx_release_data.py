from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from redis import Redis
from sqlalchemy import delete, func, select

from ai_management.config import get_settings
from ai_management.courses import rebuild_snapshot
from ai_management.db import SessionLocal
from ai_management.models import (
    AdminSetting,
    AIStyleSetting,
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    BusinessProfile,
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    Course,
    CourseClickDaily,
    ExternalDeletionTask,
    LearningRoute,
    LearningRouteProgress,
    SystemEventLog,
)
from ai_management.services.admin_settings import get_history_retention_days
from ai_management.services.style_settings import get_style_prompt
from knowledge_cleaner.dify import DifyKnowledgeClient

# This script is deliberately mounted into the currently running API image to
# take the pre-release snapshot.  The image can be one migration behind this
# script, so models introduced by the release must be optional until the new
# image and migration are active.
try:
    from ai_management.models import CourseDirection
except ImportError:  # pragma: no cover - exercised by the pre-release image
    CourseDirection = None

try:
    from ai_management.models import DifyPublicationShard, ModelCleaningTask
except ImportError:  # pragma: no cover - exercised by the pre-release image
    DifyPublicationShard = None
    ModelCleaningTask = None

FORMAL_COURSE_IDS = {
    "course_ai_001",
    "course_ai_002",
    "course_ai_003",
    "course_ai_legacy",
    "course_business_001",
    "course_business_002",
    "course_wechat_001",
    "course_wechat_002",
    "course_wechat_003",
}
FORMAL_ROUTE_IDS = {"route_ai_basic", "route_wechat_operation"}

AI_DATA_MODELS = tuple(
    model
    for model in (
        BusinessProfile,
        BusinessConversation,
        BusinessConversationSegment,
        BusinessMessage,
        LearningRouteProgress,
        CourseClickDaily,
        CleaningBatch,
        CleaningFile,
        CleaningDocument,
        ModelCleaningTask,
        DifyPublicationShard,
        ExternalDeletionTask,
        SystemEventLog,
    )
    if model is not None
)
PRESERVED_MODELS = tuple(
    model
    for model in (
        Course,
        LearningRoute,
        CourseDirection,
        AdminSetting,
        AIStyleSetting,
    )
    if model is not None
)


def _scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        column.name: _scalar(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _remote_metadata(document: dict[str, Any]) -> dict[str, str]:
    raw = document.get("doc_metadata") or document.get("metadata") or []
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if not isinstance(raw, list):
        return {}
    return {
        str(item.get("name")): str(item.get("value") or "")
        for item in raw
        if isinstance(item, dict) and item.get("name")
    }


def _document_summary(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(document.get("id") or ""),
        "enabled": bool(document.get("enabled", True)),
        "indexing_status": str(document.get("indexing_status") or ""),
    }


def _dify_documents() -> dict[str, list[dict[str, Any]]]:
    settings = get_settings()
    datasets = {
        "general": settings.dify_general_dataset_id,
        "course": settings.dify_course_dataset_id,
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for code, dataset_id in datasets.items():
        client = DifyKnowledgeClient(
            settings.knowledge_base_url,
            settings.dify_knowledge_api_key,
            dataset_id,
            timeout=60,
        )
        try:
            result[code] = [_document_summary(item) for item in client.iter_documents()]
        finally:
            client.close()
    return result


def _redis_client() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _redis_key_inventory(client: Redis) -> dict[str, Any]:
    """Return non-sensitive evidence; never persist raw keys or values."""
    keys = sorted(str(item) for item in client.scan_iter(match="*"))
    return {"count": len(keys), "fingerprint": _fingerprint(keys)}


def _redis_db() -> str:
    return urlsplit(get_settings().redis_url).path.lstrip("/") or "0"


def _redis_exclusivity_guard() -> dict[str, Any]:
    """Require explicit proof before any destructive Redis operation."""
    settings = get_settings()
    redis_url = urlsplit(settings.redis_url)
    redis_db = redis_url.path.lstrip("/") or "0"
    if redis_db != "5":
        raise RuntimeError(f"AI Redis must use DB 5, got DB {redis_db}")
    dify_url = os.getenv("DIFY_REDIS_URL", "")
    dify_db = os.getenv("DIFY_REDIS_DB", "")
    if dify_url:
        parsed_dify = urlsplit(dify_url)
        if parsed_dify.hostname == redis_url.hostname and (
            parsed_dify.port or 6379
        ) == (redis_url.port or 6379) and (parsed_dify.path.lstrip("/") or "0") == redis_db:
            raise RuntimeError("Redis DB 5 is not exclusive: DIFY_REDIS_URL points to it")
    if dify_db == redis_db:
        raise RuntimeError("Redis DB 5 is not exclusive: DIFY_REDIS_DB points to it")
    if os.getenv("AI_REDIS_EXCLUSIVE", "").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("set AI_REDIS_EXCLUSIVE=1 only after proving DB 5 is AI-only")
    if os.getenv("REDIS_BACKUP_CONFIRMED", "").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("set REDIS_BACKUP_CONFIRMED=1 only after a recoverable Redis backup")
    client = _redis_client()
    try:
        client.ping()
        inventory = _redis_key_inventory(client)
    finally:
        client.close()
    return {"redis_host": redis_url.hostname, "redis_db": redis_db, **inventory, "exclusive": True}


def _counts(session) -> dict[str, int]:
    return {
        model.__tablename__: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for model in (*AI_DATA_MODELS, *PRESERVED_MODELS)
    }


def _configuration_fingerprint() -> dict[str, Any]:
    settings = get_settings()
    database = urlsplit(settings.database_url.replace("postgresql+psycopg", "postgresql"))
    redis = urlsplit(settings.redis_url)
    return {
        "app_env": settings.app_env,
        "build_version": settings.build_version,
        "database_host": database.hostname,
        "database_name": database.path.lstrip("/"),
        "redis_host": redis.hostname,
        "redis_db": redis.path.lstrip("/"),
        "general_dataset_id": settings.dify_general_dataset_id,
        "course_dataset_id": settings.dify_course_dataset_id,
    }


def _fingerprint(values: Any) -> str:
    text = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identifier_summary(values: Any) -> dict[str, Any]:
    normalized = sorted({str(value) for value in values if str(value).strip()})
    return {"count": len(normalized), "fingerprint": _fingerprint(normalized)}


def _dify_id_sets() -> dict[str, set[str]]:
    return {
        code: {
            str(document.get("id") or "")
            for document in entries
            if str(document.get("id") or "")
        }
        for code, entries in _dify_documents().items()
    }


def _catalog_summary(
    session,
    *,
    excluded_course_ids: set[str] | None = None,
    excluded_route_ids: set[str] | None = None,
    excluded_direction_codes: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    excluded_course_ids = excluded_course_ids or set()
    excluded_route_ids = excluded_route_ids or set()
    excluded_direction_codes = excluded_direction_codes or set()
    course_ids = {
        str(value)
        for value in session.scalars(select(Course.course_id))
        if str(value) not in excluded_course_ids
    }
    route_ids = {
        str(value)
        for value in session.scalars(select(LearningRoute.route_id))
        if str(value) not in excluded_route_ids
    }
    direction_codes: set[str] = set()
    if CourseDirection is not None:
        direction_codes = {
            str(value)
            for value in session.scalars(select(CourseDirection.code))
            if str(value) not in excluded_direction_codes
        }
    return {
        "courses": _identifier_summary(course_ids),
        "routes": _identifier_summary(route_ids),
        "directions": _identifier_summary(direction_codes),
    }


def snapshot(output: Path) -> dict[str, Any]:
    with SessionLocal() as session:
        payload = {
            "schema_version": 3,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "configuration": {
                "app_env": get_settings().app_env,
                "build_version": get_settings().build_version,
                "redis_db": _redis_db(),
            },
            "counts": _counts(session),
            "catalog": _catalog_summary(session),
        }
    payload["dify"] = {
        code: _identifier_summary(values)
        for code, values in _dify_id_sets().items()
    }
    redis_client = _redis_client()
    try:
        payload["redis_inventory"] = _redis_key_inventory(redis_client)
    finally:
        redis_client.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def _formal_guard(session) -> None:
    course_ids = set(session.scalars(select(Course.course_id)))
    route_ids = set(session.scalars(select(LearningRoute.route_id)))
    if course_ids != FORMAL_COURSE_IDS:
        raise RuntimeError(
            f"正式课程集合不符合预期：count={len(course_ids)} "
            f"fingerprint={_fingerprint(course_ids)}"
        )
    if route_ids != FORMAL_ROUTE_IDS:
        raise RuntimeError(
            f"正式路线集合不符合预期：count={len(route_ids)} "
            f"fingerprint={_fingerprint(route_ids)}"
        )


def _fingerprint(values: Any) -> str:
    text = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_formal_course_document(metadata: dict[str, str]) -> bool:
    return (
        metadata.get("course_id") in FORMAL_COURSE_IDS
        or metadata.get("route_id") in FORMAL_ROUTE_IDS
    )


def _manifest_document_ids(manifest: Path | None) -> dict[str, set[str]]:
    """Read exact Dify IDs from a run manifest; names are never an authority."""
    result: dict[str, set[str]] = {"general": set(), "course": set()}
    if manifest is None:
        return result
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    raw = payload.get("dify_document_ids") or payload.get("created_dify_document_ids") or {}
    if isinstance(raw, list):
        raise ValueError("manifest.dify_document_ids must be a dataset map")
    if not isinstance(raw, dict):
        raise ValueError("manifest.dify_document_ids must be a dataset map or list")
    for code in result:
        values = raw.get(code) or []
        if not isinstance(values, list):
            raise ValueError(f"manifest.dify_document_ids.{code} must be a list")
        result[code] = {str(item) for item in values if str(item).strip()}
    if result["general"] & result["course"]:
        raise ValueError("manifest document ID cannot belong to both datasets")
    created_at = payload.get("created_at")
    if created_at:
        try:
            parsed = datetime.fromisoformat(str(created_at))
        except ValueError as error:
            raise ValueError("manifest created_at is invalid") from error
        if parsed.tzinfo is None or datetime.now(timezone.utc) - parsed > timedelta(days=1):
            raise ValueError("manifest is stale")
    return result


def _manifest_cleanup_targets(manifest: Path | None) -> dict[str, list[str]]:
    result = {
        "cleaning_batch_ids": [],
        "course_ids": [],
        "route_ids": [],
        "direction_codes": [],
        "redis_keys": [],
    }
    if manifest is None:
        return result
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    raw = payload.get("cleanup_targets") or {}
    if not isinstance(raw, dict):
        raise ValueError("manifest.cleanup_targets must be an object")
    for key in result:
        values = raw.get(key) or []
        if not isinstance(values, list):
            raise ValueError(f"manifest.cleanup_targets.{key} must be a list")
        result[key] = sorted(
            {str(item) for item in values if str(item).strip()}
        )
    if any(
        not key.startswith("ai:") for key in result["redis_keys"]
    ):
        raise ValueError("manifest redis_keys may contain only exact ai: keys")
    if set(result["course_ids"]) & FORMAL_COURSE_IDS:
        raise ValueError("manifest contains protected formal course IDs")
    if set(result["route_ids"]) & FORMAL_ROUTE_IDS:
        raise ValueError("manifest contains protected formal route IDs")
    return result


def _local_associated_document_ids() -> dict[str, set[str]]:
    """Collect only exact IDs created/associated by this service."""
    with SessionLocal() as session:
        cleaning_ids = {
            str(item)
            for item in session.scalars(
                select(CleaningDocument.dify_document_id).where(
                    CleaningDocument.dify_document_id.is_not(None)
                )
            )
            if item
        }
        catalog_ids = set(_local_nonformal_catalog_document_ids())
    return {"general": set(cleaning_ids), "course": cleaning_ids | catalog_ids}


def _planned_document_candidates(
    documents: dict[str, list[dict[str, Any]]],
    manifest_ids: dict[str, set[str]],
) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]], dict[str, list[str]]]:
    candidates: dict[str, list[str]] = {"general": [], "course": []}
    reasons: dict[str, dict[str, list[str]]] = {"general": {}, "course": {}}
    preserved_unknown: dict[str, list[str]] = {"general": [], "course": []}
    for code, entries in documents.items():
        exact_ids = manifest_ids.get(code, set())
        for document in entries:
            document_id = str(document.get("id") or "")
            if not document_id:
                continue
            if document_id in exact_ids:
                candidates[code].append(document_id)
                reasons[code][document_id] = ["explicit_run_manifest"]
            else:
                preserved_unknown[code].append(document_id)
    return (
        {code: sorted(set(ids)) for code, ids in candidates.items()},
        reasons,
        {code: sorted(set(ids)) for code, ids in preserved_unknown.items()},
    )


def _delete_planned_dify_documents(
    candidates: dict[str, list[str]],
) -> dict[str, list[str]]:
    settings = get_settings()
    datasets = {
        "general": settings.dify_general_dataset_id,
        "course": settings.dify_course_dataset_id,
    }
    deleted: dict[str, list[str]] = {"general": [], "course": []}
    for code, document_ids in candidates.items():
        client = DifyKnowledgeClient(
            settings.knowledge_base_url,
            settings.dify_knowledge_api_key,
            datasets[code],
            timeout=60,
        )
        try:
            for document_id in document_ids:
                client.delete_document(document_id)
                deleted[code].append(document_id)
        finally:
            client.close()
    return deleted


def _local_nonformal_catalog_document_ids() -> list[str]:
    with SessionLocal() as session:
        course_ids = [
            str(item)
            for item in session.scalars(
                select(Course.dify_document_id).where(
                    Course.course_id.not_in(FORMAL_COURSE_IDS),
                    Course.dify_document_id.is_not(None),
                )
            )
            if item
        ]
        route_ids = [
            str(item)
            for item in session.scalars(
                select(LearningRoute.dify_document_id).where(
                    LearningRoute.route_id.not_in(FORMAL_ROUTE_IDS),
                    LearningRoute.dify_document_id.is_not(None),
                )
            )
            if item
        ]
    return sorted(set([*course_ids, *route_ids]))


def _plan_basis(
    configuration: dict[str, Any],
    counts: dict[str, int],
    documents: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {"configuration": configuration, "counts": counts, "dify_documents": documents}


def _legacy_plan_clean_disabled(output: Path, manifest: Path | None = None) -> dict[str, Any]:
    with SessionLocal() as session:
        configuration = _configuration_fingerprint()
        counts = _counts(session)
    documents = _dify_documents()
    manifest_ids = _manifest_document_ids(manifest)
    cleanup_targets = _manifest_cleanup_targets(manifest)
    candidates, reasons, preserved_unknown = _planned_document_candidates(
        documents, manifest_ids
    )
    basis = _plan_basis(configuration, counts, documents)
    plan = {
        "plan_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_fingerprint": _fingerprint(basis),
        "plan_fingerprint": _fingerprint(
            {
                "basis": basis,
                "candidate_ids": candidates,
                "cleanup_targets": cleanup_targets,
            }
        ),
        "configuration": configuration,
        "counts": counts,
        "candidate_ids": candidates,
        "candidate_reasons": reasons,
        "preserved_unknown_document_ids": preserved_unknown,
        "manifest_fingerprint": _fingerprint(
            {
                "dify": {
                    code: sorted(values)
                    for code, values in manifest_ids.items()
                },
                "cleanup_targets": cleanup_targets,
            }
        ),
        "cleanup_targets": cleanup_targets,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return plan


def _legacy_verified_clean_plan_disabled(plan_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[dict[str, Any]]]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("plan_version") != 2 or not plan.get("plan_fingerprint"):
        raise RuntimeError("clean requires a versioned plan-clean output")
    with SessionLocal() as session:
        counts = _counts(session)
    documents = _dify_documents()
    basis = _plan_basis(_configuration_fingerprint(), counts, documents)
    if _fingerprint(basis) != plan.get("source_fingerprint"):
        raise RuntimeError("current data no longer matches plan source_fingerprint")
    candidates = plan.get("candidate_ids")
    if not isinstance(candidates, dict) or set(candidates) != {"general", "course"}:
        raise RuntimeError("plan candidate_ids must explicitly cover both Dify datasets")
    current_ids = {
        code: {str(item.get("id") or "") for item in documents.get(code, [])}
        for code in ("general", "course")
    }
    for code in current_ids:
        if not set(candidates[code]) <= current_ids[code]:
            raise RuntimeError(f"plan contains unknown {code} Dify document ID")
    expected_plan_fingerprint = _fingerprint(
        {
            "basis": basis,
            "candidate_ids": candidates,
            "cleanup_targets": plan.get("cleanup_targets") or {},
        }
    )
    if expected_plan_fingerprint != plan.get("plan_fingerprint"):
        raise RuntimeError("clean plan fingerprint no longer matches its exact targets")
    return plan, basis, documents


def _legacy_clean_disabled(output: Path, plan_path: Path) -> dict[str, Any]:
    raise RuntimeError("legacy cleanup is disabled; use the manifest-only v3 entry point")
    plan, basis, before_documents = _legacy_verified_clean_plan_disabled(plan_path)
    candidates = {
        code: sorted(set(str(item) for item in plan["candidate_ids"].get(code, [])))
        for code in ("general", "course")
    }
    cleanup_targets = plan.get("cleanup_targets") or {}
    batch_ids = sorted(set(cleanup_targets.get("cleaning_batch_ids") or []))
    extra_course_ids = sorted(set(cleanup_targets.get("course_ids") or []))
    extra_route_ids = sorted(set(cleanup_targets.get("route_ids") or []))
    direction_codes = sorted(set(cleanup_targets.get("direction_codes") or []))
    redis_keys = sorted(set(cleanup_targets.get("redis_keys") or []))
    if any(not key.startswith("ai:") for key in redis_keys):
        raise RuntimeError("clean plan contains a non-ai Redis key")
    if set(extra_course_ids) & FORMAL_COURSE_IDS:
        raise RuntimeError("clean plan contains protected formal course IDs")
    if set(extra_route_ids) & FORMAL_ROUTE_IDS:
        raise RuntimeError("clean plan contains protected formal route IDs")
    with SessionLocal() as validation_session:
        current_batch_ids = (
            set(
                validation_session.scalars(
                    select(CleaningBatch.id).where(
                        CleaningBatch.id.in_(batch_ids)
                    )
                )
            )
            if batch_ids
            else set()
        )
        current_course_ids = (
            set(
                validation_session.scalars(
                    select(Course.course_id).where(
                        Course.course_id.in_(extra_course_ids)
                    )
                )
            )
            if extra_course_ids
            else set()
        )
        current_route_ids = (
            set(
                validation_session.scalars(
                    select(LearningRoute.route_id).where(
                        LearningRoute.route_id.in_(extra_route_ids)
                    )
                )
            )
            if extra_route_ids
            else set()
        )
        current_direction_codes = (
            set(
                validation_session.scalars(
                    select(CourseDirection.code).where(
                        CourseDirection.code.in_(direction_codes)
                    )
                )
            )
            if direction_codes
            else set()
        )
    if current_batch_ids != set(batch_ids):
        raise RuntimeError("cleaning_batch_ids no longer match current database")
    if current_course_ids != set(extra_course_ids):
        raise RuntimeError("course_ids no longer match current database")
    if current_route_ids != set(extra_route_ids):
        raise RuntimeError("route_ids no longer match current database")
    if current_direction_codes != set(direction_codes):
        raise RuntimeError("direction_codes no longer match current database")

    deleted_documents = _delete_planned_dify_documents(candidates)

    with SessionLocal() as session:
        before_counts = basis["counts"]
        if extra_route_ids:
            session.execute(
                delete(LearningRoute).where(
                    LearningRoute.route_id.in_(extra_route_ids)
                )
            )
        if extra_course_ids:
            session.execute(
                delete(Course).where(Course.course_id.in_(extra_course_ids))
            )
        if direction_codes:
            session.execute(
                delete(CourseDirection).where(
                    CourseDirection.code.in_(direction_codes)
                )
            )
        if batch_ids:
            session.execute(
                delete(SystemEventLog).where(
                    SystemEventLog.resource_id.in_(batch_ids)
                )
            )
            session.execute(
                delete(CleaningBatch).where(
                    CleaningBatch.id.in_(batch_ids)
                )
            )
        session.commit()
        _formal_guard(session)
        after_counts = _counts(session)

    redis_client = _redis_client()
    try:
        redis_inventory_before = _redis_key_inventory(redis_client)
        redis_deleted = (
            int(redis_client.delete(*redis_keys)) if redis_keys else 0
        )
    finally:
        redis_client.close()

    with SessionLocal() as session:
        rebuild_snapshot(session)
        history_retention_days = get_history_retention_days(session)
        style_codes = list(session.scalars(select(AIStyleSetting.code)))
        for code in style_codes:
            get_style_prompt(session, code)

    redis_client = _redis_client()
    try:
        redis_inventory_after = _redis_key_inventory(redis_client)
    finally:
        redis_client.close()

    after_documents = _dify_documents()
    result = {
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "before_counts": before_counts,
        "after_counts": after_counts,
        "dify_counts_before": {
            code: len(items) for code, items in before_documents.items()
        },
        "dify_candidate_ids": candidates,
        "dify_deleted_ids": deleted_documents,
        "dify_counts_after": {
            code: len(items) for code, items in after_documents.items()
        },
        "redis_inventory_before": redis_inventory_before,
        "redis_inventory_after": redis_inventory_after,
        "redis_db": _redis_db(),
        "redis_candidate_key_count": len(redis_keys),
        "redis_deleted_key_count": redis_deleted,
        "plan_fingerprint": plan["plan_fingerprint"],
        "history_retention_days": history_retention_days,
        "style_setting_count": len(style_codes),
        "formal_course_count": len(FORMAL_COURSE_IDS),
        "formal_route_count": len(FORMAL_ROUTE_IDS),
        "formal_course_fingerprint": _fingerprint(sorted(FORMAL_COURSE_IDS)),
        "formal_route_fingerprint": _fingerprint(sorted(FORMAL_ROUTE_IDS)),
        "removed_extra_course_ids": extra_course_ids,
        "removed_extra_route_ids": extra_route_ids,
        "removed_direction_codes": direction_codes,
        "removed_cleaning_batch_ids": batch_ids,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def _legacy_safe_summary_disabled(payload: dict[str, Any]) -> dict[str, Any]:
    if "courses" in payload:
        course_ids = {item["course_id"] for item in payload["courses"]}
        route_ids = {item["route_id"] for item in payload["routes"]}
        return {
            "counts": payload["counts"],
            "course_count": len(payload["courses"]),
            "route_count": len(payload["routes"]),
            "course_direction_count": len(payload.get("course_directions") or []),
            "course_fingerprint": _fingerprint(
                sorted(item["course_id"] for item in payload["courses"])
            ),
            "route_fingerprint": _fingerprint(
                sorted(item["route_id"] for item in payload["routes"])
            ),
            "extra_course_ids": sorted(course_ids - FORMAL_COURSE_IDS),
            "extra_route_ids": sorted(route_ids - FORMAL_ROUTE_IDS),
            "dify_counts": {
                code: len(items)
                for code, items in payload.get("dify_documents", {}).items()
            },
            "redis_key_count": int(
                (payload.get("redis_inventory") or {}).get("count", 0)
            ),
            "output_fingerprint": _fingerprint(payload),
        }
    return payload


# Version 3 is intentionally separate from the legacy helpers above.  The
# entry point only calls these functions: historical reports and inferred
# service associations are never deletion authority.
_MANIFEST_SCHEMA_VERSION = 3
_MANIFEST_KIND = "acceptance_cleanup_manifest"
_AUTOMATED_MANIFEST_SCOPE = "automated_cleaning_acceptance"
_TEST_UI_MANIFEST_SCOPE = "manual_test_ui_acceptance"
_TEST_UI_CLEANUP_RUN_METADATA_KEY = "_test_ui_cleanup_run_id"
_TARGET_KEYS = (
    "cleaning_batch_ids",
    "course_ids",
    "route_ids",
    "direction_codes",
    "redis_keys",
    "chat_user_ids",
)


def _manifest_run_id_valid(scope: str, run_id: str) -> bool:
    if scope == _AUTOMATED_MANIFEST_SCOPE:
        return bool(re.fullmatch(r"cleaning-[a-f0-9]{32}", run_id))
    if scope == _TEST_UI_MANIFEST_SCOPE:
        return bool(re.fullmatch(r"cleaning-ui-[a-f0-9]{32}", run_id))
    return False


def _canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _write_v3_manifest(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Write a canonical signed manifest; identifiers stay in 0600 storage."""

    unsigned = dict(payload)
    unsigned.pop("integrity", None)
    payload = {
        **unsigned,
        "integrity": {"manifest_sha256": _canonical_fingerprint(unsigned)},
    }
    _write_private_json(path, payload)
    return payload


def _unique_strings(raw: Any, *, field: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list")
    values = [str(value).strip() for value in raw]
    if any(not value or "\n" in value or "\r" in value for value in values):
        raise ValueError(f"{field} contains an invalid identifier")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicate identifiers")
    return sorted(values)


def _load_v3_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scope = str(payload.get("scope") or "")
    if (
        payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION
        or payload.get("kind") != _MANIFEST_KIND
        or scope not in {_AUTOMATED_MANIFEST_SCOPE, _TEST_UI_MANIFEST_SCOPE}
    ):
        raise RuntimeError("clean requires an acceptance cleanup manifest v3")
    integrity = payload.get("integrity") or {}
    supplied = str(integrity.get("manifest_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("integrity", None)
    expected = _canonical_fingerprint(unsigned)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise RuntimeError("cleanup manifest integrity check failed")
    run_id = str(payload.get("run_id") or "")
    if not _manifest_run_id_valid(scope, run_id):
        raise RuntimeError("cleanup manifest run_id is invalid")
    try:
        created_at = datetime.fromisoformat(str(payload.get("created_at") or ""))
    except ValueError as error:
        raise RuntimeError("cleanup manifest created_at is invalid") from error
    if created_at.tzinfo is None or datetime.now(timezone.utc) - created_at > timedelta(days=1):
        raise RuntimeError("cleanup manifest is stale or missing a timezone")
    raw_dify = payload.get("targets", {}).get("dify_documents")
    if not isinstance(raw_dify, dict) or set(raw_dify) != {"general", "course"}:
        raise RuntimeError("cleanup manifest must map exact Dify IDs by dataset")
    dify_documents = {
        code: _unique_strings(raw_dify[code], field=f"dify_documents.{code}")
        for code in ("general", "course")
    }
    if set(dify_documents["general"]) & set(dify_documents["course"]):
        raise RuntimeError("cleanup manifest maps one Dify ID to both datasets")
    raw_targets = payload.get("targets") or {}
    targets = {
        key: _unique_strings(raw_targets.get(key, []), field=key)
        for key in _TARGET_KEYS
    }
    for batch_id in targets["cleaning_batch_ids"]:
        try:
            uuid.UUID(batch_id)
        except ValueError as error:
            raise RuntimeError("cleanup manifest has an invalid batch ID") from error
    allowed_redis_keys = {
        f"ai:cleaning:batch:{batch_id}"
        for batch_id in targets["cleaning_batch_ids"]
    } | {
        f"ai:cleaning:batch:{batch_id}:files"
        for batch_id in targets["cleaning_batch_ids"]
    }
    if set(targets["redis_keys"]) - allowed_redis_keys:
        raise RuntimeError("cleanup manifest redis keys are not exact batch keys")
    if any(
        not re.fullmatch(r"cleaning-acceptance-[a-f0-9]{32}", value)
        for value in targets["chat_user_ids"]
    ):
        raise RuntimeError("cleanup manifest has a non-acceptance chat user")
    return {
        "run_id": run_id,
        "scope": scope,
        "manifest_fingerprint": supplied,
        "targets": {**targets, "dify_documents": dify_documents},
    }


def _parse_recent_timestamp(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError as error:
        raise RuntimeError(f"{field} is invalid") from error
    if parsed.tzinfo is None or datetime.now(timezone.utc) - parsed > timedelta(days=1):
        raise RuntimeError(f"{field} is stale or missing a timezone")
    return parsed


def _capture_test_ui_catalog_baseline(*, output: Path, run_id: str) -> dict[str, Any]:
    """Record pre-test catalog ownership privately before any UI publication.

    A catalogue import can deliberately reuse an existing course URL/ID.  That
    is correct production behavior but it cannot be deleted as a test artefact.
    The baseline therefore makes a manual UI cleanup refuse any pre-existing
    course or route rather than silently deleting or overwriting it.
    """

    if not _manifest_run_id_valid(_TEST_UI_MANIFEST_SCOPE, run_id):
        raise RuntimeError("test UI cleanup run_id is invalid")
    with SessionLocal() as session:
        payload = {
            "schema_version": 1,
            "kind": "test_ui_catalog_baseline",
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "build_version": get_settings().build_version,
            "course_ids": sorted(str(value) for value in session.scalars(select(Course.course_id))),
            "route_ids": sorted(str(value) for value in session.scalars(select(LearningRoute.route_id))),
            "direction_codes": (
                sorted(str(value) for value in session.scalars(select(CourseDirection.code)))
                if CourseDirection is not None
                else []
            ),
        }
    _write_v3_manifest(output, payload)
    return {
        "schema_version": 1,
        "scope": _TEST_UI_MANIFEST_SCOPE,
        "catalog": {
            "courses": _identifier_summary(payload["course_ids"]),
            "routes": _identifier_summary(payload["route_ids"]),
            "directions": _identifier_summary(payload["direction_codes"]),
        },
    }


def _load_test_ui_catalog_baseline(path: Path, *, run_id: str) -> dict[str, set[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(payload)
    integrity = unsigned.pop("integrity", {})
    supplied = str((integrity or {}).get("manifest_sha256") or "")
    if (
        payload.get("schema_version") != 1
        or payload.get("kind") != "test_ui_catalog_baseline"
        or payload.get("run_id") != run_id
        or not supplied
        or not hmac.compare_digest(supplied, _canonical_fingerprint(unsigned))
    ):
        raise RuntimeError("test UI catalog baseline integrity check failed")
    _parse_recent_timestamp(payload.get("created_at"), field="catalog baseline created_at")
    return {
        "course_ids": set(_unique_strings(payload.get("course_ids", []), field="course_ids")),
        "route_ids": set(_unique_strings(payload.get("route_ids", []), field="route_ids")),
        "direction_codes": set(
            _unique_strings(payload.get("direction_codes", []), field="direction_codes")
        ),
    }


def _test_ui_catalog_targets(documents: list[CleaningDocument]) -> dict[str, set[str]]:
    targets = {"course_ids": set(), "route_ids": set(), "direction_codes": set()}
    for document in documents:
        metadata = document.metadata_json or {}
        audit = document.audit_json or {}
        record = audit.get("catalog_record") if isinstance(audit, dict) else None
        for key in ("course_id", "route_id", "direction"):
            value = ""
            if isinstance(record, dict):
                value = str(record.get(key) or "")
            if not value and isinstance(metadata, dict):
                value = str(metadata.get(key) or "")
            if value:
                target_key = "direction_codes" if key == "direction" else f"{key}s"
                targets[target_key].add(value)
    return targets


def _capture_test_ui_manifest(
    *,
    output: Path,
    run_id: str,
    batch_ids: list[str],
    catalog_baseline: Path | None,
) -> dict[str, Any]:
    """Produce a short-lived cleanup manifest from explicit test UI batch IDs.

    This deliberately has no filename, timestamp-window, source-file or local
    association fallback.  Every batch must have been tagged by the test UI in
    the same run, and Dify targets are read from those exact database rows.
    """

    if not _manifest_run_id_valid(_TEST_UI_MANIFEST_SCOPE, run_id):
        raise RuntimeError("test UI cleanup run_id is invalid")
    batch_ids = _unique_strings(batch_ids, field="batch_id")
    if not batch_ids:
        raise RuntimeError("at least one exact test UI batch_id is required")
    for batch_id in batch_ids:
        try:
            uuid.UUID(batch_id)
        except ValueError as error:
            raise RuntimeError("test UI cleanup batch_id is invalid") from error

    settings = get_settings()
    if DifyPublicationShard is None:
        raise RuntimeError("test UI cleanup capture requires the current release models")
    with SessionLocal() as session:
        batches = {
            str(batch.id): batch
            for batch in session.scalars(
                select(CleaningBatch).where(CleaningBatch.id.in_(batch_ids))
            )
        }
        if set(batches) != set(batch_ids):
            raise RuntimeError("test UI cleanup batch IDs do not all exist")
        for batch in batches.values():
            if (batch.common_metadata or {}).get(_TEST_UI_CLEANUP_RUN_METADATA_KEY) != run_id:
                raise RuntimeError("test UI cleanup batch is not tagged by this UI run")
            if batch.status != "completed":
                raise RuntimeError("test UI cleanup batch is not terminal; wait for completion")

        files = list(
            session.scalars(select(CleaningFile).where(CleaningFile.batch_id.in_(batch_ids)))
        )
        file_ids = [str(row.id) for row in files]
        documents = list(
            session.scalars(
                select(CleaningDocument).where(CleaningDocument.file_id.in_(file_ids))
            )
            if file_ids
            else []
        )
        shards = list(
            session.scalars(
                select(DifyPublicationShard).where(DifyPublicationShard.file_id.in_(file_ids))
            )
            if file_ids
            else []
        )
        catalog_targets = _test_ui_catalog_targets(documents)
        current_catalog = {
            "course_ids": set(str(value) for value in session.scalars(select(Course.course_id))),
            "route_ids": set(str(value) for value in session.scalars(select(LearningRoute.route_id))),
            "direction_codes": (
                set(str(value) for value in session.scalars(select(CourseDirection.code)))
                if CourseDirection is not None
                else set()
            ),
        }

    if catalog_targets["course_ids"] or catalog_targets["route_ids"]:
        if catalog_baseline is None:
            raise RuntimeError("catalog publication cleanup requires a pre-upload catalog baseline")
        baseline = _load_test_ui_catalog_baseline(catalog_baseline, run_id=run_id)
        for key in ("course_ids", "route_ids"):
            if catalog_targets[key] & baseline[key]:
                raise RuntimeError("test UI catalog target existed before the test run")
            if not catalog_targets[key] <= current_catalog[key]:
                raise RuntimeError("test UI catalog target is not currently owned by this batch")
        catalog_targets["direction_codes"] -= baseline["direction_codes"]
        if not catalog_targets["direction_codes"] <= current_catalog["direction_codes"]:
            raise RuntimeError("test UI direction target is not currently owned by this batch")
    else:
        catalog_targets = {key: set() for key in catalog_targets}

    dify_documents = {"general": set(), "course": set()}
    file_batch_ids = {str(row.id): str(row.batch_id) for row in files}
    for row in documents:
        if row.dify_document_id:
            dify_documents[batches[file_batch_ids[str(row.file_id)]].knowledge_base_code].add(
                str(row.dify_document_id)
            )
    for row in shards:
        if row.dify_document_id:
            dify_documents[batches[file_batch_ids[str(row.file_id)]].knowledge_base_code].add(
                str(row.dify_document_id)
            )

    payload = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "kind": _MANIFEST_KIND,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_version": settings.build_version,
        "batch_build_versions": sorted({str(batch.build_version) for batch in batches.values()}),
        "scope": _TEST_UI_MANIFEST_SCOPE,
        "targets": {
            "dify_documents": {code: sorted(values) for code, values in dify_documents.items()},
            "cleaning_batch_ids": batch_ids,
            "course_ids": sorted(catalog_targets["course_ids"]),
            "route_ids": sorted(catalog_targets["route_ids"]),
            "direction_codes": sorted(catalog_targets["direction_codes"]),
            "redis_keys": [
                key
                for batch_id in batch_ids
                for key in (
                    f"ai:cleaning:batch:{batch_id}",
                    f"ai:cleaning:batch:{batch_id}:files",
                )
            ],
            "chat_user_ids": [],
        },
    }
    signed_payload = _write_v3_manifest(output, payload)
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "scope": _TEST_UI_MANIFEST_SCOPE,
        "manifest_fingerprint": (signed_payload.get("integrity") or {}).get("manifest_sha256", ""),
        "target_counts": {
            "dify": {code: len(values) for code, values in dify_documents.items()},
            "cleaning_batches": len(batch_ids),
            "courses": len(catalog_targets["course_ids"]),
            "routes": len(catalog_targets["route_ids"]),
            "directions": len(catalog_targets["direction_codes"]),
        },
    }


def _validate_database_targets(session, targets: dict[str, Any]) -> None:
    checks: tuple[tuple[str, Any, Any], ...] = (
        ("cleaning_batch_ids", CleaningBatch.id, CleaningBatch),
        ("course_ids", Course.course_id, Course),
        ("route_ids", LearningRoute.route_id, LearningRoute),
    )
    for name, column, model in checks:
        values = set(targets[name])
        current = (
            {str(value) for value in session.scalars(select(column).where(column.in_(values)))}
            if values
            else set()
        )
        if current != values:
            raise RuntimeError(f"cleanup manifest {name} no longer match the database")
    directions = set(targets["direction_codes"])
    if directions and CourseDirection is None:
        raise RuntimeError("cleanup manifest targets directions unavailable before migration")
    if directions and CourseDirection is not None:
        current_directions = {
            str(value)
            for value in session.scalars(
                select(CourseDirection.code).where(CourseDirection.code.in_(directions))
            )
        }
        if current_directions != directions:
            raise RuntimeError("cleanup manifest directions no longer match the database")


def _preflight_v3(manifest: dict[str, Any], *, allow_journalled: bool) -> dict[str, Any]:
    targets = manifest["targets"]
    document_ids = _dify_id_sets()
    journalled = {"general": set(), "course": set()}
    if allow_journalled:
        journalled = _load_journalled_ids(manifest["run_id"])
    for code in ("general", "course"):
        required = set(targets["dify_documents"][code]) - journalled[code]
        if not required <= document_ids[code]:
            raise RuntimeError(f"cleanup manifest has unknown {code} Dify document IDs")
    with SessionLocal() as session:
        _validate_database_targets(session, targets)
        non_target_catalog = _catalog_summary(
            session,
            excluded_course_ids=set(targets["course_ids"]),
            excluded_route_ids=set(targets["route_ids"]),
            excluded_direction_codes=set(targets["direction_codes"]),
        )
        counts = _counts(session)
    return {
        "counts": counts,
        "non_target_catalog": non_target_catalog,
        "dify": {
            code: _identifier_summary(document_ids[code])
            for code in ("general", "course")
        },
    }


def _final_cleanup_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate every exact target before the first Dify deletion."""
    return _preflight_v3(manifest, allow_journalled=True)


def plan_clean_v3(output: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _load_v3_manifest(manifest_path)
    preflight = _preflight_v3(manifest, allow_journalled=False)
    plan = {
        "plan_version": 3,
        "run_id": manifest["run_id"],
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "targets": manifest["targets"],
        "preflight": preflight,
    }
    plan["plan_fingerprint"] = _canonical_fingerprint(plan)
    _write_private_json(output, plan)
    return {
        "plan_version": 3,
        "manifest_fingerprint": plan["manifest_fingerprint"],
        "target_counts": {
            "dify": {
                code: len(plan["targets"]["dify_documents"][code])
                for code in ("general", "course")
            },
            **{key: len(plan["targets"][key]) for key in _TARGET_KEYS},
        },
    }


def _verified_clean_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    supplied = str(plan.pop("plan_fingerprint", ""))
    if plan.get("plan_version") != 3 or not supplied:
        raise RuntimeError("clean requires a version 3 private cleanup plan")
    if not hmac.compare_digest(supplied, _canonical_fingerprint(plan)):
        raise RuntimeError("private cleanup plan integrity check failed")
    plan["plan_fingerprint"] = supplied
    manifest = {
        "run_id": plan.get("run_id"),
        "manifest_fingerprint": plan.get("manifest_fingerprint"),
        "targets": plan.get("targets"),
    }
    if not isinstance(manifest["targets"], dict):
        raise RuntimeError("private cleanup plan has no exact targets")
    return plan


def _journal_path(run_id: str) -> Path:
    return Path(f"/private/{run_id}-cleanup-journal.json")


def _load_journalled_ids(run_id: str) -> dict[str, set[str]]:
    path = _journal_path(run_id)
    if not path.exists():
        return {"general": set(), "course": set()}
    payload = json.loads(path.read_text(encoding="utf-8"))
    completed = payload.get("deleted_dify_documents") or {}
    return {
        code: {str(value) for value in completed.get(code) or []}
        for code in ("general", "course")
    }


def _record_journal(run_id: str, deleted: dict[str, set[str]]) -> None:
    _write_private_json(
        _journal_path(run_id),
        {
            "schema_version": 1,
            "run_id": run_id,
            "deleted_dify_documents": {
                code: sorted(values) for code, values in deleted.items()
            },
        },
    )


def _delete_planned_dify_documents_v3(manifest: dict[str, Any]) -> dict[str, set[str]]:
    settings = get_settings()
    datasets = {
        "general": settings.dify_general_dataset_id,
        "course": settings.dify_course_dataset_id,
    }
    deleted = _load_journalled_ids(manifest["run_id"])
    for code in ("general", "course"):
        client = DifyKnowledgeClient(
            settings.knowledge_base_url,
            settings.dify_knowledge_api_key,
            datasets[code],
            timeout=60,
        )
        try:
            for document_id in manifest["targets"]["dify_documents"][code]:
                if document_id in deleted[code]:
                    continue
                client.delete_document(document_id)
                if document_id in _dify_id_sets()[code]:
                    raise RuntimeError(f"Dify did not remove the exact {code} document")
                deleted[code].add(document_id)
                _record_journal(manifest["run_id"], deleted)
        finally:
            client.close()
    return deleted


def _delete_exact_chat_users(user_ids: list[str]) -> int:
    if not user_ids:
        return 0
    settings = get_settings()
    client = httpx.Client(
        base_url="http://api:8000",
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"X-Internal-Token": settings.internal_api_token, "X-Admin-Token": settings.admin_api_token},
    )
    try:
        for user_id in user_ids:
            response = client.delete(f"/internal/v1/admin/users/{user_id}/ai-data")
            if response.status_code not in {200, 204}:
                raise RuntimeError("acceptance chat cleanup was rejected")
    finally:
        client.close()
    return len(user_ids)


def clean_v3(output: Path, plan_path: Path, private_result: Path) -> dict[str, Any]:
    plan = _verified_clean_plan(plan_path)
    manifest = {
        "run_id": str(plan["run_id"]),
        "manifest_fingerprint": str(plan["manifest_fingerprint"]),
        "targets": plan["targets"],
    }
    preflight = _final_cleanup_gate(manifest)
    if preflight["non_target_catalog"] != plan.get("preflight", {}).get("non_target_catalog"):
        raise RuntimeError("non-target catalog changed after cleanup plan was approved")
    deleted_dify = _delete_planned_dify_documents_v3(manifest)
    targets = manifest["targets"]
    with SessionLocal() as session:
        if targets["route_ids"]:
            session.execute(delete(LearningRoute).where(LearningRoute.route_id.in_(targets["route_ids"])))
        if targets["course_ids"]:
            session.execute(delete(Course).where(Course.course_id.in_(targets["course_ids"])))
        if targets["direction_codes"] and CourseDirection is not None:
            session.execute(delete(CourseDirection).where(CourseDirection.code.in_(targets["direction_codes"])))
        if targets["cleaning_batch_ids"]:
            session.execute(delete(SystemEventLog).where(SystemEventLog.resource_id.in_(targets["cleaning_batch_ids"])))
            session.execute(delete(CleaningBatch).where(CleaningBatch.id.in_(targets["cleaning_batch_ids"])))
        non_target_after = _catalog_summary(
            session,
            excluded_course_ids=set(targets["course_ids"]),
            excluded_route_ids=set(targets["route_ids"]),
            excluded_direction_codes=set(targets["direction_codes"]),
        )
        if non_target_after != preflight["non_target_catalog"]:
            raise RuntimeError("cleanup would change non-target catalog data")
        session.commit()
        after_counts = _counts(session)
        rebuild_snapshot(session)
    redis_client = _redis_client()
    try:
        deleted_redis = int(redis_client.delete(*targets["redis_keys"])) if targets["redis_keys"] else 0
    finally:
        redis_client.close()
    deleted_chat_users = _delete_exact_chat_users(targets["chat_user_ids"])
    private = {
        "schema_version": 3,
        "run_id": manifest["run_id"],
        "removed_cleaning_batch_ids": targets["cleaning_batch_ids"],
        "deleted_dify_documents": {code: sorted(values) for code, values in deleted_dify.items()},
    }
    _write_private_json(private_result, private)
    result = {
        "schema_version": 3,
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "plan_fingerprint": plan["plan_fingerprint"],
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "deleted_dify_counts": {code: len(values) for code, values in deleted_dify.items()},
        "deleted_cleaning_batch_count": len(targets["cleaning_batch_ids"]),
        "deleted_course_count": len(targets["course_ids"]),
        "deleted_route_count": len(targets["route_ids"]),
        "deleted_direction_count": len(targets["direction_codes"]),
        "deleted_redis_key_count": deleted_redis,
        "deleted_chat_user_count": deleted_chat_users,
        "after_counts": after_counts,
        "non_target_catalog": preflight["non_target_catalog"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return result


def safe_summary(payload: dict[str, Any]) -> dict[str, Any]:
    forbidden = {
        "dify_candidate_ids",
        "dify_deleted_ids",
        "removed_cleaning_batch_ids",
        "removed_extra_course_ids",
        "removed_extra_route_ids",
        "removed_direction_codes",
    }
    return {key: value for key, value in payload.items() if key not in forbidden}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "plan-clean", "clean"):
        child = subparsers.add_parser(command)
        child.add_argument("--output", type=Path, required=True)
        if command == "plan-clean":
            child.add_argument("--manifest", type=Path, required=True)
        elif command == "clean":
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--private-result", type=Path, required=True)
    catalog_baseline = subparsers.add_parser("capture-ui-catalog-baseline")
    catalog_baseline.add_argument("--run-id", required=True)
    catalog_baseline.add_argument("--output", type=Path, required=True)
    capture_manifest = subparsers.add_parser("capture-ui-manifest")
    capture_manifest.add_argument("--run-id", required=True)
    capture_manifest.add_argument("--batch-id", action="append", required=True)
    capture_manifest.add_argument("--catalog-baseline", type=Path)
    capture_manifest.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "snapshot":
        payload = snapshot(args.output)
    elif args.command == "plan-clean":
        payload = plan_clean_v3(args.output, args.manifest)
    elif args.command == "clean":
        payload = clean_v3(args.output, args.plan, args.private_result)
    elif args.command == "capture-ui-catalog-baseline":
        payload = _capture_test_ui_catalog_baseline(
            output=args.output,
            run_id=args.run_id,
        )
    else:
        payload = _capture_test_ui_manifest(
            output=args.output,
            run_id=args.run_id,
            batch_ids=args.batch_id,
            catalog_baseline=args.catalog_baseline,
        )
    print(json.dumps(safe_summary(payload), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
