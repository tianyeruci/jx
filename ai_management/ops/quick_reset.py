from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from knowledge_cleaner.dify import DifyKnowledgeClient

from ..config import get_settings
from ..courses import rebuild_snapshot
from ..db import Base, SessionLocal
from ..services.style_settings import STYLE_DEFAULTS, ensure_style_settings

PLAN_SCHEMA_VERSION = 1
PLAN_KIND = "jx_quick_test_environment_reset"
CONFIRMATION = "RESET_JX_TEST_ENVIRONMENT"

EXPECTED_TABLES = {
    "admin_setting",
    "ai_style_setting",
    "business_conversation",
    "business_conversation_segment",
    "business_message",
    "business_profile",
    "cleaning_batch",
    "cleaning_document",
    "cleaning_file",
    "course",
    "course_collection",
    "course_collection_member",
    "course_click_daily",
    "course_direction",
    "dify_publication_shard",
    "external_deletion_task",
    "learning_route",
    "learning_route_progress",
    "model_cleaning_task",
    "system_event_log",
}

REDIS_PATTERNS = (
    "ai:*",
    "celery-task-meta-*",
    "celery-taskset-meta-*",
)
CELERY_EXACT_KEYS = {
    "ai_default",
    "ai_cleaning",
    "ai_ocr",
    "ai_model_realtime",
    "ai_model_batch",
    "ai_publication",
    "_kombu.binding.ai_default",
    "_kombu.binding.ai_cleaning",
    "_kombu.binding.ai_ocr",
    "_kombu.binding.ai_model_realtime",
    "_kombu.binding.ai_model_batch",
    "_kombu.binding.ai_publication",
    "unacked",
    "unacked_index",
}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _table_counts(session: Session) -> dict[str, int]:
    actual = {table.name for table in Base.metadata.sorted_tables}
    if actual != EXPECTED_TABLES:
        missing = sorted(EXPECTED_TABLES - actual)
        unexpected = sorted(actual - EXPECTED_TABLES)
        raise RuntimeError(
            f"reset table contract changed; missing={missing}, unexpected={unexpected}"
        )
    return {
        table.name: int(session.scalar(select(func.count()).select_from(table)) or 0)
        for table in Base.metadata.sorted_tables
    }


def _dataset_clients() -> dict[str, DifyKnowledgeClient]:
    settings = get_settings()
    datasets = {
        "general": settings.dify_general_dataset_id,
        "course": settings.dify_course_dataset_id,
    }
    if any(not value for value in datasets.values()):
        raise RuntimeError("both configured Dify dataset IDs are required")
    if len(set(datasets.values())) != len(datasets):
        raise RuntimeError("general and course Dify datasets must be distinct")
    return {
        code: DifyKnowledgeClient(
            settings.knowledge_base_url,
            settings.dify_knowledge_api_key,
            dataset_id,
            timeout=60,
        )
        for code, dataset_id in datasets.items()
    }


def _document_ids(client: DifyKnowledgeClient) -> list[str]:
    identifiers: list[str] = []
    for item in client.iter_documents():
        document_id = str(item.get("id") or "").strip()
        if not document_id or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", document_id):
            raise RuntimeError("Dify returned an invalid or missing document ID")
        identifiers.append(document_id)
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("Dify returned duplicate document IDs")
    return sorted(identifiers)


def _dify_inventory(
    clients: dict[str, DifyKnowledgeClient] | None = None,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    owned_clients = clients is None
    clients = clients or _dataset_clients()
    try:
        documents = {code: _document_ids(client) for code, client in clients.items()}
        dataset_ids = {code: client.dataset_id for code, client in clients.items()}
        return documents, dataset_ids
    finally:
        if owned_clients:
            for client in clients.values():
                client.close()


def _redis_client() -> Redis:
    settings = get_settings()
    parsed = urlsplit(settings.redis_url)
    try:
        database = int((parsed.path or "/0").lstrip("/") or "0")
    except ValueError as error:
        raise RuntimeError("REDIS_URL has an invalid database number") from error
    if database != 5:
        raise RuntimeError("quick reset is restricted to the verified AI Redis DB 5")
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _redis_inventory(client: Redis) -> list[str]:
    keys: set[str] = set()
    for pattern in REDIS_PATTERNS:
        keys.update(str(value) for value in client.scan_iter(match=pattern))
    keys.update(key for key in CELERY_EXACT_KEYS if client.exists(key))
    if any(not key or "\n" in key or "\r" in key for key in keys):
        raise RuntimeError("Redis returned an invalid key")
    return sorted(keys)


def create_plan(
    *,
    private_plan: Path,
    safe_output: Path,
    session_factory: sessionmaker[Session] = SessionLocal,
    redis_client: Redis | None = None,
    dify_clients: dict[str, DifyKnowledgeClient] | None = None,
) -> dict[str, Any]:
    with session_factory() as session:
        database_counts = _table_counts(session)
    documents, dataset_ids = _dify_inventory(dify_clients)
    owned_redis = redis_client is None
    redis_client = redis_client or _redis_client()
    try:
        redis_keys = _redis_inventory(redis_client)
    finally:
        if owned_redis:
            redis_client.close()
    basis = {
        "database_counts": database_counts,
        "dify_documents": documents,
        "dify_dataset_ids": dataset_ids,
        "redis_keys": redis_keys,
    }
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "basis": basis,
        "basis_sha256": _canonical_hash(basis),
    }
    _write_private_json(private_plan, plan)
    safe = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "database_counts": database_counts,
        "dify_document_counts": {
            code: len(values) for code, values in documents.items()
        },
        "redis_key_count": len(redis_keys),
        "basis_sha256": plan["basis_sha256"],
    }
    _write_private_json(safe_output, safe)
    return safe


