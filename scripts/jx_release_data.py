from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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

AI_DATA_MODELS = (
    BusinessProfile,
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    LearningRouteProgress,
    CourseClickDaily,
    CleaningBatch,
    CleaningFile,
    CleaningDocument,
    ExternalDeletionTask,
    SystemEventLog,
)
PRESERVED_MODELS = (Course, LearningRoute, AdminSetting, AIStyleSetting)


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
        "name": str(document.get("name") or ""),
        "enabled": bool(document.get("enabled", True)),
        "indexing_status": str(document.get("indexing_status") or ""),
        "metadata": _remote_metadata(document),
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


def snapshot(output: Path) -> dict[str, Any]:
    with SessionLocal() as session:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "configuration": _configuration_fingerprint(),
            "counts": _counts(session),
            "courses": [
                _row_dict(item)
                for item in session.scalars(select(Course).order_by(Course.course_id))
            ],
            "routes": [
                _row_dict(item)
                for item in session.scalars(
                    select(LearningRoute).order_by(LearningRoute.route_id)
                )
            ],
            "admin_settings": [
                _row_dict(item)
                for item in session.scalars(
                    select(AdminSetting).order_by(AdminSetting.key)
                )
            ],
            "style_settings": [
                _row_dict(item)
                for item in session.scalars(
                    select(AIStyleSetting).order_by(AIStyleSetting.code)
                )
            ],
            "cleaning_batches": [
                _row_dict(item)
                for item in session.scalars(
                    select(CleaningBatch).order_by(CleaningBatch.created_at)
                )
            ],
            "cleaning_files": [
                _row_dict(item)
                for item in session.scalars(
                    select(CleaningFile).order_by(CleaningFile.created_at)
                )
            ],
            "cleaning_documents": [
                _row_dict(item)
                for item in session.scalars(
                    select(CleaningDocument).order_by(
                        CleaningDocument.file_id,
                        CleaningDocument.sequence,
                    )
                )
            ],
        }
    payload["dify_documents"] = _dify_documents()
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
        raw = {"general": raw, "course": raw}
    if not isinstance(raw, dict):
        raise ValueError("manifest.dify_document_ids must be a dataset map or list")
    for code in result:
        values = raw.get(code) or []
        if not isinstance(values, list):
            raise ValueError(f"manifest.dify_document_ids.{code} must be a list")
        result[code] = {str(item) for item in values if str(item).strip()}
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
    associated = _local_associated_document_ids()
    candidates: dict[str, list[str]] = {"general": [], "course": []}
    reasons: dict[str, dict[str, list[str]]] = {"general": {}, "course": {}}
    preserved_unknown: dict[str, list[str]] = {"general": [], "course": []}
    for code, entries in documents.items():
        exact_ids = manifest_ids.get(code, set()) | associated.get(code, set())
        for document in entries:
            document_id = str(document.get("id") or "")
            if not document_id:
                continue
            metadata = document.get("metadata") or {}
            if _is_formal_course_document(metadata):
                preserved_unknown[code].append(document_id)
                continue
            if document_id in exact_ids:
                candidates[code].append(document_id)
                reasons[code][document_id] = []
                if document_id in manifest_ids.get(code, set()):
                    reasons[code][document_id].append("explicit_run_manifest")
                if document_id in associated.get(code, set()):
                    reasons[code][document_id].append("local_service_association")
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


def plan_clean(output: Path, manifest: Path | None = None) -> dict[str, Any]:
    with SessionLocal() as session:
        configuration = _configuration_fingerprint()
        counts = _counts(session)
    documents = _dify_documents()
    manifest_ids = _manifest_document_ids(manifest)
    candidates, reasons, preserved_unknown = _planned_document_candidates(
        documents, manifest_ids
    )
    basis = _plan_basis(configuration, counts, documents)
    plan = {
        "plan_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_fingerprint": _fingerprint(basis),
        "plan_fingerprint": _fingerprint(
            {"basis": basis, "candidate_ids": candidates, "manifest_ids": manifest_ids}
        ),
        "configuration": configuration,
        "counts": counts,
        "candidate_ids": candidates,
        "candidate_reasons": reasons,
        "preserved_unknown_document_ids": preserved_unknown,
        "manifest_fingerprint": _fingerprint(
            {code: sorted(values) for code, values in manifest_ids.items()}
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return plan


def _verified_clean_plan(plan_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[dict[str, Any]]]]:
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
    return plan, basis, documents


def clean(output: Path, plan_path: Path) -> dict[str, Any]:
    plan, basis, before_documents = _verified_clean_plan(plan_path)
    redis_guard = _redis_exclusivity_guard()
    candidates = {
        code: sorted(set(str(item) for item in plan["candidate_ids"].get(code, [])))
        for code in ("general", "course")
    }
    deleted_documents = _delete_planned_dify_documents(candidates)

    with SessionLocal() as session:
        before_counts = basis["counts"]
        extra_course_ids = sorted(
            set(session.scalars(select(Course.course_id))) - FORMAL_COURSE_IDS
        )
        extra_route_ids = sorted(
            set(session.scalars(select(LearningRoute.route_id))) - FORMAL_ROUTE_IDS
        )
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
        for model in (
            BusinessMessage,
            BusinessConversationSegment,
            BusinessConversation,
            BusinessProfile,
            LearningRouteProgress,
            CourseClickDaily,
            CleaningDocument,
            CleaningFile,
            CleaningBatch,
            ExternalDeletionTask,
            SystemEventLog,
        ):
            session.execute(delete(model))
        session.commit()
        _formal_guard(session)
        after_counts = _counts(session)

    redis_client = _redis_client()
    try:
        redis_inventory_before = _redis_key_inventory(redis_client)
        redis_client.flushdb()
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
        "redis_exclusivity": redis_guard,
        "plan_fingerprint": plan["plan_fingerprint"],
        "history_retention_days": history_retention_days,
        "style_setting_count": len(style_codes),
        "formal_course_count": len(FORMAL_COURSE_IDS),
        "formal_route_count": len(FORMAL_ROUTE_IDS),
        "formal_course_fingerprint": _fingerprint(sorted(FORMAL_COURSE_IDS)),
        "formal_route_fingerprint": _fingerprint(sorted(FORMAL_ROUTE_IDS)),
        "removed_extra_course_ids": extra_course_ids,
        "removed_extra_route_ids": extra_route_ids,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def safe_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if "courses" in payload:
        course_ids = {item["course_id"] for item in payload["courses"]}
        route_ids = {item["route_id"] for item in payload["routes"]}
        return {
            "counts": payload["counts"],
            "course_count": len(payload["courses"]),
            "route_count": len(payload["routes"]),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "plan-clean", "clean"):
        child = subparsers.add_parser(command)
        child.add_argument("--output", type=Path, required=True)
        if command == "plan-clean":
            child.add_argument("--manifest", type=Path)
        elif command == "clean":
            child.add_argument("--plan", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "snapshot":
        payload = snapshot(args.output)
    elif args.command == "plan-clean":
        payload = plan_clean(args.output, args.manifest)
    else:
        payload = clean(args.output, args.plan)
    print(json.dumps(safe_summary(payload), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
