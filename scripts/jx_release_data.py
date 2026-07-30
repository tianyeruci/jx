from __future__ import annotations

import argparse
import hashlib
import json
import re
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
TEST_MARKER = re.compile(r"(?:^|[^a-z])(test|synthetic|acceptance)(?:[^a-z]|$)|测试|验收|合成", re.I)

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


def _redis_keys() -> list[str]:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        return sorted(str(item) for item in client.scan_iter(match="*"))
    finally:
        client.close()


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
    payload["redis_keys"] = _redis_keys()
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


def _test_document_candidates(
    documents: dict[str, list[dict[str, Any]]],
) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {"general": [], "course": []}
    for code, entries in documents.items():
        for document in entries:
            metadata = document.get("metadata") or {}
            if _is_formal_course_document(metadata):
                continue
            course_id = str(metadata.get("course_id") or "")
            route_id = str(metadata.get("route_id") or "")
            if (course_id and course_id not in FORMAL_COURSE_IDS) or (
                route_id and route_id not in FORMAL_ROUTE_IDS
            ):
                candidates[code].append(str(document.get("id") or ""))
                continue
            evidence = " ".join(
                [
                    str(document.get("name") or ""),
                    *(f"{key}={value}" for key, value in metadata.items()),
                ]
            )
            if TEST_MARKER.search(evidence):
                candidates[code].append(str(document.get("id") or ""))
    return {
        code: sorted(set(document_id for document_id in ids if document_id))
        for code, ids in candidates.items()
    }


def _delete_test_dify_documents(
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


def clean(output: Path) -> dict[str, Any]:
    before_documents = _dify_documents()
    candidates = _test_document_candidates(before_documents)
    remote_course_ids = {
        str(item.get("id") or "") for item in before_documents["course"]
    }
    candidates["course"] = sorted(
        set(
            [
                *candidates["course"],
                *(
                    document_id
                    for document_id in _local_nonformal_catalog_document_ids()
                    if document_id in remote_course_ids
                ),
            ]
        )
    )
    deleted_documents = _delete_test_dify_documents(candidates)

    with SessionLocal() as session:
        before_counts = _counts(session)
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

    settings = get_settings()
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        redis_size_before = int(redis_client.dbsize())
        redis_client.flushdb()
    finally:
        redis_client.close()

    with SessionLocal() as session:
        rebuild_snapshot(session)
        history_retention_days = get_history_retention_days(session)
        style_codes = list(session.scalars(select(AIStyleSetting.code)))
        for code in style_codes:
            get_style_prompt(session, code)

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        redis_size_after = int(redis_client.dbsize())
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
        "redis_size_before": redis_size_before,
        "redis_size_after": redis_size_after,
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
            "redis_key_count": len(payload.get("redis_keys", [])),
            "output_fingerprint": _fingerprint(payload),
        }
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "clean"):
        child = subparsers.add_parser(command)
        child.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = snapshot(args.output) if args.command == "snapshot" else clean(args.output)
    print(json.dumps(safe_summary(payload), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