def _load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != PLAN_SCHEMA_VERSION
        or payload.get("kind") != PLAN_KIND
    ):
        raise RuntimeError("reset requires a quick-test environment plan")
    basis = payload.get("basis")
    if not isinstance(basis, dict):
        raise RuntimeError("reset plan has no basis")
    supplied = str(payload.get("basis_sha256") or "")
    if not supplied or not hmac.compare_digest(supplied, _canonical_hash(basis)):
        raise RuntimeError("reset plan integrity check failed")
    return payload


def _assert_plan_current(
    plan: dict[str, Any],
    *,
    session_factory: sessionmaker[Session],
    redis_client: Redis,
    dify_clients: dict[str, DifyKnowledgeClient],
) -> None:
    basis = plan["basis"]
    with session_factory() as session:
        if _table_counts(session) != basis["database_counts"]:
            raise RuntimeError("database changed after the reset plan was created")
    documents, dataset_ids = _dify_inventory(dify_clients)
    if documents != basis["dify_documents"] or dataset_ids != basis["dify_dataset_ids"]:
        raise RuntimeError("Dify datasets changed after the reset plan was created")
    if _redis_inventory(redis_client) != basis["redis_keys"]:
        raise RuntimeError("Redis changed after the reset plan was created")


def _delete_dify_documents(
    clients: dict[str, DifyKnowledgeClient],
    targets: dict[str, list[str]],
    journal_path: Path,
) -> dict[str, int]:
    deleted: dict[str, list[str]] = {code: [] for code in clients}
    for code, client in clients.items():
        for document_id in targets[code]:
            client.delete_document(document_id)
            deleted[code].append(document_id)
            _write_private_json(
                journal_path,
                {
                    "schema_version": 1,
                    "deleted_dify_documents": deleted,
                },
            )
    for _ in range(3):
        remaining = {code: _document_ids(client) for code, client in clients.items()}
        if not any(remaining.values()):
            return {code: len(values) for code, values in deleted.items()}
    raise RuntimeError("configured Dify datasets are not empty after exact deletion")


def _clear_database(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        _table_counts(session)
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        ensure_style_settings(session)
        if int(session.scalar(select(func.count()).select_from(Base.metadata.tables["ai_style_setting"])) or 0) != len(STYLE_DEFAULTS):
            raise RuntimeError("default AI style settings were not restored")
        return _table_counts(session)


def execute_plan(
    *,
    private_plan: Path,
    private_journal: Path,
    safe_output: Path,
    confirmation: str,
    session_factory: sessionmaker[Session] = SessionLocal,
    redis_client: Redis | None = None,
    dify_clients: dict[str, DifyKnowledgeClient] | None = None,
) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise RuntimeError("invalid reset confirmation")
    plan = _load_plan(private_plan)
    owned_redis = redis_client is None
    owned_dify = dify_clients is None
    redis_client = redis_client or _redis_client()
    dify_clients = dify_clients or _dataset_clients()
    try:
        _assert_plan_current(
            plan,
            session_factory=session_factory,
            redis_client=redis_client,
            dify_clients=dify_clients,
        )
        deleted_dify = _delete_dify_documents(
            dify_clients,
            plan["basis"]["dify_documents"],
            private_journal,
        )
        database_counts = _clear_database(session_factory)
        redis_keys = list(plan["basis"]["redis_keys"])
        deleted_redis = int(redis_client.delete(*redis_keys)) if redis_keys else 0
        if _redis_inventory(redis_client):
            raise RuntimeError("AI Redis keys remain after reset")
        with session_factory() as session:
            rebuild_snapshot(session)
            database_counts = _table_counts(session)
        expected_counts = {name: 0 for name in EXPECTED_TABLES}
        expected_counts["ai_style_setting"] = len(STYLE_DEFAULTS)
        if database_counts != expected_counts:
            raise RuntimeError("database is not at the expected empty test baseline")
        safe = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "database_counts": database_counts,
            "deleted_dify_document_counts": deleted_dify,
            "deleted_redis_key_count": deleted_redis,
            "basis_sha256": plan["basis_sha256"],
        }
        _write_private_json(safe_output, safe)
        return safe
    finally:
        if owned_redis:
            redis_client.close()
        if owned_dify:
            for client in dify_clients.values():
                client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--private-plan", type=Path, required=True)
    plan.add_argument("--safe-output", type=Path, required=True)
    execute = subparsers.add_parser("execute")
    execute.add_argument("--private-plan", type=Path, required=True)
    execute.add_argument("--private-journal", type=Path, required=True)
    execute.add_argument("--safe-output", type=Path, required=True)
    execute.add_argument("--confirmation", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "plan":
        result = create_plan(
            private_plan=args.private_plan,
            safe_output=args.safe_output,
        )
    else:
        result = execute_plan(
            private_plan=args.private_plan,
            private_journal=args.private_journal,
            safe_output=args.safe_output,
            confirmation=args.confirmation,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
