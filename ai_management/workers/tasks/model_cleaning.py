from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import object_session, selectinload

from knowledge_cleaner.chunking import prepare_chunks
from knowledge_cleaner.consultation_rag import (
    ConsultationRagResponse,
    render_methodology_candidate,
    topic_for_problem_category,
    validate_consultation_rag_response,
)
from knowledge_cleaner.consultation_rag import (
    render_case_markdown as render_consultation_rag_case,
)

from ...cache import cache
from ...config import get_settings
from ...course_import import _course_text, _route_text
from ...db import SessionLocal
from ...model_cleaning import (
    BailianClient,
    CourseEnrichmentResponse,
    ModelNoResponseError,
    PromptRequest,
    batch_request_line,
    build_custom_id,
    build_prompt_messages,
    choose_call_mode,
    get_provider_batch_ids,
    validate_consultation_response,
    validate_semantic_response,
)
from ...model_rate_limit import model_rate_limiter
from ...models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    DifyPublicationShard,
    ExternalDeletionTask,
    ModelCleaningTask,
    utcnow,
)
from ...publication_shards import ShardItem, plan_post_shards
from ...sanitization import sanitize_for_knowledge
from ...schemas import CourseRecord, RouteRecord
from ...services.bailian_cleanup import (
    clear_successful_bailian_file_tasks,
    ensure_bailian_cleanup_tasks,
)
from ...services.dify_references import has_other_live_dify_reference
from ...services.observability import exception_details, record_event, safe_exception_message
from ..celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()
POST_STRATEGIES = {
    "wechat_public_account_cognition",
    "zsxq_post",
    "xiaobot_post",
}


def _methodology_document_key(document_key: str, case_payload: dict[str, Any]) -> str:
    """Bind a candidate key to the validated case, not its transient position."""

    fingerprint = hashlib.sha256(
        json.dumps(case_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"{document_key}:methodology:{fingerprint}"


_SAFE_CASE_GROUP_ID_RE = re.compile(r"case_[0-9a-f]{16}")


def _safe_case_group_id(document: CleaningDocument) -> str:
    """Return a display-safe group identifier without trusting legacy metadata."""

    existing = str((document.metadata_json or {}).get("case_id") or "").lower()
    if _SAFE_CASE_GROUP_ID_RE.fullmatch(existing):
        return existing
    content_sha = str(document.content_sha256 or "").lower()
    if re.fullmatch(r"[0-9a-f]{16,64}", content_sha):
        return f"case_{content_sha[:16]}"
    stable_local_key = f"{document.file_id or ''}:{document.document_key or ''}"
    return f"case_{hashlib.sha256(stable_local_key.encode('utf-8')).hexdigest()[:16]}"


CONTENT_MODEL_STRATEGIES = frozenset(
    {
        "personal_business_consultation",
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
    }
)
PROVIDER_TERMINAL = {"completed", "failed", "expired", "cancelled"}
# Dify requires a 50-unit minimum for custom segments.  Keep a conservative
# buffer after sanitization and title/context assembly.
_DIFY_MIN_CUSTOM_SEGMENT_CHARS = 120
_DIFY_MIN_SOURCE_SEGMENT_CHARS = 8
_MODEL_REVIEW_ARTIFACT_MAX_BYTES = 16 * 1024 * 1024


class _ZeroCoreModelOutputError(ValueError):
    """A model response contained no publishable core knowledge unit."""


class _ModelCallLimitExceeded(RuntimeError):
    """A source item has already consumed its two provider-call slots."""


class _ModelBudgetExceeded(RuntimeError):
    """A provider call would exceed its current daily or test-lifetime cap."""


class _ModelDailyBudgetExceeded(_ModelBudgetExceeded):
    """A provider call would exceed the configured Asia/Shanghai daily cap."""


class _ModelAcceptanceBudgetExceeded(_ModelBudgetExceeded):
    """A provider call would exceed the isolated test's lifetime cap."""


class _ModelBudgetLockUnavailable(RuntimeError):
    """A paid request must not proceed without the distributed budget lock."""


class _ModelLeaseLost(RuntimeError):
    """A redelivered worker no longer owns this model task's side effects."""


_MAX_PROVIDER_CALLS_PER_DOCUMENT = 2
_MODEL_LEASE_DURATION = timedelta(minutes=30)


def _assert_active_model_lease(session, task_id: str, lease_token: str) -> ModelCleaningTask:
    """Fence realtime writes behind the currently held task lease."""

    task = session.scalar(
        select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
    )
    if task is None:
        raise _ModelLeaseLost("模型任务已不存在")
    session.refresh(task)
    now = datetime.now(timezone.utc)
    expires_at = task.lease_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        task.status != "running"
        or task.lease_token != lease_token
        or expires_at is None
        or expires_at <= now
    ):
        raise _ModelLeaseLost("模型任务 lease 已失效，停止旧 Worker 副作用")
    return task


def _renew_active_model_lease(
    session,
    task_id: str,
    lease_token: str,
) -> ModelCleaningTask:
    """Renew only the current Worker lease before an external side effect."""

    task = _assert_active_model_lease(session, task_id, lease_token)
    task.lease_expires_at = datetime.now(timezone.utc) + _MODEL_LEASE_DURATION
    session.flush()
    return task


def _raise_if_model_lease_lost(exc: Exception) -> None:
    """Keep fencing failures out of semantic-repair/fallback error handling."""

    if isinstance(exc, _ModelLeaseLost):
        raise exc


def _normalized_publication_digest(markdown: str) -> str:
    from ...cleaning import normalized_publication_content_sha256

    return normalized_publication_content_sha256(markdown)


def _existing_shard_publication_digest(shard: DifyPublicationShard) -> str:
    """Use normalized final Markdown; unknown legacy digest domains do not match."""

    if shard.markdown:
        return _normalized_publication_digest(shard.markdown)
    return ""


def _shard_remote_is_live(
    session,
    *,
    client: Any,
    shard: DifyPublicationShard,
) -> bool:
    """Verify one precise historical shard before reusing it.

    A local ``succeeded`` shard is only provenance.  Reuse requires the exact
    remote ID, completed/enabled state, controlled metadata and the persisted
    parent/child content witness.  Known remote invalidation permits a precise
    replacement; read uncertainty remains retryable and must not blind-write.
    """

    from ...cleaning import STRATEGY_REGISTRY
    from .cleaning import (
        RemoteDuplicateVerificationError,
        _project_dify_metadata,
        _remote_metadata_value,
    )

    document_id = str(shard.dify_document_id or "").strip()
    if not document_id:
        return False
    try:
        remote = client.get_document(document_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            shard.remote_deleted_at = utcnow()
            session.flush()
            return False
        raise RemoteDuplicateVerificationError("Dify分片重复成果读取失败") from exc
    except Exception as exc:
        raise RemoteDuplicateVerificationError("Dify分片重复成果读取未确认") from exc
    if not isinstance(remote, dict):
        raise RemoteDuplicateVerificationError("Dify分片重复成果响应格式无效")
    remote_id = str(remote.get("id") or remote.get("document_id") or "").strip()
    if remote_id and remote_id != document_id:
        return False
    expected_digest = str((shard.metadata_json or {}).get("publication_content_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or expected_digest != str(
        shard.content_sha256 or ""
    ):
        return False
    if _remote_metadata_value(remote, "publication_content_sha256") != expected_digest:
        return False
    indexing_status = str(remote.get("indexing_status") or "").strip().lower()
    if (
        indexing_status != "completed"
        or remote.get("enabled") is False
        or remote.get("is_enabled") is False
    ):
        return False
    expected_witness = str(
        (shard.metadata_json or {}).get("remote_content_witness_sha256") or ""
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_witness):
        return False
    try:
        actual_witness = str(client.document_content_witness(document_id)).strip()
    except Exception as exc:
        raise RemoteDuplicateVerificationError("Dify分片重复成果正文见证未确认") from exc
    if actual_witness != expected_witness:
        return False
    strategy = STRATEGY_REGISTRY.get(shard.file.batch.strategy_code)
    expected_metadata = _project_dify_metadata(dict(shard.metadata_json or {}), strategy=strategy)
    try:
        client.verify_document_metadata(document_id, expected_metadata)
    except Exception as exc:
        # This client message is emitted after a successful read proves a
        # controlled metadata mismatch. It is remote invalidation, rather than
        # a transport uncertainty, and permits a precise replacement.
        if "Dify metadata 回读不一致" in str(exc):
            return False
        raise RemoteDuplicateVerificationError("Dify分片重复成果元数据未确认") from exc
    return True


def _source_fallback_enabled(document: CleaningDocument, strategy_code: str) -> bool:
    from ...cleaning import source_markdown_fallback_enabled

    return source_markdown_fallback_enabled(
        strategy_code,
        document.metadata_json or {},
    )


def _model_requires_source_fallback(
    document: CleaningDocument,
    strategy_code: str,
) -> bool:
    if document.status == "excluded":
        return False
    # Content-model failures are terminal/retryable states only.  The new
    # tolerance contract never turns an untrusted source Markdown snapshot
    # into a review publication candidate.
    if strategy_code in CONTENT_MODEL_STRATEGIES:
        return False
    if document.status in {"failed", "review_required"}:
        return True
    audit = document.audit_json or {}
    if audit.get("source_markdown_fallback_blocked") is True:
        return False
    model_result = audit.get("model_result") or {}
    disposition = str(model_result.get("disposition") or "").lower()
    return disposition in {"review", "review_required"} or bool(audit.get("review_original_error"))


def _apply_source_markdown_fallback(
    document: CleaningDocument,
    strategy_code: str,
    *,
    source_markdown: str,
    source_metadata: dict[str, Any],
    source_chunking: dict[str, Any],
) -> bool:
    """Persist a review artifact and stop; never auto-publish source text.

    The function name is retained because older worker call sites use it.  A
    contract failure is a human decision boundary now: the source Markdown is
    kept on the local document for an eventual explicit approval, but its
    status cannot be changed to a publishable state here.
    """

    if not source_markdown.strip() or not _model_requires_source_fallback(
        document,
        strategy_code,
    ):
        audit = dict(document.audit_json or {})
        audit.pop("_model_review_payload", None)
        audit.pop("_model_review_request", None)
        document.audit_json = audit
        document.__dict__.pop("_model_review_payload_for_artifact", None)
        document.__dict__.pop("_model_review_request_for_artifact", None)
        return False
    from ...cleaning import preserve_review_reason
    from .cleaning import _content_sha

    original_code = document.error_code
    original_message = document.error_message
    model_payload = document.__dict__.get("_model_review_payload_for_artifact")
    audit = preserve_review_reason(
        document.audit_json,
        original_code,
        original_message,
    )
    model_request = document.__dict__.get("_model_review_request_for_artifact")
    if model_request is None:
        model_request = audit.get("_model_review_request")
    artifact_error = ""
    try:
        artifact_path = _save_model_review_artifact(
            document,
            strategy_code=strategy_code,
            source_markdown=source_markdown,
            source_metadata=source_metadata,
            model_payload=model_payload,
            request_context=model_request,
        )
    except Exception as exc:
        # A bounded/permission failure must not leave a model task stuck in
        # validating.  Fail closed into the same human-review gate while
        # removing transient raw payload/context from the local audit row.
        artifact_path = ""
        artifact_error = safe_exception_message(exc, limit=500)
    audit["source_markdown_fallback"] = {
        "applied": False,
        "review_required": True,
        "reason_code": original_code or "MODEL_REVIEW_OR_FAILURE",
        "model_output_ignored": True,
        "artifact_path": artifact_path,
    }
    if artifact_error:
        audit["source_markdown_fallback"]["artifact_write_error"] = artifact_error
    audit.pop("_model_review_payload", None)
    audit.pop("_model_review_request", None)
    document.__dict__.pop("_model_review_payload_for_artifact", None)
    document.__dict__.pop("_model_review_request_for_artifact", None)
    # Keep the source candidate locally so an explicit approve action can
    # publish it. The review status remains a hard gate until that action.
    document.cleaned_markdown = source_markdown
    document.metadata_json = dict(source_metadata)
    document.chunking_json = dict(source_chunking)
    document.content_sha256 = _content_sha(
        source_markdown,
        document.metadata_json,
        document.chunking_json,
    )
    document.audit_json = audit
    document.status = "review_required"
    document.stage = "review"
    document.error_code = original_code or "MODEL_CONTRACT_INVALID"
    document.error_message = original_message or "模型结果未通过契约校验，需要人工审批"
    document.retryable = False
    record_event(
        level="WARNING",
        category="task",
        component="model_cleaning_worker",
        event_code="CLEANING_SOURCE_MARKDOWN_FALLBACK",
        status="review_required",
        message="模型结果未采用，已保存审核材料并转人工审批；未自动入库",
        resource_type="cleaning_document",
        resource_id=str(document.id or ""),
        details={
            "strategy_code": strategy_code,
            "reason_code": audit["source_markdown_fallback"]["reason_code"],
            "model_output_ignored": True,
        },
        session=object_session(document),
    )
    return False


def _review_artifact_filename(strategy_code: str, original_filename: str) -> str:
    from ...cleaning import normalized_original_filename

    safe = normalized_original_filename(original_filename)
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._（）()\[\]【】 -]", "_", safe)
    return f"{strategy_code}+{safe[:220]}.json"


def _save_model_review_artifact(
    document: CleaningDocument,
    *,
    strategy_code: str,
    source_markdown: str,
    source_metadata: dict[str, Any],
    model_payload: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
) -> str:
    """Write an internal, bounded review artifact without leaking source to logs."""

    file_row = getattr(document, "file", None)
    original_filename = str(
        getattr(file_row, "original_filename", "")
        or source_metadata.get("source_filename")
        or document.title
        or "未命名文件"
    )
    root = Path(settings.audit_root) / "model-review"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    target = root / _review_artifact_filename(strategy_code, original_filename)
    audit = document.audit_json or {}
    payload = (
        model_payload
        if model_payload is not None
        else getattr(document, "_model_review_payload_for_artifact", None)
    )
    if payload is None:
        payload = audit.get("_model_review_payload")
    request_fingerprint_source = (
        request_context
        or audit.get("_model_review_request")
        or {
            "title": document.title,
            "metadata": source_metadata,
            "source_sha256": document.content_sha256,
        }
    )
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    entry = {
        "strategy_code": strategy_code,
        "original_filename": original_filename,
        "document_id": str(document.id or ""),
        "item_sequence": int(document.sequence or 0),
        "request_sha256": hashlib.sha256(
            json.dumps(
                request_fingerprint_source,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
        "response_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        "repair_hint_sha256": str(request_fingerprint_source.get("repair_hint_sha256") or ""),
        "model_result": payload,
        "source_markdown_sha256": hashlib.sha256(source_markdown.encode("utf-8")).hexdigest(),
        "error": {
            "code": document.error_code or "MODEL_CONTRACT_INVALID",
            "message": str(document.error_message or "模型结果未通过契约校验")[:500],
        },
        "validation_summary": audit.get("review_original_error") or {},
    }
    lock_key = "ai:model-review-artifact:" + hashlib.sha256(str(target).encode("utf-8")).hexdigest()
    with cache.lock(lock_key, ttl=120) as acquired:
        if not acquired:
            raise RuntimeError("模型审核材料正在写入，请稍后重试")
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
            entries = list(existing.get("entries") or []) if isinstance(existing, dict) else []
        else:
            entries = []
        entries = [item for item in entries if item.get("document_id") != entry["document_id"]]
        entries.append(entry)
        artifact = {
            "artifact_version": 1,
            "strategy_code": strategy_code,
            "original_filename": original_filename,
            "entries": entries,
        }
        serialized = json.dumps(
            artifact,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        if len(serialized.encode("utf-8")) > _MODEL_REVIEW_ARTIFACT_MAX_BYTES:
            raise ValueError("模型审核材料超过单文件大小上限")
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(
                str(temporary),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(serialized)
            temporary.chmod(0o600)
            os.replace(temporary, target)
            target.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)
    return str(target)


def _client() -> BailianClient:
    return BailianClient(
        api_key=settings.bailian_api_key,
        base_url=settings.bailian_base_url,
        timeout_seconds=180,
    )


def _documents_for_batch(session, batch_id: str) -> list[CleaningDocument]:
    return list(_iter_documents_for_batch(session, batch_id))


def _document_statement(batch_id: str):
    return (
        select(CleaningDocument)
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningFile.batch_id == batch_id,
            CleaningDocument.status.in_(("awaiting_model", "model_processing")),
        )
        .order_by(
            CleaningFile.created_at,
            CleaningDocument.sequence,
            CleaningDocument.id,
        )
    )


def _iter_documents_for_batch(
    session,
    batch_id: str,
) -> Iterable[CleaningDocument]:
    return session.scalars(_document_statement(batch_id).execution_options(yield_per=500))


def _iter_realtime_document_pages(
    session,
    batch_id: str,
    *,
    page_size: int = 100,
) -> Iterable[list[CleaningDocument]]:
    """Yield bounded realtime work pages so a large batch is resumable."""

    last_id = ""
    while True:
        statement = (
            select(CleaningDocument)
            .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
            .where(
                CleaningFile.batch_id == batch_id,
                CleaningDocument.status == "awaiting_model",
                CleaningDocument.id > last_id,
            )
            .order_by(CleaningDocument.id)
            .limit(page_size)
        )
        page = list(session.scalars(statement))
        if not page:
            return
        yield page
        last_id = page[-1].id


def _request(document: CleaningDocument, strategy_code: str) -> PromptRequest:
    blocks = (document.audit_json or {}).get("model_blocks") or {}
    if not isinstance(blocks, dict) or not blocks:
        raise ValueError("模型清洗条目缺少来源块")
    safe_blocks = {
        str(key): str(value)
        for key, value in blocks.items()
        if str(key).strip() and str(value).strip()
    }
    return PromptRequest(
        strategy_code=strategy_code,
        item_id=document.id,
        content_sha256=document.content_sha256,
        title=document.title,
        blocks=safe_blocks,
    )


def _estimated_tokens(request: PromptRequest) -> int:
    total_chars = len(request.title) + sum(
        len(key) + len(value) for key, value in request.blocks.items()
    )
    return max(1, (total_chars + 1) // 2)


def _estimated_provider_input_tokens(request: PromptRequest, *, repair_hint: str = "") -> int:
    """Return a provider-agnostic upper bound for the complete prompt.

    UTF-8 bytes bound byte-pair/tokenizer input from above, unlike character
    counts for Chinese.  A small per-message envelope also reserves provider
    chat framing that is not present in the visible content string.
    """

    messages = build_prompt_messages(request, repair_hint=repair_hint)
    total_bytes = sum(
        len(str(message.get("content") or "").encode("utf-8")) + 16 for message in messages
    )
    return max(1, total_bytes)


def _provider_budget_entries(task: ModelCleaningTask) -> list[tuple[str, Decimal]]:
    """Read the effective cost for individually durable provider calls."""

    raw_entries = (task.result_counts or {}).get("_provider_budget_reservations")
    entries: list[tuple[str, Decimal]] = []
    if not isinstance(raw_entries, list):
        return entries
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or "")
        try:
            cost = Decimal(str(entry.get("cost_yuan") or "0"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) and cost.is_finite() and cost >= 0:
            entries.append((date, cost))
    return entries


def _provider_budget_ledger_is_corrupt(
    task: ModelCleaningTask,
    *,
    valid_document_ids: set[str] | None = None,
) -> bool:
    """Reject incomplete call accounting instead of silently undercounting it.

    Pre-call ledger entries did not have an id or an immutable pricing
    snapshot.  They cannot be reconciled to an exact provider response, so
    they deliberately fail closed rather than being silently upgraded in
    place.  New reservations carry every field needed to settle one realtime
    response without touching historical task data.
    """

    raw_entries = (task.result_counts or {}).get("_provider_budget_reservations")
    if not isinstance(raw_entries, list):
        return False
    seen_call_ids: set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, dict):
            return True
        call_id = str(entry.get("call_id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", call_id) or call_id in seen_call_ids:
            return True
        seen_call_ids.add(call_id)
        date = str(entry.get("date") or "")
        try:
            cost = Decimal(str(entry.get("cost_yuan") or ""))
            reserved_cost = Decimal(str(entry.get("reserved_cost_yuan") or ""))
        except (InvalidOperation, TypeError, ValueError):
            return True
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
            or not cost.is_finite()
            or cost < 0
            or not reserved_cost.is_finite()
            or reserved_cost < 0
            or not str(entry.get("document_id") or "")
            or str(entry.get("transport_mode") or "") not in {"realtime", "batch"}
        ):
            return True
        if valid_document_ids is not None and str(entry["document_id"]) not in valid_document_ids:
            return True
        actual_cost = entry.get("actual_cost_yuan")
        if actual_cost is not None:
            try:
                actual_cost_decimal = Decimal(str(actual_cost))
            except (InvalidOperation, TypeError, ValueError):
                return True
            if not actual_cost_decimal.is_finite() or actual_cost_decimal < 0:
                return True
            if cost != actual_cost_decimal:
                return True
        for price_key in (
            "input_price_per_million",
            "output_price_per_million",
            "price_discount",
        ):
            try:
                price = Decimal(str(entry.get(price_key) or ""))
            except (InvalidOperation, TypeError, ValueError):
                return True
            if not price.is_finite() or price < 0:
                return True
    return False


def merge_result_counts_preserving_provider_ledger(
    task: ModelCleaningTask, result_counts: dict[str, Any]
) -> dict[str, Any]:
    """Replace transient counters without discarding durable call reservations.

    ``result_counts`` predates provider accounting and is still used for
    display counters in several task transitions.  Keep the ledger in one
    place so a successful completion, a repair round, or an exact retry
    cannot silently recreate budget headroom.
    """

    # Reservation writes are a partial transition.  Start from the complete
    # locked snapshot so they cannot erase candidate IDs, batch-generation
    # fences, terminal batches, or future durable fields written by another
    # lifecycle transition in the same task.
    merged = dict(task.result_counts or {})
    merged.update(result_counts)
    return merged


def _derived_document_audit(parent_audit: dict[str, Any] | None, **derived: Any) -> dict[str, Any]:
    """Keep provenance on a derived child without inheriting provider calls."""
    audit = dict(parent_audit or {})
    for key in tuple(audit):
        if key == "model_attempts" or key == "model_blocks" or key.startswith("_model_"):
            audit.pop(key, None)
    audit["model_attempts"] = 0
    audit.update(derived)
    return audit


def model_attempt_count_for_document(
    document: CleaningDocument,
    task: ModelCleaningTask,
    *,
    valid_document_ids: set[str] | None = None,
) -> int:
    """Return a durable attempt count or fail closed for ambiguous history."""

    audit = dict(document.audit_json or {})
    raw_attempts = audit.get("model_attempts")
    reservations = (task.result_counts or {}).get("_provider_budget_reservations")
    document_reservations: list[dict[str, Any]] = []
    if _provider_budget_ledger_is_corrupt(task, valid_document_ids=valid_document_ids):
        raise _ModelCallLimitExceeded("模型调用账本损坏，拒绝自动重试")
    if isinstance(reservations, list):
        for entry in reservations:
            if not isinstance(entry, dict):
                continue
            entry_document_id = str(entry.get("document_id") or "")
            if entry_document_id == str(document.id):
                document_reservations.append(entry)

    if raw_attempts is None:
        # A new task explicitly seeds zero.  A missing historical value may
        # only be reconstructed from document-specific durable reservations.
        if not document_reservations:
            raise _ModelCallLimitExceeded("模型调用次数记录不完整，拒绝自动重试")
        attempted = len(document_reservations)
    elif isinstance(raw_attempts, bool) or not isinstance(raw_attempts, int):
        raise _ModelCallLimitExceeded("模型调用次数记录无效，拒绝自动重试")
    else:
        attempted = raw_attempts

    if attempted < 0 or attempted > _MAX_PROVIDER_CALLS_PER_DOCUMENT:
        raise _ModelCallLimitExceeded("模型调用次数记录超出允许范围，拒绝自动重试")
    if len(document_reservations) != attempted:
        raise _ModelCallLimitExceeded("模型调用账本与次数记录冲突，拒绝自动重试")
    return attempted


def _task_provider_ledger_is_consistent(
    session,
    task: ModelCleaningTask,
    *,
    valid_document_ids: set[str],
) -> bool:
    """Require every task-batch document to reconcile to this task's ledger."""

    if _provider_budget_ledger_is_corrupt(task, valid_document_ids=valid_document_ids):
        return False
    documents = list(
        session.scalars(
            select(CleaningDocument)
            .join(CleaningFile)
            .where(CleaningFile.batch_id == task.batch_id)
        )
    )
    try:
        for candidate in documents:
            model_attempt_count_for_document(
                candidate,
                task,
                valid_document_ids=valid_document_ids,
            )
    except _ModelCallLimitExceeded:
        return False
    return True


def _task_has_legacy_provider_reservation(task: ModelCleaningTask) -> bool:
    """Conservatively retain pre-ledger task cost without mutating history."""

    if _provider_budget_entries(task):
        return False
    usage = task.actual_usage or {}
    if any(int(usage.get(key) or 0) > 0 for key in ("input_tokens", "output_tokens")):
        return True
    if task.status in {"batch_uploading", "batch_running", "validating", "completed", "failed"}:
        return True
    return task.status == "cancelled" and bool(
        get_provider_batch_ids(task) or task.provider_input_file_ids
    )


def _provider_call_cost(
    task: ModelCleaningTask,
    request: PromptRequest,
    *,
    repair_hint: str = "",
    transport_mode: str | None = None,
) -> Decimal:
    """Reserve the capped upper cost for this one provider request."""

    _, _, _, input_price, output_price, discount = _price_snapshot(transport_mode or task.mode)
    input_tokens = _estimated_provider_input_tokens(request, repair_hint=repair_hint)
    return (
        (
            Decimal(input_tokens) * Decimal(str(input_price))
            + Decimal(settings.cleaning_model_max_tokens) * Decimal(str(output_price))
        )
        / Decimal("1000000")
        * Decimal(str(discount))
    )


def _provider_actual_call_cost(entry: dict[str, Any], item_usage: dict[str, Any]) -> Decimal | None:
    """Price one returned realtime response with its reservation snapshot.

    A missing or malformed provider usage record is not interpreted as zero:
    the reservation remains outstanding and continues to protect the budget.
    """

    try:
        input_tokens = int(item_usage.get("input_tokens"))
        output_tokens = int(item_usage.get("output_tokens"))
        input_price = Decimal(str(entry["input_price_per_million"]))
        output_price = Decimal(str(entry["output_price_per_million"]))
        discount = Decimal(str(entry["price_discount"]))
    except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation):
        return None
    if (
        input_tokens < 0
        or output_tokens < 0
        or (input_tokens == 0 and output_tokens == 0)
        or not input_price.is_finite()
        or not output_price.is_finite()
        or not discount.is_finite()
        or input_price < 0
        or output_price < 0
        or discount < 0
    ):
        return None
    return (
        (Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price)
        / Decimal("1000000")
        * discount
    )


def _reserve_provider_call(
    session,
    task: ModelCleaningTask,
    document: CleaningDocument,
    request: PromptRequest,
    *,
    repair_hint: str = "",
    transport_mode: str | None = None,
    lease_token: str | None = None,
) -> str:
    """Atomically reserve one document slot and its actual-call-day budget.

    The task-level planning estimate is only an admission check.  Each real
    provider call records its own durable reservation immediately before
    transport, so a cross-midnight retry, cancellation or worker redelivery
    cannot borrow an earlier day's budget or silently create a third call.
    """

    from zoneinfo import ZoneInfo

    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with cache.lock(f"ai:model-cleaning-daily-budget:{day}", ttl=180) as lock_state:
        if not lock_state or not lock_state.available or not lock_state.acquired:
            raise _ModelBudgetLockUnavailable("模型预算锁不可用，未发起模型调用")
        locked_task = session.scalar(
            select(ModelCleaningTask).where(ModelCleaningTask.id == task.id).with_for_update()
        )
        locked_document = session.scalar(
            select(CleaningDocument).where(CleaningDocument.id == document.id).with_for_update()
        )
        if locked_task is None or locked_document is None:
            raise ValueError("模型调用目标不存在")
        if lease_token is not None:
            session.refresh(locked_task)
            now = datetime.now(timezone.utc)
            expires_at = locked_task.lease_expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if (
                locked_task.status != "running"
                or locked_task.lease_token != lease_token
                or expires_at is None
                or expires_at <= now
            ):
                raise _ModelLeaseLost("模型任务 lease 已失效，未发起模型调用")
        valid_document_ids = set(
            session.scalars(
                select(CleaningDocument.id)
                .join(CleaningFile)
                .where(CleaningFile.batch_id == locked_task.batch_id)
            )
        )
        if str(locked_document.id) not in valid_document_ids:
            raise _ModelCallLimitExceeded("模型调用条目不属于当前任务批次，拒绝自动调用")
        if not _task_provider_ledger_is_consistent(
            session,
            locked_task,
            valid_document_ids=valid_document_ids,
        ):
            raise _ModelCallLimitExceeded("模型调用账本与任务条目不一致，拒绝自动调用")
        audit = dict(locked_document.audit_json or {})
        attempted = model_attempt_count_for_document(
            locked_document,
            locked_task,
            valid_document_ids=valid_document_ids,
        )
        if attempted >= _MAX_PROVIDER_CALLS_PER_DOCUMENT:
            raise _ModelCallLimitExceeded("单个条目的模型调用已达到两次上限")

        effective_transport_mode = transport_mode or locked_task.mode
        (
            _price_version,
            _price_effective_date,
            _price_valid_until,
            input_price,
            output_price,
            discount,
        ) = _price_snapshot(effective_transport_mode)
        input_tokens = _estimated_provider_input_tokens(request, repair_hint=repair_hint)
        call_cost = (
            (
                Decimal(input_tokens) * Decimal(str(input_price))
                + Decimal(settings.cleaning_model_max_tokens) * Decimal(str(output_price))
            )
            / Decimal("1000000")
            * Decimal(str(discount))
        )
        daily_budget = Decimal(str(settings.cleaning_daily_budget_yuan))
        if _daily_model_reserved_cost(session) + call_cost > daily_budget:
            raise _ModelDailyBudgetExceeded("模型调用会超过当日预算")
        if settings.cleaning_general_only:
            lifetime_budget = Decimal(str(settings.acceptance_cleaning_budget_max_yuan))
            if _cumulative_model_reserved_cost(session) + call_cost > lifetime_budget:
                raise _ModelAcceptanceBudgetExceeded("模型调用会超过隔离测试累计预算")

        entries = list((locked_task.result_counts or {}).get("_provider_budget_reservations") or [])
        call_id = uuid.uuid4().hex
        entries.append(
            {
                "call_id": call_id,
                "date": day,
                "cost_yuan": f"{call_cost:.8f}",
                "reserved_cost_yuan": f"{call_cost:.8f}",
                "transport_mode": effective_transport_mode,
                "document_id": str(locked_document.id),
                "input_price_per_million": str(input_price),
                "output_price_per_million": str(output_price),
                "price_discount": str(discount),
            }
        )
        locked_task.result_counts = merge_result_counts_preserving_provider_ledger(
            locked_task, {"_provider_budget_reservations": entries}
        )
        audit["model_attempts"] = attempted + 1
        locked_document.audit_json = audit
        session.commit()
        return call_id


def _wait_for_realtime_capacity(
    request: PromptRequest,
    *,
    model: str,
    repair_hint: str = "",
    before_request: Callable[[], None] | None = None,
) -> None:
    """Wait for rate capacity before reserving money or sending a request."""

    token_cost = (
        _estimated_provider_input_tokens(
            request,
            repair_hint=repair_hint,
        )
        + settings.cleaning_model_max_tokens
    )
    while True:
        wait_seconds = model_rate_limiter.acquire(
            model=model,
            token_cost=token_cost,
        )
        if wait_seconds <= 0:
            break
        time.sleep(min(60, wait_seconds))
        if before_request is not None:
            before_request()

    # The acquisition result becomes stale while this Worker waits.  The
    # caller renews/fences immediately before it reserves this provider call.
    if before_request is not None:
        before_request()


def _realtime_chat(
    client: BailianClient,
    request: PromptRequest,
    *,
    model: str,
    repair_hint: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Send a request only after the caller has completed its lease/budget gate."""

    return client.chat_json(
        build_prompt_messages(request, repair_hint=repair_hint),
        model=model,
        max_tokens=settings.cleaning_model_max_tokens,
    )


def _persist_realtime_usage(
    session,
    task: ModelCleaningTask,
    usage: dict[str, int],
    item_usage: dict[str, Any],
    *,
    call_id: str | object,
    lease_token: str | None = None,
) -> None:
    """Settle one durable realtime reservation before semantic handling."""

    if lease_token is not None:
        _assert_active_model_lease(session, task.id, lease_token)
    for key in ("input_tokens", "output_tokens"):
        try:
            amount = int(item_usage.get(key) or 0)
        except (AttributeError, TypeError, ValueError):
            amount = 0
        if amount > 0:
            usage[key] = int(usage.get(key) or 0) + amount
    if isinstance(call_id, str) and re.fullmatch(r"[0-9a-f]{32}", call_id):
        entries = list((task.result_counts or {}).get("_provider_budget_reservations") or [])
        matched_entries = [entry for entry in entries if entry.get("call_id") == call_id]
        if len(matched_entries) != 1:
            raise ValueError("模型调用账本缺少唯一预留，拒绝结算")
        entry = matched_entries[0]
        if str(entry.get("transport_mode") or "") != "realtime":
            raise ValueError("模型调用账本 transport 与 realtime 响应不一致")
        actual_cost = _provider_actual_call_cost(entry, item_usage)
        if actual_cost is not None:
            entry["actual_cost_yuan"] = f"{actual_cost:.8f}"
            entry["cost_yuan"] = f"{actual_cost:.8f}"
            task.result_counts = merge_result_counts_preserving_provider_ledger(
                task, {"_provider_budget_reservations": entries}
            )
    task.actual_usage = dict(usage)
    session.commit()


def _validation_error_entries(exc: Exception) -> list[dict[str, str]]:
    errors_method = getattr(exc, "errors", None)
    if not callable(errors_method):
        return []
    try:
        errors = errors_method()
    except Exception:
        return []
    entries: list[dict[str, str]] = []
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        location = ".".join(str(item) for item in error.get("loc") or ())
        error_type = str(error.get("type") or "validation_error")
        message = str(error.get("msg") or "")
        if "包含不匹配的事实状态" in message:
            code = "fact_state_mismatch"
        elif "Field required" in message or "字段必填" in message:
            code = "missing_field"
        elif "valid list" in message or "有效列表" in message:
            code = "invalid_list"
        else:
            code = error_type
        entries.append({"path": location or "root", "code": code})
    return entries[:8]


def _safe_model_value_error_code(message: str) -> str:
    """Map semantic contract failures to stable, non-content error codes."""

    mappings = (
        ("咨询结构章节不能重复", "duplicate_sections"),
        ("咨询结果引用未知来源块", "unknown_source_block"),
        ("咨询结果包含来源块未支持的数字或日期", "unsupported_source_number"),
        ("咨询结果改变了来源块的否定状态", "negation_state_mismatch"),
        ("咨询结果改变了来源块的人物归属", "person_attribution_mismatch"),
        ("咨询章节与事实状态不一致", "section_fact_state_mismatch"),
        ("source_block_ids必须唯一且非空", "invalid_source_block_ids"),
        ("决策单元至少需要problem或mechanism_or_diagnosis", "missing_case_problem"),
        ("非E0决策单元必须包含actions", "missing_case_actions"),
        ("E2/E3必须包含observed_outcomes", "missing_observed_outcomes"),
        ("整体E2/E3必须至少包含一个已观察结果", "missing_overall_observed_result"),
        ("source_coverage必须完整覆盖输入来源块", "source_coverage"),
        ("retrieval_queries必须为中文自然问法", "retrieval_query_language"),
        ("缺少background或problem证据", "missing_case_evidence"),
        ("reference_advice缺少advice证据", "unverified_advice"),
        ("reference_advice不是可回溯原文", "unverified_advice"),
        ("背景/问题表现包含建议或未来动作", "non_factual_case_field"),
        ("咨询案例核心事实超出本案例来源证据", "unsupported_case_fact"),
        ("问题高度重复，疑似过度拆分", "duplicate_cases"),
    )
    for marker, code in mappings:
        if marker in message:
            return code
    return "semantic_contract_error"


def _model_contract_error_message(exc: Exception) -> str:
    if isinstance(exc, _ModelCallLimitExceeded):
        return "单个条目的模型调用已达到两次上限，未发起第三次调用"
    if isinstance(exc, _ModelDailyBudgetExceeded):
        return "模型调用会超过当日预算，未发起模型调用"
    if isinstance(exc, _ModelAcceptanceBudgetExceeded):
        return "模型调用会超过隔离测试累计预算，未发起模型调用"
    if isinstance(exc, _ModelBudgetLockUnavailable):
        return "模型预算锁不可用，未发起模型调用"
    if isinstance(exc, ModelNoResponseError):
        return "模型未返回可用结果（已按文档重试一次）"
    entries = _validation_error_entries(exc)
    if entries:
        details = "; ".join(f"path={entry['path']},code={entry['code']}" for entry in entries)
        return f"模型契约校验失败（脱敏）: {details}"
    message = str(exc)
    if "包含不匹配的事实状态" in message:
        return "模型契约校验失败（脱敏）: code=fact_state_mismatch"
    if "完整覆盖" in message:
        return "模型契约校验失败（脱敏）: code=source_coverage"
    if isinstance(exc, ValueError):
        return f"模型契约校验失败（脱敏）: code={_safe_model_value_error_code(message)}"
    return f"模型契约校验失败（脱敏）: code={type(exc).__name__}"


def _model_contract_retry_hint(exc: Exception) -> str:
    """Return a safe, structural-only correction request for one retry.

    The provider never receives the failed payload or source filename.  The
    hint contains only a stable contract requirement, so it can repair common
    Qwen JSON omissions without weakening the evidence gate.
    """

    message = str(exc)
    entries = _validation_error_entries(exc)
    if "risks_or_limits" in message and any(
        entry["code"] == "fact_state_mismatch" for entry in entries
    ):
        return (
            "修复 risks_or_limits 的 EvidenceItem.status：该数组只能使用 "
            "consultant_judgment 或 derived_condition；不得使用 customer_fact、"
            "observed_result、decision 或 planned_action。保持原文证据，不要改写 text。"
        )
    for entry in entries:
        path = entry["path"]
        if entry["code"] == "fact_state_mismatch" and "risks_or_limits" in path:
            return (
                "修复 risks_or_limits 的 EvidenceItem.status：该数组只能使用 "
                "consultant_judgment 或 derived_condition；不得使用 customer_fact、"
                "observed_result、decision 或 planned_action。保持原文证据，不要改写 text。"
            )
        if entry["code"] == "fact_state_mismatch":
            return (
                "严格按字段语义修复 EvidenceItem.status，不要改变 text 或来源块："
                "local_context 只能 customer_fact/observed_result；problem 只能 "
                "customer_fact/consultant_judgment；mechanism_or_diagnosis 只能 "
                "consultant_judgment；actions 只能 planned_action/decision；"
                "observed_outcomes 只能 observed_result；stated_conditions 只能 "
                "customer_fact/consultant_judgment/derived_condition；derived_conditions "
                "只能 derived_condition；risks_or_limits 只能 consultant_judgment/derived_condition。"
            )
    if "完整覆盖" in message:
        return (
            "输入 blocks 的每一个 key 必须且只能出现一次："
            "保留的列入 keep_block_ids，其余列入 drop_block_ids。"
        )
    if "valid list" in message or "Input should be a valid list" in message:
        return (
            "DecisionUnit 的 local_context、problem、mechanism_or_diagnosis、actions、"
            "observed_outcomes、stated_conditions、derived_conditions、risks_or_limits，"
            "以及 shared_context/objectives/time_sensitive_items，必须都是 EvidenceItem 对象数组；"
            "每项包含 text、status、source_block_ids，不能把整段字符串直接填入数组字段。"
        )
    if "E2/E3必须包含observed_outcomes" in message:
        return (
            "若输入来源没有明确已观察结果，decision_unit.evidence_level 只能使用 E0 或 E1；"
            "不要为了满足字段虚构 observed_outcomes。只有来源明确写出实际结果时才使用 E2/E3，"
            "并将该结果以 status=observed_result 的 EvidenceItem 放入 observed_outcomes。"
        )
    value_error_code = _safe_model_value_error_code(message)
    if value_error_code == "duplicate_sections":
        return "每个咨询章节 heading 必须唯一；不要重复输出同名章节，保留有来源依据的章节。"
    if value_error_code == "unknown_source_block":
        return "每个 source_block_ids 只能引用输入 blocks 中已有的 key，不要创建或改写来源块 key。"
    if value_error_code == "unsupported_source_number":
        return "不要新增来源 blocks 未出现的数字、日期、比例或金额；无法保留时删除该表述。"
    if value_error_code == "negation_state_mismatch":
        return "保持来源 blocks 的肯定/否定事实状态，不要把肯定改成否定或把否定改成肯定。"
    if value_error_code == "person_attribution_mismatch":
        return "保持来源 blocks 中的人物归属，不要新增人物、替换人物或改变人物与事实的关系。"
    if value_error_code == "section_fact_state_mismatch":
        return (
            "章节 fact_state 必须匹配 heading：顾问判断只能 advisor_judgment；"
            "已确认决定只能 decision；行动计划只能 plan 或 decision；实际结果只能 actual_result。"
        )
    if "包含不匹配的事实状态" in message:
        return (
            "严格按数组语义设置 EvidenceItem.status：local_context 只能 customer_fact 或 observed_result；"
            "problem 只能 customer_fact 或 consultant_judgment；mechanism_or_diagnosis 只能 consultant_judgment；"
            "actions 只能 planned_action 或 decision；observed_outcomes 只能 observed_result；"
            "stated_conditions 只能 customer_fact、consultant_judgment 或 derived_condition；"
            "derived_conditions 只能 derived_condition；risks_or_limits 只能 consultant_judgment 或 derived_condition。"
        )
    if "未知来源块" in message or "来源块不能重复归类" in message:
        return "只可使用输入 blocks 中已有的 key，每个 key 只能归类一次。"
    if "否定状态" in message:
        return "不得改变来源中的肯定或否定事实状态；没有证据的章节不要输出。"
    if "数字或日期" in message:
        return "不得增加来源块未出现的数字、日期、比例或金额。"
    if "背景/问题表现包含建议或未来动作" in message:
        return (
            "relevant_background 和 problem_signals 只能保留来源中已经发生的事实或明确的当前表现；"
            "删除其中的建议、应该、计划、希望、未来动作和预计结果。若建议有原文依据，"
            "只能逐字放入 reference_advice，并确保 evidence 中有 kind=advice 的对应原文；"
            "不要新增案例、人物或事实。"
        )
    if "咨询案例核心事实超出本案例来源证据" in message:
        return (
            "primary_problem、relevant_background 和 problem_signals 不得新增本案例证据未出现的"
            "金额、日期、实际结果或明确行业/赛道；保留可回溯证据支持的事实，"
            "不要改写 evidence.quote 或 source_block_ids。"
        )
    if "人物归属" in message:
        return "不得增加或变更来源块中的人物归属。"
    if "课程目录模型未返回课程描述" in message:
        return "课程记录若有来源依据请填写 description；只能依据输入来源块概括，不得编造事实；无依据时留空，由服务按来源事实兜底或阻断。"
    if "课程目录模型未返回方向候选" in message:
        return "课程记录只有在输入来源有依据时才填写 direction_candidate；不得猜测或改名、改链接，无依据时留空并接受自动原子阻断。"
    if "学习路线模型未返回路线目标" in message:
        return "路线记录只有在输入来源有依据时才填写 route_target；只能概括来源中的适用目标，不得从名称或顺序猜测、不得编造收益承诺。"
    return "字段、枚举值、来源块引用和 JSON 类型必须完全符合系统提示。"


def _candidate_fingerprint(documents: Iterable[CleaningDocument], prompt_version: str) -> str:
    digest = hashlib.sha256()
    for item in documents:
        digest.update(f"{item.id}:{item.content_sha256}\n".encode("utf-8"))
    digest.update(f"prompt:{prompt_version}".encode("utf-8"))
    return digest.hexdigest()


def _price_snapshot(mode: str) -> tuple[str, str, str, float, float, float]:
    """Return the price snapshot and discount for the selected call mode."""

    if mode == "batch":
        return (
            settings.cleaning_price_version,
            settings.cleaning_price_effective_date,
            settings.cleaning_price_valid_until,
            settings.cleaning_input_price_per_million,
            settings.cleaning_output_price_per_million,
            0.5,
        )
    return (
        settings.cleaning_realtime_price_version,
        settings.cleaning_realtime_price_effective_date,
        settings.cleaning_realtime_price_valid_until,
        settings.cleaning_realtime_input_price_per_million,
        settings.cleaning_realtime_output_price_per_million,
        1.0,
    )


def _catalog_auto_block_reason(
    *,
    estimated_cost_high: float,
    price_version: str,
    effective_date: str,
    valid_until: str,
    input_price: float,
    output_price: float,
) -> str:
    """Return a stable reason when upload-only catalogue automation is unsafe."""

    if not price_version:
        return "价格版本缺失"
    if not effective_date:
        return "价格生效日期缺失"
    if not valid_until:
        return "价格有效期缺失"
    if input_price <= 0 or output_price <= 0:
        return "模型价格未配置"
    try:
        effective = datetime.fromisoformat(effective_date).date()
        valid_until_date = datetime.fromisoformat(valid_until).date()
    except ValueError:
        return "价格日期格式无效"
    today = datetime.now(timezone.utc).date()
    if effective > today:
        return "价格快照尚未生效"
    if valid_until_date < today:
        return "价格快照已过期"
    if estimated_cost_high > settings.course_catalog_auto_approve_cost_max:
        return "预估费用超过自动审批上限"
    return ""


def _fail_catalog_auto_run(session, batch: CleaningBatch, reason: str) -> None:
    """Fail every source item together; never publish a partial catalogue."""

    error_code = "CATALOG_AUTO_PUBLISH_BLOCKED"
    error_message = f"课程目录自动发布已阻断：{reason}"
    documents = session.scalars(
        select(CleaningDocument).join(CleaningFile).where(CleaningFile.batch_id == batch.id)
    )
    for document in documents:
        if document.status == "excluded":
            continue
        # Keep the document-level model/validation failure that caused the
        # atomic catalogue run to stop.  The batch-level block is only a
        # publication summary and must not erase the actionable root cause.
        if document.status in {"failed", "review_required"} and document.error_code:
            document.retryable = False
            continue
        document.status = "failed"
        document.stage = "failed"
        document.error_code = error_code
        document.error_message = error_message
        document.retryable = False
    for file_row in session.scalars(select(CleaningFile).where(CleaningFile.batch_id == batch.id)):
        file_row.status = "failed"
        file_row.outcome = "failed"
        file_row.stage = "failed"
        file_row.error_code = error_code
        file_row.error_message = error_message
    batch.status = "completed"


def _task_cost_reservation(task: ModelCleaningTask) -> Decimal:
    """Return the larger of a task's estimate and verifiable token usage cost.

    A task stores its chosen price-version identifiers but not the numeric
    prices.  When the active price snapshot no longer matches a task that has
    token usage, its exact historical cost cannot be reconstructed safely.  In
    that exceptional case reserve the full daily budget, which blocks further
    calls instead of silently undercounting the cap.
    """

    try:
        estimated = Decimal(str(task.estimated_cost_high or "0"))
    except (InvalidOperation, ValueError):
        logger.warning("invalid model cost reservation task_id=%s", task.id)
        estimated = Decimal("0")

    usage = task.actual_usage or {}
    try:
        input_tokens = Decimal(str(usage.get("input_tokens") or "0"))
        output_tokens = Decimal(str(usage.get("output_tokens") or "0"))
    except (AttributeError, InvalidOperation, ValueError):
        logger.warning("invalid model usage task_id=%s", task.id)
        return estimated
    if input_tokens <= 0 and output_tokens <= 0:
        return estimated
    if input_tokens < 0 or output_tokens < 0:
        logger.warning("negative model usage task_id=%s", task.id)
        return estimated

    (
        price_version,
        price_effective_date,
        price_valid_until,
        input_price_per_million,
        output_price_per_million,
        price_discount,
    ) = _price_snapshot(task.mode)
    if (
        task.price_version != price_version
        or task.price_effective_date != price_effective_date
        or task.price_valid_until != price_valid_until
    ):
        logger.warning(
            "model usage price snapshot no longer matches task_id=%s; blocking further calls",
            task.id,
        )
        return max(estimated, Decimal(str(settings.cleaning_daily_budget_yuan)))

    actual = (
        (
            input_tokens * Decimal(str(input_price_per_million))
            + output_tokens * Decimal(str(output_price_per_million))
        )
        / Decimal("1000000")
        * Decimal(str(price_discount))
    )
    return max(estimated, actual)


def _daily_model_reserved_cost(session) -> Decimal:
    """Return today's conservative cost of provider calls actually reserved."""

    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    total = Decimal("0")
    tasks = session.scalars(select(ModelCleaningTask)).all()
    for task in tasks:
        valid_document_ids = set(
            session.scalars(
                select(CleaningDocument.id)
                .join(CleaningFile)
                .where(CleaningFile.batch_id == task.batch_id)
            )
        )
        if not _task_provider_ledger_is_consistent(
            session, task, valid_document_ids=valid_document_ids
        ):
            # We cannot allocate an unknown call to a date safely.  Reserving
            # the whole daily headroom blocks a new paid request rather than
            # letting a partially decoded ledger understate spend.
            total += Decimal(str(settings.cleaning_daily_budget_yuan))
            continue
        entries = _provider_budget_entries(task)
        if entries:
            total += sum(cost for date, cost in entries if date == today.isoformat())
            continue
        if not _task_has_legacy_provider_reservation(task):
            continue
        created_at = task.created_at
        if created_at is None:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != today:
            continue
        total += _task_cost_reservation(task)
    return total


def _cumulative_model_reserved_cost(session) -> Decimal:
    """Return a conservative lifetime total for the disposable test service."""

    total = Decimal("0")
    for task in session.scalars(select(ModelCleaningTask)).all():
        valid_document_ids = set(
            session.scalars(
                select(CleaningDocument.id)
                .join(CleaningFile)
                .where(CleaningFile.batch_id == task.batch_id)
            )
        )
        if not _task_provider_ledger_is_consistent(
            session, task, valid_document_ids=valid_document_ids
        ):
            # An unparseable durable reservation makes lifetime spend unknown.
            # The disposable acceptance service must stop instead of assuming
            # it has remaining budget.
            total += Decimal(str(settings.acceptance_cleaning_budget_max_yuan))
            continue
        entries = _provider_budget_entries(task)
        if entries:
            total += sum(cost for _, cost in entries)
        elif _task_has_legacy_provider_reservation(task):
            total += _task_cost_reservation(task)
    return total


def _maybe_plan_model_task_unlocked(batch_id: str) -> str | None:
    """Create exactly one model plan after every source file is parsed."""

    submit_realtime = False
    with SessionLocal() as session:
        batch = session.scalar(
            select(CleaningBatch)
            .where(CleaningBatch.id == batch_id)
            .options(selectinload(CleaningBatch.files))
            .with_for_update()
        )
        if batch is None:
            return None
        # A concurrent file worker may still be parsing. Wait for its final
        # call rather than pricing an incomplete candidate set.
        if any(
            item.stage in {"upload", "cleaning"} or item.status == "pending" for item in batch.files
        ):
            return None
        existing = session.scalar(
            select(ModelCleaningTask).where(
                ModelCleaningTask.batch_id == batch_id,
                ModelCleaningTask.status.not_in(("failed", "cancelled", "superseded")),
            )
        )
        if existing is not None:
            return existing.id
        has_prior_model_task = session.scalar(
            select(ModelCleaningTask.id).where(ModelCleaningTask.batch_id == batch_id).limit(1)
        )
        auto_catalog = (
            batch.strategy_code == "course_route_catalog" and settings.course_catalog_auto_publish
        )
        candidate_count = 0
        estimated_input = 0
        estimated_provider_input = 0
        fingerprint_digest = hashlib.sha256()
        oversized_reason = ""
        for document in _iter_documents_for_batch(session, batch_id):
            # A fresh source document has a provable zero before it can ever
            # enter a provider queue.  Do not "repair" missing values on a
            # batch with historical model tasks: that ambiguity must fail
            # closed at reservation/retry time instead.
            if has_prior_model_task is None and "model_attempts" not in (document.audit_json or {}):
                document.audit_json = {**(document.audit_json or {}), "model_attempts": 0}
            request = _request(document, batch.strategy_code)
            tokens = _estimated_tokens(request)
            if tokens > settings.cleaning_candidate_max_tokens:
                if auto_catalog:
                    oversized_reason = "单候选超过模型输入上限"
                else:
                    is_content_strategy = batch.strategy_code in CONTENT_MODEL_STRATEGIES
                    fallback_enabled = (
                        batch.strategy_code not in CONTENT_MODEL_STRATEGIES
                        and _source_fallback_enabled(document, batch.strategy_code)
                    )
                    can_publish_source = bool(document.cleaned_markdown.strip())
                    if fallback_enabled and can_publish_source:
                        document.status = (
                            "shard_pending" if batch.strategy_code in POST_STRATEGIES else "pending"
                        )
                        document.stage = document.status
                    else:
                        document.status = "failed" if is_content_strategy else "review_required"
                        document.stage = "failed" if is_content_strategy else "review"
                    document.error_code = "MODEL_INPUT_TOO_LARGE"
                    document.error_message = (
                        "单候选超过自动模型清洗上限，已使用清洗源 Markdown"
                        if fallback_enabled and can_publish_source
                        else (
                            "单候选超过自动模型清洗上限，已跳过"
                            if is_content_strategy
                            else "单候选超过自动模型清洗上限，需要人工复核"
                        )
                    )
                    if fallback_enabled and can_publish_source:
                        document.audit_json = {
                            **(document.audit_json or {}),
                            "source_markdown_fallback": {
                                "applied": True,
                                "reason_code": "MODEL_INPUT_TOO_LARGE",
                                "model_output_ignored": True,
                            },
                        }
                        document.error_code = None
                        document.error_message = None
                        document.retryable = True
                    else:
                        document.retryable = is_content_strategy
                continue
            candidate_count += 1
            estimated_input += tokens
            estimated_provider_input += _estimated_provider_input_tokens(request)
            fingerprint_digest.update(f"{document.id}:{document.content_sha256}\n".encode("utf-8"))
        if not candidate_count:
            if oversized_reason:
                _fail_catalog_auto_run(session, batch, oversized_reason)
            else:
                from .cleaning import _finish_batch, _refresh_file_state

                for file_row in batch.files:
                    _refresh_file_state(session, file_row.id, commit=False)
                _finish_batch(session, batch.id)
            session.commit()
            return None
        fingerprint_digest.update(f"prompt:{settings.cleaning_prompt_version}".encode("utf-8"))
        mode = choose_call_mode(
            batch.strategy_code,
            candidate_count,
            estimated_input,
            batch_candidate_threshold=settings.cleaning_batch_candidate_threshold,
            batch_token_threshold=settings.cleaning_batch_token_threshold,
            batch_enabled=settings.bailian_batch_enabled,
        )
        fingerprint = fingerprint_digest.hexdigest()
        (
            price_version,
            price_effective_date,
            price_valid_until,
            input_price_per_million,
            output_price_per_million,
            price_discount,
        ) = _price_snapshot(mode)
        # Every candidate may need one complete retry. Reserve both requests
        # up front using a provider-enforced output cap, rather than inferring
        # spend only after worker retries have already called the provider.
        reserved_input = estimated_provider_input * _MAX_PROVIDER_CALLS_PER_DOCUMENT
        estimated_output = (
            candidate_count * settings.cleaning_model_max_tokens * _MAX_PROVIDER_CALLS_PER_DOCUMENT
        )
        input_cost = reserved_input / 1_000_000 * input_price_per_million * price_discount
        output_cost = estimated_output / 1_000_000 * output_price_per_million * price_discount
        estimated_cost_high = input_cost + output_cost
        daily_budget = Decimal(str(settings.cleaning_daily_budget_yuan))
        daily_reserved = _daily_model_reserved_cost(session)
        estimated_cost = Decimal(str(estimated_cost_high))
        lifetime_budget = Decimal(str(settings.acceptance_cleaning_budget_max_yuan))
        cumulative_reserved = _cumulative_model_reserved_cost(session)
        budget_reason_code = ""
        budget_reason_message = ""
        if daily_reserved + estimated_cost > daily_budget:
            budget_reason_code = "MODEL_DAILY_BUDGET_EXCEEDED"
            budget_reason_message = "本批次模型预估费用超过当日预算，未发起模型调用"
        elif (
            settings.cleaning_general_only
            and cumulative_reserved + estimated_cost > lifetime_budget
        ):
            budget_reason_code = "MODEL_ACCEPTANCE_BUDGET_EXCEEDED"
            budget_reason_message = "本批次模型预估费用超过隔离测试累计预算，未发起模型调用"
        if budget_reason_code:
            fallback_applied, fallback_pending_ids, fallback_shard_ids = (
                _fallback_failed_model_documents(
                    session,
                    batch_id,
                    reason_code=budget_reason_code,
                    reason_message=budget_reason_message,
                )
            )
            if not fallback_applied and batch.strategy_code == "course_route_catalog":
                _fail_catalog_auto_run(
                    session,
                    batch,
                    "模型当日预算不足",
                )
            record_event(
                level="WARNING",
                category="task",
                component="model_cleaning_worker",
                event_code=budget_reason_code,
                status="failed",
                message=budget_reason_message,
                resource_type="cleaning_batch",
                resource_id=batch_id,
                details={
                    "daily_budget_yuan": str(daily_budget),
                    "daily_reserved_yuan": str(daily_reserved),
                    "cumulative_reserved_yuan": str(cumulative_reserved),
                    "acceptance_budget_yuan": str(lifetime_budget),
                    "estimated_cost_high_yuan": str(estimated_cost),
                    "fallback_pending_count": len(fallback_pending_ids),
                    "fallback_shard_count": len(fallback_shard_ids),
                },
                session=session,
            )
            session.commit()
            _queue_publication_outputs(fallback_pending_ids, fallback_shard_ids)
            return None
        task = ModelCleaningTask(
            batch_id=batch_id,
            mode=mode,
            model=settings.cleaning_model,
            prompt_version=settings.cleaning_prompt_version,
            status="awaiting_approval" if mode == "batch" else "queued",
            candidate_fingerprint=fingerprint,
            candidate_count=candidate_count,
            estimated_input_tokens=reserved_input,
            estimated_output_tokens=estimated_output,
            estimated_cost_low=f"{input_cost:.6f}",
            estimated_cost_high=f"{estimated_cost_high:.6f}",
            price_version=price_version,
            price_effective_date=price_effective_date,
            price_valid_until=price_valid_until,
        )
        session.add(task)
        auto_submit_batch = False
        if auto_catalog:
            reason = oversized_reason or _catalog_auto_block_reason(
                estimated_cost_high=estimated_cost_high,
                price_version=price_version,
                effective_date=price_effective_date,
                valid_until=price_valid_until,
                input_price=input_price_per_million,
                output_price=output_price_per_million,
            )
            if reason:
                task.status = "failed"
                task.last_error = f"课程目录自动发布已阻断：{reason}"
                _fail_catalog_auto_run(session, batch, reason)
                record_event(
                    level="WARNING",
                    category="task",
                    component="model_cleaning_worker",
                    event_code="CATALOG_AUTO_PUBLISH_BLOCKED",
                    status="failed",
                    message="课程目录自动发布已阻断",
                    resource_type="cleaning_batch",
                    resource_id=batch.id,
                    details={"reason": reason, "candidate_count": candidate_count},
                    session=session,
                )
            elif mode == "batch":
                task.status = "approved"
                task.approval_fingerprint = fingerprint
                task.approved_at = utcnow()
                auto_submit_batch = True
                record_event(
                    level="INFO",
                    category="task",
                    component="model_cleaning_worker",
                    event_code="CATALOG_MODEL_AUTO_APPROVED",
                    status="succeeded",
                    message="课程目录模型任务已按内部费用门禁自动批准",
                    resource_type="cleaning_batch",
                    resource_id=batch.id,
                    details={
                        "candidate_count": candidate_count,
                        "estimated_cost_high": round(estimated_cost_high, 6),
                        "mode": mode,
                    },
                    session=session,
                )
        batch.status = (
            "model_batch_uploading"
            if auto_submit_batch
            else (
                "completed"
                if auto_catalog and task.status == "failed"
                else ("awaiting_model_approval" if mode == "batch" else "model_validating")
            )
        )
        if task.status != "failed":
            for document in _iter_documents_for_batch(session, batch_id):
                document.stage = "model_queued"
            submit_realtime = mode == "realtime"
        session.commit()
        task_id = task.id
    if submit_realtime:
        process_realtime_model_task.apply_async(args=[task_id], queue="ai_model_realtime")
    elif auto_submit_batch:
        try:
            submit_batch_model_task.apply_async(args=[task_id], queue="ai_model_batch")
        except Exception:
            logger.exception("course catalogue auto model queue failed")
            with SessionLocal() as session:
                task = session.get(ModelCleaningTask, task_id)
                if task is not None:
                    batch = session.get(CleaningBatch, task.batch_id)
                    task.status = "failed"
                    task.last_error = "课程目录自动模型任务入队失败"
                    if batch is not None:
                        _fail_catalog_auto_run(session, batch, "模型任务入队失败")
                    session.commit()
    return task_id


def maybe_plan_model_task(batch_id: str) -> str | None:
    """Serialize daily-budget planning across independently finishing files."""

    from zoneinfo import ZoneInfo

    day_key = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with cache.lock(f"ai:model-cleaning-daily-budget:{day_key}", ttl=180) as lock_state:
        # A production Redis lock failure must not turn into concurrent paid
        # requests. The awaiting-model documents remain recoverable for the
        # existing reconciler after cache availability is restored.
        if not lock_state or not lock_state.available:
            logger.warning("daily model budget lock unavailable; defer batch_id=%s", batch_id)
            return None
        return _maybe_plan_model_task_unlocked(batch_id)


def _mark_privacy_review(document: CleaningDocument, message: str) -> None:
    from ...cleaning import content_review_bypass_enabled, preserve_review_reason

    strategy_code = str((document.metadata_json or {}).get("cleaning_strategy") or "")
    if strategy_code in CONTENT_MODEL_STRATEGIES:
        document.status = "failed"
        document.stage = "failed"
        document.error_code = "PRIVACY_REVIEW_REQUIRED"
        document.error_message = message
        document.retryable = False
        return
    if (
        content_review_bypass_enabled(
            strategy_code,
            document.metadata_json or {},
        )
        and document.cleaned_markdown.strip()
    ):
        document.audit_json = preserve_review_reason(
            document.audit_json,
            "PRIVACY_REVIEW_REQUIRED",
            message,
        )
        document.status = "pending"
        document.stage = "pending"
        document.error_code = None
        document.error_message = None
        document.retryable = True
        return
    document.status = "review_required"
    document.stage = "review"
    document.error_code = "PRIVACY_REVIEW_REQUIRED"
    document.error_message = message
    document.retryable = False


def _review_bypass_enabled(document: CleaningDocument, strategy_code: str) -> bool:
    from ...cleaning import content_review_bypass_enabled

    return content_review_bypass_enabled(
        strategy_code,
        document.metadata_json or {},
    )


def _source_block_review_fallback(
    request: PromptRequest,
    title: str,
    kept_entries: Iterable[Any],
) -> str:
    """Render only source blocks explicitly retained by a review response."""

    wanted: set[str] = set()
    ordinals: set[int] = set()
    for entry in kept_entries:
        raw = str(getattr(entry, "block_id", entry) or "").strip()
        if not raw:
            continue
        if raw in request.blocks:
            wanted.add(raw)
            continue
        if raw[:1].lower() == "b" and raw[1:].isdigit():
            ordinals.add(int(raw[1:]))
    values = [
        str(text).strip()
        for block_id, text in request.blocks.items()
        if block_id in wanted
        or (
            block_id[:1].lower() == "b" and block_id[1:].isdigit() and int(block_id[1:]) in ordinals
        )
    ]
    values = [value for value in values if value]
    if not values:
        return ""
    return sanitize_for_knowledge(f"# {title}\n\n" + "\n\n".join(values))


def _count_model_result(counts: dict[str, int], document: CleaningDocument) -> None:
    if document.status == "excluded":
        key = "excluded"
    elif document.status == "review_required":
        key = "review"
    elif document.status == "failed":
        key = "failed"
    else:
        key = "succeeded"
    counts[key] = counts.get(key, 0) + 1


def _render_post_knowledge_unit(
    unit: Any,
    *,
    source_blocks: dict[str, str],
) -> str:
    """Render an evidence-bound model unit without adding unsupported facts."""

    evidence = "\n\n".join(
        source_blocks[block_id] for block_id in unit.source_block_ids if block_id in source_blocks
    ).strip()
    sections: list[str] = []
    for heading, value in (
        ("问题", unit.problem),
        ("结论", unit.conclusion),
        ("推理依据", unit.reasoning),
    ):
        if str(value or "").strip():
            sections.append(f"## {heading}\n\n{str(value).strip()}")
    for heading, values in (
        ("适用条件", unit.applicable_conditions),
        ("行动建议", unit.actions),
        ("风险与边界", unit.risks_or_limits),
    ):
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if cleaned:
            sections.append(f"## {heading}\n\n" + "\n".join(f"- {value}" for value in cleaned))
    sections.append("## 来源证据\n\n" + evidence)
    return sanitize_for_knowledge(
        f"# {unit.title.strip()}\n\n"
        + (f"主题：{unit.topic.strip()}\n\n" if unit.topic.strip() else "")
        + "\n\n".join(sections)
    )


def _render_post_markdown(*, body: str, title: str, topic: str) -> str:
    chunks = prepare_chunks(
        body,
        title=title,
        content_type="article",
        topics=(topic,) if topic else (),
        overlap=0,
    )
    raw_children = [child for chunk in chunks for child in chunk.children if child.strip()]
    children = [_post_child_for_dify(child, title=title) for child in raw_children]
    if any(child is None for child in children):
        raise ValueError("DIFY_CHILD_TOO_SHORT: 帖子有效内容不足以满足知识库最小分段长度")
    children = [child for child in children if child is not None]
    context = chunks[0].content.partition("\n")[0] if chunks else f"【上下文】{title}｜article"
    one_parent = f"{context}\n\n" + "\n\n<<<DIFY_CHILD>>>\n\n".join(children) if children else body
    return sanitize_for_knowledge(one_parent)


def _model_review_request_context(
    request: PromptRequest,
    *,
    model_name: str = "",
    prompt_version: str = "",
    repair_hint: str = "",
) -> dict[str, Any]:
    return {
        "strategy_code": request.strategy_code,
        "item_id": request.item_id,
        "content_sha256": request.content_sha256,
        "title": request.title,
        "blocks": request.blocks,
        "model": model_name,
        "prompt_version": prompt_version,
        "repair_hint_sha256": hashlib.sha256(repair_hint.encode("utf-8")).hexdigest()
        if repair_hint
        else "",
    }


def _store_model_review_request_context(
    document: CleaningDocument,
    request: PromptRequest,
    *,
    model_name: str = "",
    prompt_version: str = "",
    repair_hint: str = "",
) -> None:
    audit = dict(document.audit_json or {})
    audit["_model_review_request"] = _model_review_request_context(
        request,
        model_name=model_name,
        prompt_version=prompt_version,
        repair_hint=repair_hint,
    )
    document.audit_json = audit


def _store_fenced_model_review_request_context(
    session,
    document: CleaningDocument,
    request: PromptRequest,
    *,
    task_id: str,
    lease_token: str,
    model_name: str = "",
    prompt_version: str = "",
    repair_hint: str = "",
) -> None:
    """Commit request audit evidence only while this worker owns the lease."""

    _assert_active_model_lease(session, task_id, lease_token)
    _store_model_review_request_context(
        document,
        request,
        model_name=model_name,
        prompt_version=prompt_version,
        repair_hint=repair_hint,
    )
    session.commit()


def _apply_model_payload(
    document: CleaningDocument,
    strategy_code: str,
    request: PromptRequest,
    payload: dict[str, Any],
    *,
    session=None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    repair_hint: str | None = None,
) -> None:
    audit = dict(document.audit_json or {})
    # Keep the provider response in the internal audit trail so a later
    # contract failure can produce a complete review artifact.  It never goes
    # to Dify metadata or user-facing logs.
    audit["_model_review_payload"] = payload
    existing_request = audit.get("_model_review_request") or {}
    request_context = _model_review_request_context(
        request,
        model_name=model_name or "",
        prompt_version=prompt_version or "",
        repair_hint=repair_hint or "",
    )
    if repair_hint is None and isinstance(existing_request, dict):
        request_context["repair_hint_sha256"] = str(
            existing_request.get("repair_hint_sha256") or ""
        )
    audit["_model_review_request"] = request_context
    document.audit_json = audit
    available = set(request.blocks)
    if strategy_code in POST_STRATEGIES:
        try:
            result = validate_semantic_response(
                strategy_code,
                payload,
                available_block_ids=available,
                source_blocks=request.blocks,
            )
        except ValueError as exc:
            if "未返回可检索的知识单元" in str(exc):
                raise _ZeroCoreModelOutputError(str(exc)) from exc
            raise
        audit["model_result"] = result.model_dump(mode="json")
        if result.disposition == "exclude":
            document.status = "excluded"
            document.stage = "completed"
            document.error_code = result.reason_code
            document.error_message = "模型判定不适合商业顾问知识库"
        elif result.disposition == "review" and not _review_bypass_enabled(
            document,
            strategy_code,
        ):
            document.status = "failed"
            document.stage = "failed"
            document.error_code = "MODEL_REVIEW_SKIPPED"
            document.error_message = f"模型判定需要复核，已跳过（reason={result.reason_code}）"
            document.retryable = True
        else:
            if result.disposition == "review":
                from ...cleaning import preserve_review_reason

                audit = preserve_review_reason(
                    audit,
                    "MODEL_REVIEW_SKIPPED",
                    f"模型判定需要复核，按保留来源继续（reason={result.reason_code}）",
                )
            kept = [request.blocks[block_id] for block_id in result.keep_block_ids]
            body = "\n\n".join(kept)
            if not body.strip():
                raise _ZeroCoreModelOutputError("模型复核结果没有可继续的来源正文")
            from ...cleaning import content_review_reason

            short_reason = content_review_reason(
                body,
                strategy_code,
                document.metadata_json or {},
            )
            if short_reason:
                raise _ZeroCoreModelOutputError(short_reason[1])
            unit_specs: list[tuple[str, str, str, dict[str, Any]]] = []
            if result.knowledge_units:
                for unit in result.knowledge_units:
                    unit_body = _render_post_knowledge_unit(
                        unit,
                        source_blocks=request.blocks,
                    )
                    unit_specs.append(
                        (
                            unit.title.strip(),
                            unit_body,
                            unit.topic.strip() or result.topic,
                            unit.model_dump(mode="json"),
                        )
                    )
            else:
                unit_specs.append((document.title, body, result.topic, {}))

            rendered_units: list[tuple[str, str, str, dict[str, Any]]] = []
            rejected_units: list[dict[str, str]] = []
            for unit_title, unit_body, unit_topic, unit_payload in unit_specs:
                short_reason = content_review_reason(
                    unit_body,
                    strategy_code,
                    document.metadata_json or {},
                )
                if short_reason:
                    rejected_units.append(
                        {
                            "unit_id": str(unit_payload.get("unit_id") or unit_title),
                            "reason_code": short_reason[0],
                        }
                    )
                    continue
                rendered_units.append(
                    (
                        unit_title,
                        _render_post_markdown(
                            body=unit_body,
                            title=unit_title,
                            topic=unit_topic,
                        ),
                        unit_topic,
                        unit_payload,
                    )
                )

            if rejected_units:
                audit["rejected_knowledge_units"] = rejected_units
            if not rendered_units:
                document.audit_json = audit
                raise _ZeroCoreModelOutputError("模型输出没有可发布的核心知识单元")

            from .cleaning import _content_sha

            source_article_title = document.title

            def apply_rendered(
                target: CleaningDocument,
                unit_title: str,
                rendered: str,
                unit_topic: str,
                unit_payload: dict[str, Any],
            ) -> None:
                metadata = dict(target.metadata_json or {})
                metadata["source_title"] = source_article_title
                metadata["source_document_id"] = document.id
                if unit_title != source_article_title:
                    metadata["unit_title"] = unit_title
                if unit_payload.get("unit_id"):
                    metadata["knowledge_unit_id"] = unit_payload["unit_id"]
                if unit_topic:
                    metadata["topic"] = unit_topic
                target.title = unit_title[:300]
                target.cleaned_markdown = rendered
                target.metadata_json = metadata
                target.status = "shard_pending"
                target.stage = "shard_pending"
                target.error_code = None
                target.error_message = None
                target.retryable = True
                target.content_sha256 = _content_sha(
                    target.cleaned_markdown,
                    target.metadata_json,
                    target.chunking_json or {},
                )

            first_title, first_rendered, first_topic, first_payload = rendered_units[0]
            apply_rendered(
                document,
                first_title,
                first_rendered,
                first_topic,
                first_payload,
            )
            if session is not None and len(rendered_units) > 1:
                for unit_index, (unit_title, rendered, unit_topic, unit_payload) in enumerate(
                    rendered_units[1:], start=2
                ):
                    child_audit = _derived_document_audit(
                        audit,
                        model_result={"knowledge_unit": unit_payload},
                        source_article_document_id=document.id,
                    )
                    child = CleaningDocument(
                        file_id=document.file_id,
                        sequence=document.sequence * 1000 + unit_index,
                        title=unit_title[:300],
                        dify_document_name=document.dify_document_name,
                        document_key=f"{document.document_key}:unit:{unit_index}",
                        status="shard_pending",
                        stage="shard_pending",
                        content_sha256="",
                        metadata_json=dict(document.metadata_json or {}),
                        chunking_json=dict(document.chunking_json or {}),
                        audit_json=child_audit,
                        warnings_json=list(document.warnings_json or []),
                        cleaned_markdown=rendered,
                        retryable=True,
                    )
                    session.add(child)
                    session.flush()
                    apply_rendered(child, unit_title, rendered, unit_topic, unit_payload)
            elif len(rendered_units) > 1:
                raise ValueError("模型返回多个知识单元，但当前任务没有可写入会话")
            document.audit_json = audit
    elif strategy_code == "personal_business_consultation":
        if str(payload.get("schema_version") or "") == "consultation-rag-v1":
            # The sample cleaner's evidence-bound case contract is the current
            # production path.  Validate and render every case before mutating
            # the document so one bad case falls back atomically to the whole
            # de-identified meeting.
            source_text = "\n\n".join(request.blocks.values())
            sensitive_tokens = tuple(
                str(value)
                for value in audit.get("forbidden_public_names") or []
                if str(value).strip()
            )
            response: ConsultationRagResponse = validate_consultation_rag_response(
                payload,
                source_blocks=request.blocks,
                sensitive_tokens=sensitive_tokens,
            )
            audit["model_result"] = response.model_dump(mode="json")
            if len(response.cases) < 1:
                raise ValueError("咨询案例模型未返回可发布案例")
            rendered_cases: list[tuple[str, str, str, dict[str, Any]]] = []
            source_case_id = _safe_case_group_id(document)
            for case_index, case in enumerate(response.cases, start=1):
                case_id = f"{source_case_id}_c{case_index:02d}"
                rendered = sanitize_for_knowledge(render_consultation_rag_case(case_id, case))
                from knowledge_cleaner.pdf_convert import scan_residual_pii

                residual = scan_residual_pii(rendered)
                if residual or any(value in rendered for value in sensitive_tokens):
                    raise ValueError("咨询模型输出仍含潜在PII或受保护身份")
                topic = topic_for_problem_category(case.problem_category)
                rendered_cases.append(
                    (
                        case_id,
                        rendered,
                        topic,
                        {
                            "case_id": case_id,
                            "case_group_id": source_case_id,
                            "problem_category": case.problem_category,
                            "case_stage": case.case_stage,
                            "has_reference_advice": bool(case.reference_advice),
                            "has_advice_summary": bool(case.advice_summary.strip()),
                            "advice_evidence_count": sum(
                                item.kind == "advice" for item in case.evidence
                            ),
                            "methodology_candidate": (
                                "candidate_ready"
                                if render_methodology_candidate(case_id, case)
                                else "omitted_no_traceable_advice"
                            ),
                        },
                    )
                )

            def apply_case(
                target: CleaningDocument,
                case_id: str,
                rendered: str,
                topic: str,
                case_audit: dict[str, Any],
            ) -> None:
                metadata = dict(target.metadata_json or {})
                metadata["record_type"] = "consulting_case"
                metadata["case_id"] = case_id
                metadata["case_group_id"] = source_case_id
                if topic:
                    metadata["topic"] = topic
                else:
                    metadata.pop("topic", None)
                target.title = f"匿名商业案例 {case_id[-5:]}"
                target.cleaned_markdown = rendered
                target.metadata_json = metadata
                target.audit_json = {
                    **dict(target.audit_json or {}),
                    "model_result": {
                        "schema_version": response.schema_version,
                        "case": case_audit,
                    },
                    "consultation_rag": {
                        "case_group_id": source_case_id,
                        "case_count": len(rendered_cases),
                        "source_chars": len(source_text),
                    },
                }
                target.status = "pending"
                target.stage = "pending"
                target.error_code = None
                target.error_message = None
                target.retryable = True
                from .cleaning import _content_sha

                target.content_sha256 = _content_sha(
                    target.cleaned_markdown,
                    target.metadata_json,
                    target.chunking_json or {},
                )

            first_case_id, first_rendered, first_topic, first_audit = rendered_cases[0]
            if len(rendered_cases) > 1:
                from ...cleaning import dify_document_name

                document.dify_document_name = dify_document_name(
                    getattr(document.file, "original_filename", document.dify_document_name),
                    document.content_sha256,
                    1,
                    split=True,
                )
            apply_case(document, first_case_id, first_rendered, first_topic, first_audit)
            if session is not None and len(rendered_cases) > 1:
                for case_index, (case_id, rendered, topic, case_audit) in enumerate(
                    rendered_cases[1:], start=2
                ):
                    from ...cleaning import dify_document_name

                    child_dify_name = dify_document_name(
                        getattr(document.file, "original_filename", document.dify_document_name),
                        document.content_sha256,
                        case_index,
                        split=True,
                    )
                    child = CleaningDocument(
                        file_id=document.file_id,
                        sequence=document.sequence * 1000 + case_index,
                        title=f"匿名商业案例 {case_id[-5:]}",
                        dify_document_name=child_dify_name,
                        document_key=f"{document.document_key}:case:{case_index}",
                        status="pending",
                        stage="pending",
                        content_sha256="",
                        metadata_json=dict(document.metadata_json or {}),
                        chunking_json=dict(document.chunking_json or {}),
                        audit_json=_derived_document_audit(audit),
                        warnings_json=list(document.warnings_json or []),
                        cleaned_markdown=rendered,
                        retryable=True,
                    )
                    session.add(child)
                    session.flush()
                    apply_case(child, case_id, rendered, topic, case_audit)
            elif len(rendered_cases) > 1:
                raise ValueError("模型返回多个咨询案例，但当前任务没有可写入会话")
            if session is not None:
                # The candidate is projected from the same validated case and
                # evidence.  It is deliberately case-scoped and never a
                # cross-source/general-method claim.
                for case_index, case in enumerate(response.cases, start=1):
                    case_id = rendered_cases[case_index - 1][0]
                    methodology = render_methodology_candidate(case_id, case)
                    if not methodology:
                        continue
                    method_id = f"{case_id}_m01"
                    _, _, case_topic, _ = rendered_cases[case_index - 1]
                    # ``document`` has already been transformed into the
                    # first case above.  Build each methodology projection
                    # from its own rendered case instead of leaking that
                    # first case's topic into later candidates.
                    method_metadata = dict(document.metadata_json or {})
                    if case_topic:
                        method_metadata["topic"] = case_topic
                    else:
                        method_metadata.pop("topic", None)
                    method_metadata.update(
                        {
                            "record_type": "methodology",
                            "case_id": method_id,
                            "case_group_id": source_case_id,
                            "candidate_for_case_id": case_id,
                            "methodology_scope": "case_scoped",
                        }
                    )

                    method_key = _methodology_document_key(
                        document.document_key,
                        case.model_dump(mode="json"),
                    )
                    existing_method = session.scalar(
                        select(CleaningDocument.id).where(
                            CleaningDocument.file_id == document.file_id,
                            CleaningDocument.document_key == method_key,
                        )
                    )
                    if existing_method is not None:
                        continue
                    from ...cleaning import dify_document_name
                    from .cleaning import _content_sha

                    method = CleaningDocument(
                        file_id=document.file_id,
                        sequence=document.sequence * 1000 + 100 + case_index,
                        title=f"匿名方法候选 {method_id[-5:]}",
                        dify_document_name=dify_document_name(
                            getattr(
                                document.file, "original_filename", document.dify_document_name
                            ),
                            document.content_sha256,
                            100 + case_index,
                            split=True,
                        ),
                        document_key=method_key,
                        status="pending",
                        stage="pending",
                        content_sha256="",
                        metadata_json=method_metadata,
                        chunking_json=dict(document.chunking_json or {}),
                        audit_json={
                            **audit,
                            "methodology_candidate": {
                                "case_id": case_id,
                                "evidence_count": sum(
                                    item.kind == "advice" for item in case.evidence
                                ),
                            },
                        },
                        warnings_json=list(document.warnings_json or []),
                        cleaned_markdown=sanitize_for_knowledge(methodology),
                        retryable=True,
                    )
                    method.content_sha256 = _content_sha(
                        method.cleaned_markdown,
                        method.metadata_json,
                        method.chunking_json or {},
                    )
                    session.add(method)
            # A semantic review is a human gate, not a source fallback.  The
            # source fallback flag is consumed by the outer worker only for
            # technical/contract failures.
            document.audit_json = {
                **(document.audit_json or {}),
                "source_markdown_fallback_blocked": False,
            }
            return
        # v2 is opt-in by an explicit schema marker so historical model tasks
        # remain replayable under the existing sections contract.
        if str(payload.get("schema_version") or "") == "consulting_case_v2":
            from knowledge_cleaner.consultation_v2 import (
                CaseDisposition,
                render_case_markdown,
                validate_case_payload,
            )

            case = validate_case_payload(payload, source_blocks=request.blocks)
            audit["model_result"] = case.model_dump(mode="json")
            if case.disposition == CaseDisposition.EXCLUDE:
                document.status = "excluded"
                document.stage = "completed"
                document.error_code = "CONSULTATION_EXCLUDED"
                document.error_message = "模型判定案例不适合进入商业顾问知识库"
                document.audit_json = audit
                return
            if case.disposition == CaseDisposition.REVIEW and not _review_bypass_enabled(
                document,
                strategy_code,
            ):
                document.status = "failed"
                document.stage = "failed"
                document.error_code = "MODEL_REVIEW_SKIPPED"
                document.error_message = "咨询模型判定证据或背景不足，已跳过"
                document.retryable = True
                document.audit_json = audit
                return
            if case.disposition == CaseDisposition.REVIEW:
                from ...cleaning import preserve_review_reason

                audit = preserve_review_reason(
                    audit,
                    "MODEL_REVIEW_SKIPPED",
                    "咨询模型判定证据或背景不足，按保留来源块继续",
                )
                rendered = _source_block_review_fallback(
                    request,
                    document.title,
                    case.source_coverage.kept,
                )
                if not rendered.strip():
                    document.status = "review_required"
                    document.stage = "review"
                    document.error_code = "CONTENT_EMPTY_AFTER_MODEL"
                    document.error_message = "咨询复核结果没有可继续的来源正文"
                    document.audit_json = audit
                    document.retryable = False
                    return
            else:
                rendered = render_case_markdown(case)
            from knowledge_cleaner.pdf_convert import scan_residual_pii

            residual = scan_residual_pii(rendered)
            forbidden_names = [
                str(value) for value in audit.get("forbidden_public_names") or [] if str(value)
            ]
            document.cleaned_markdown = sanitize_for_knowledge(rendered)
            if residual or any(value in rendered for value in forbidden_names):
                _mark_privacy_review(document, "咨询模型输出仍含潜在PII或受保护身份")
                return
            document.metadata_json = {
                **(document.metadata_json or {}),
                "record_type": "consulting_case_v2",
                "case_id": case.case_id,
                "case_group_id": case.case_group_id or "",
                "topic": "；".join(unit.topic for unit in case.decision_units),
                "evidence_level": case.overall_evidence_level.value,
                "background_sufficiency": case.background_sufficiency.value,
                "time_sensitive": case.time_sensitive,
                "review_status": "awaiting_model",
            }
            document.status = "pending"
            document.stage = "pending"
            document.error_code = None
            document.error_message = None
            document.audit_json = audit
            return
        result = validate_consultation_response(
            payload,
            available_block_ids=available,
            source_blocks=request.blocks,
        )
        parts = [f"## {section.heading}\n\n{section.text}" for section in result.sections]
        document.cleaned_markdown = sanitize_for_knowledge(
            f"# {document.title}\n\n" + "\n\n".join(parts)
        )
        from knowledge_cleaner.pdf_convert import scan_residual_pii

        residual = scan_residual_pii(document.cleaned_markdown)
        forbidden_names = [
            str(value) for value in audit.get("forbidden_public_names") or [] if str(value)
        ]
        if residual or any(value in document.cleaned_markdown for value in forbidden_names):
            _mark_privacy_review(document, "咨询模型输出仍含潜在PII或受保护身份")
            return
        document.status = "pending"
        document.stage = "pending"
        audit["model_result"] = result.model_dump(mode="json")
    elif strategy_code == "course_route_catalog":
        result = CourseEnrichmentResponse.model_validate(payload)
        audit["ai_candidate"] = result.model_dump(mode="json")
        audit["model_result"] = result.model_dump(mode="json")
        draft = dict(audit.get("catalog_draft") or {})
        kind = str(audit.get("catalog_kind") or "")
        direction = result.direction_candidate.strip() or str(draft.get("direction") or "").strip()
        if not direction and kind == "route":
            direction = "课程学习路线"
        if not direction:
            raise ValueError("课程目录模型未返回方向候选")
        if kind == "course":
            description = result.description.strip()
            if not description:
                # The description is semantic, but a sparse catalogue row can
                # legitimately leave the model with no safe paraphrase.  A
                # deterministic source-field summary is still factual and
                # keeps the course publishable without asking the model to
                # invent content or choose identity fields.
                source_facts = [
                    str(value).strip()
                    for value in (request.blocks or {}).values()
                    if str(value).strip()
                ]
                description = "；".join(source_facts)
            if not description:
                raise ValueError("课程目录模型未返回课程描述")
            draft.update(
                {
                    "direction": direction,
                    "description": description,
                    "audience": result.audience.strip(),
                    "highlights": list(result.highlights),
                    "learning_outcomes": list(result.learning_outcomes),
                    "enabled": True,
                    "review_status": "approved",
                }
            )
            record = CourseRecord.model_validate(draft)
            document.cleaned_markdown = sanitize_for_knowledge(_course_text(record))
            document.metadata_json = {
                **(document.metadata_json or {}),
                "document_type": "course",
                "course_id": record.course_id,
                "direction": record.direction,
                "stage": record.stage,
                "source_platform": record.source_platform,
                "series_name": record.series_name or "",
                "status": "active",
                "review_status": "approved",
            }
        elif kind == "route":
            route_target = result.route_target.strip() or str(draft.get("target") or "").strip()
            # The workbook's route rows are authoritative for the complete
            # stage/course structure but may not contain a separate marketing
            # target.  Keep those routes publishable without inventing a
            # promise: use the route name as a neutral deterministic target
            # when neither the source nor the model supplied one.
            if not route_target:
                route_target = str(draft.get("name") or document.title).strip()
            draft.update(
                {
                    "direction": direction,
                    "target": route_target,
                    "enabled": True,
                    "review_status": "approved",
                }
            )
            record = RouteRecord.model_validate(draft)
            document.cleaned_markdown = sanitize_for_knowledge(_route_text(record))
            document.metadata_json = {
                **(document.metadata_json or {}),
                "document_type": "learning_route",
                "route_id": record.route_id,
                "direction": record.direction,
                "status": "active",
                "review_status": "approved",
            }
        else:
            raise ValueError("课程目录记录类型无效")
        audit["catalog_draft"] = draft
        catalog_record = record.model_dump(mode="json")
        if kind == "course":
            for key in (
                "collection_id",
                "collection_name",
                "collection_order",
                "collection_entry",
                "collection_declared_lessons",
                "collection_total_minutes",
                "collection_raw_title",
                "collection_entry_course_id",
                "collection_warnings",
            ):
                if key in draft:
                    catalog_record[key] = draft[key]
        audit["catalog_record"] = catalog_record
        audit["automatic_review"] = {
            "approved": True,
            "source": "course_catalog_auto_publish",
        }
        document.status = "pending"
        document.stage = "pending"
        document.error_code = None
        document.error_message = None
        document.retryable = True
    else:
        raise ValueError("未知模型清洗策略")
    document.audit_json = audit


def _invalidate_methodology_candidates_for_failed_case(document: CleaningDocument) -> None:
    """Keep a case-scoped method from outliving its failed source case.

    The relationship intentionally reuses existing metadata instead of adding
    a table or a new state.  This runs before publication, so no remote cleanup
    is needed for the normal model-finalisation failure path.
    """

    session = object_session(document)
    case_id = str((document.metadata_json or {}).get("case_id") or "")
    if session is None or not document.file_id or not case_id:
        return
    candidates = list(
        session.scalars(
            select(CleaningDocument).where(CleaningDocument.file_id == document.file_id)
        )
    )
    for candidate in candidates:
        metadata = candidate.metadata_json or {}
        if (
            metadata.get("record_type") != "methodology"
            or metadata.get("candidate_for_case_id") != case_id
            or candidate.status not in {"pending", "processing", "staged", "awaiting_model"}
        ):
            continue
        candidate.status = "failed"
        candidate.stage = "failed"
        candidate.error_code = "METHODOLOGY_SOURCE_CASE_FAILED"
        candidate.error_message = "关联咨询案例未通过发布前校验，方法候选未发布"
        candidate.retryable = False
        candidate.audit_json = {
            **(candidate.audit_json or {}),
            "methodology_candidate": {
                **((candidate.audit_json or {}).get("methodology_candidate") or {}),
                "source_case_status": "failed",
            },
        }


def _finalize_model_document(
    document: CleaningDocument,
    *,
    strategy_code: str = "",
) -> None:
    """Reapply the knowledge-body invariant to every model output."""

    if not document.cleaned_markdown.strip():
        if strategy_code in CONTENT_MODEL_STRATEGIES:
            document.status = "failed"
            document.stage = "failed"
            document.error_code = "CONTENT_EMPTY_AFTER_MODEL"
            document.error_message = "模型清洗后没有可用核心正文"
            document.retryable = False
            _invalidate_methodology_candidates_for_failed_case(document)
        return
    from ...cleaning import content_review_reason, finalize_knowledge_markdown
    from .cleaning import _content_sha

    audit = dict(document.audit_json or {})
    model_payload = audit.pop("_model_review_payload", None)
    if model_payload is not None:
        document.__dict__["_model_review_payload_for_artifact"] = model_payload
    model_request = audit.pop("_model_review_request", None)
    if model_request is not None:
        document.__dict__["_model_review_request_for_artifact"] = model_request
    document.cleaned_markdown = finalize_knowledge_markdown(
        document.cleaned_markdown,
        audit=audit,
    )
    if strategy_code not in POST_STRATEGIES:
        from ...cleaning import validate_chunking_boundaries
        from .cleaning import _chunking_from_json

        validate_chunking_boundaries(
            document.cleaned_markdown,
            _chunking_from_json(document.chunking_json),
        )
    document.audit_json = audit
    document.content_sha256 = _content_sha(
        document.cleaned_markdown,
        document.metadata_json or {},
        document.chunking_json or {},
    )
    if document.status not in {"failed", "excluded", "review_required"}:
        review_reason = content_review_reason(
            document.cleaned_markdown,
            strategy_code,
            document.metadata_json or {},
        )
        if review_reason:
            document.status = (
                "failed" if strategy_code in CONTENT_MODEL_STRATEGIES else "review_required"
            )
            document.stage = "failed" if strategy_code in CONTENT_MODEL_STRATEGIES else "review"
            document.error_code, document.error_message = review_reason
            document.retryable = False
    if document.status == "failed":
        _invalidate_methodology_candidates_for_failed_case(document)


def _post_child_for_dify(text: str, *, title: str) -> str | None:
    """Meet Dify's custom-segment minimum without fabricating business facts."""

    value = text.strip()
    if len(value) < _DIFY_MIN_SOURCE_SEGMENT_CHARS:
        return None
    if len(value) >= _DIFY_MIN_CUSTOM_SEGMENT_CHARS:
        return value
    contextualized = (
        f"【商业顾问知识库条目】\n【主题】{title.strip()}\n"
        f"【用途】用于商业顾问问答检索\n【来源内容】{value}\n"
        "【检索边界】以下仅保留来源中的商业相关表述，用于定位和回答；"
        "未补充事实、案例、数字、承诺或结论。回答时应结合原文，不将本条扩展为新的观点。"
    )
    if len(contextualized) < _DIFY_MIN_CUSTOM_SEGMENT_CHARS:
        return None
    return contextualized


def _mark_contract_failure(
    document: CleaningDocument,
    exc: Exception,
    *,
    final: bool,
    auto_fail: bool = False,
    review_on_final: bool = False,
) -> None:
    error_code = (
        "MODEL_CALL_LIMIT_EXCEEDED"
        if isinstance(exc, _ModelCallLimitExceeded)
        else "MODEL_DAILY_BUDGET_EXCEEDED"
        if isinstance(exc, _ModelDailyBudgetExceeded)
        else "MODEL_ACCEPTANCE_BUDGET_EXCEEDED"
        if isinstance(exc, _ModelAcceptanceBudgetExceeded)
        else "MODEL_BUDGET_LOCK_UNAVAILABLE"
        if isinstance(exc, _ModelBudgetLockUnavailable)
        else "MODEL_NO_RESPONSE"
        if isinstance(exc, ModelNoResponseError)
        else "MODEL_CONTRACT_INVALID"
    )
    if final and auto_fail:
        document.status = "failed"
        document.stage = "failed"
        document.error_code = "CATALOG_AUTO_PUBLISH_BLOCKED"
        document.retryable = False
    elif final and review_on_final:
        if (
            document.metadata_json
            and document.metadata_json.get("record_type") in CONTENT_MODEL_STRATEGIES
        ):
            document.status = "failed"
            document.stage = "failed"
            document.error_code = error_code
            document.retryable = False
        else:
            document.status = "review_required"
            document.stage = "review"
            document.error_code = error_code
            document.retryable = False
    elif final:
        document.status = "failed"
        document.stage = "failed"
        document.error_code = error_code
        # A normal item retry starts at publication and must never turn a
        # failed model/OCR source candidate into a Dify request.  Only the
        # separate, bounded model-contract retry may reuse this document.
        document.retryable = False
    else:
        document.status = "awaiting_model"
        document.stage = "model_retry"
        document.error_code = error_code
        document.retryable = False
    document.error_message = _model_contract_error_message(exc)
    if document.status == "failed":
        _invalidate_methodology_candidates_for_failed_case(document)


def _plan_post_publication_unlocked(session, batch_id: str) -> list[str]:
    shard_ids: list[str] = []
    batch = session.get(CleaningBatch, batch_id)
    if batch is None:
        raise ValueError("模型分片规划缺少清洗批次")
    files = list(
        session.scalars(
            # Reconcile and model completion can both attempt to plan the
            # same file.  Serialize per-file planning so shard indexes and
            # the unique content constraint are evaluated against a stable
            # snapshot instead of racing each other.
            select(CleaningFile).where(CleaningFile.batch_id == batch_id).with_for_update()
        )
    )
    for file_row in files:
        existing = list(
            session.scalars(
                select(DifyPublicationShard).where(DifyPublicationShard.file_id == file_row.id)
            )
        )
        documents = list(
            session.scalars(
                select(CleaningDocument)
                .where(
                    CleaningDocument.file_id == file_row.id,
                    CleaningDocument.status == "shard_pending",
                    CleaningDocument.shard_id.is_(None),
                )
                .order_by(CleaningDocument.sequence)
            )
        )
        # Reconcile may run while a realtime/model task is still creating
        # post knowledge-unit children.  Existing shards therefore do not
        # mean the file is fully planned: append shards for newly-created
        # unassigned documents and only requeue retryable shards.  Succeeded
        # shards must not be submitted a second time.
        if not documents:
            shard_ids.extend(item.id for item in existing if item.status == "shard_pending")
            continue
        shard_ids.extend(item.id for item in existing if item.status == "shard_pending")
        if documents:
            from ...cleaning import finalize_knowledge_markdown

            for document in documents:
                audit = dict(document.audit_json or {})
                document.cleaned_markdown = finalize_knowledge_markdown(
                    document.cleaned_markdown,
                    audit=audit,
                )
                document.audit_json = audit
        plans = plan_post_shards(
            [
                ShardItem(
                    item_id=document.id,
                    title=document.title,
                    markdown=document.cleaned_markdown,
                )
                for document in documents
            ],
            max_items=settings.dify_shard_max_items,
            max_bytes=settings.dify_shard_max_bytes,
        )
        by_id = {document.id: document for document in documents}
        next_shard_index = max(
            (int(item.shard_index) for item in existing),
            default=0,
        )
        for plan in plans:
            from ...cleaning import finalize_knowledge_markdown

            shard_markdown = finalize_knowledge_markdown(plan.markdown)
            digest = _normalized_publication_digest(shard_markdown)
            existing_same_content = next(
                (item for item in existing if _existing_shard_publication_digest(item) == digest),
                None,
            )
            if existing_same_content is not None:
                # A local shard row, even one marked succeeded, is not proof
                # of a currently retrievable Dify result.  For the same file
                # we reuse the unique shard row as recoverable coordination:
                # failed/succeeded rows are (re)published and an in-flight
                # row keeps its existing bounded delivery.  Cross-file local
                # rows are never terminal duplicate evidence at this planner
                # seam; they receive an independent publication shard below.
                if existing_same_content.status == "shard_indexing":
                    # A publisher has already committed the claim and may be
                    # performing slow remote reads. Do not attach a later
                    # document to its cached relationship collection; the next
                    # planner pass will give this unbound row a runnable shard.
                    continue
                if existing_same_content.status in {"failed", "succeeded"}:
                    was_failed = existing_same_content.status == "failed"
                    existing_same_content.status = "shard_pending"
                    existing_same_content.last_error = ""
                    if was_failed:
                        existing_same_content.attempts = 0
                    existing_same_content.item_count = len(plan.items)
                    existing_same_content.content_bytes = len(shard_markdown.encode("utf-8"))
                    existing_same_content.markdown = shard_markdown
                    existing_metadata = dict(existing_same_content.metadata_json or {})
                    existing_metadata["publication_content_sha256"] = digest
                    existing_same_content.metadata_json = existing_metadata
                    if existing_same_content.id not in shard_ids:
                        shard_ids.append(existing_same_content.id)
                for parent_index, item in enumerate(plan.items, start=1):
                    document = by_id[item.item_id]
                    document.shard_id = existing_same_content.id
                    document.shard_parent_index = parent_index
                continue
            first_document = by_id[plan.items[0].item_id]
            from ...cleaning import STRATEGY_REGISTRY
            from .cleaning import _project_dify_metadata

            remote_candidates = list(
                session.scalars(
                    select(DifyPublicationShard)
                    .join(CleaningFile, DifyPublicationShard.file_id == CleaningFile.id)
                    .join(CleaningBatch, CleaningFile.batch_id == CleaningBatch.id)
                    .where(
                        CleaningBatch.knowledge_base_code == batch.knowledge_base_code,
                        CleaningBatch.strategy_code == batch.strategy_code,
                        CleaningBatch.spec_revision == batch.spec_revision,
                        CleaningFile.id != file_row.id,
                        DifyPublicationShard.status == "succeeded",
                        DifyPublicationShard.remote_deleted_at.is_(None),
                    )
                    .order_by(DifyPublicationShard.updated_at.desc())
                )
            )
            remote_candidate = next(
                (
                    candidate
                    for candidate in remote_candidates
                    if _existing_shard_publication_digest(candidate) == digest
                    and str(candidate.dify_document_id or "").strip()
                    and re.fullmatch(
                        r"[0-9a-f]{64}",
                        str(
                            (candidate.metadata_json or {}).get("remote_content_witness_sha256")
                            or ""
                        ).strip(),
                    )
                ),
                None,
            )
            remote_candidate_id = str(
                (remote_candidate.dify_document_id if remote_candidate is not None else "") or ""
            ).strip()
            remote_candidate_witness = str(
                (
                    (remote_candidate.metadata_json or {}).get("remote_content_witness_sha256")
                    if remote_candidate is not None
                    else ""
                )
                or ""
            ).strip()
            if not (
                remote_candidate_id and re.fullmatch(r"[0-9a-f]{64}", remote_candidate_witness)
            ):
                remote_candidate = None
                remote_candidate_id = ""
                remote_candidate_witness = ""

            shard_metadata = dict(first_document.metadata_json or {})
            shard_metadata.update(
                {
                    "record_type": str(shard_metadata.get("record_type") or "article"),
                    "content_format": "markdown",
                    "cleaning_strategy": batch.strategy_code,
                    "cleaning_spec_revision": batch.spec_revision,
                    "source_title": str(shard_metadata.get("source_title") or first_document.title),
                    "publication_content_sha256": digest,
                }
            )
            shard_metadata = _project_dify_metadata(
                shard_metadata,
                strategy=STRATEGY_REGISTRY.get(batch.strategy_code),
            )
            shard_metadata["chunking"] = {
                "doc_form": "hierarchical_model",
                "process_mode": "hierarchical",
                "parent_mode": "paragraph",
                "parent_separator": "<<<DIFY_PARENT>>>",
                "parent_max_tokens": settings.dify_parent_max_tokens,
                "child_separator": "<<<DIFY_CHILD>>>",
                "child_max_tokens": 800,
                "parent_chunk_overlap": 0,
                "child_chunk_overlap": 0,
            }
            if remote_candidate is not None:
                # This is merely a candidate.  The publisher must still
                # re-read this exact remote id, controlled metadata and the
                # actual parent/child witness before it may reuse the result.
                shard_metadata["remote_content_witness_sha256"] = remote_candidate_witness
                # A cross-file result is a verification candidate, not a
                # replacement owned by this new source.  Keep that distinction
                # durable because a metadata mismatch can legitimately require
                # a new document later, but must never delete the source that
                # first published this candidate.
                shard_metadata["_dify_remote_reference_ownership"] = "shared_candidate"
            shard = DifyPublicationShard(
                file_id=file_row.id,
                shard_index=next_shard_index + plan.index,
                status="shard_pending",
                item_count=len(plan.items),
                content_bytes=len(shard_markdown.encode("utf-8")),
                content_sha256=digest,
                markdown=shard_markdown,
                metadata_json=shard_metadata,
                dify_document_id=remote_candidate_id or None,
                dify_batch_id=(
                    str(remote_candidate.dify_batch_id or "")
                    if remote_candidate is not None
                    else None
                ),
            )
            session.add(shard)
            session.flush()
            for parent_index, item in enumerate(plan.items, start=1):
                document = by_id[item.item_id]
                document.shard_id = shard.id
                document.shard_parent_index = parent_index
            shard_ids.append(shard.id)
    session.commit()
    return shard_ids


def _plan_post_publication(session, batch_id: str) -> list[str]:
    """Plan post shards under one knowledge-base/contract publication lock."""

    batch = session.get(CleaningBatch, batch_id)
    if batch is None:
        raise ValueError("模型分片规划缺少清洗批次")
    with cache.lock(
        f"ai:content-publication:{batch.knowledge_base_code}:{batch.strategy_code}:{batch.spec_revision}",
        ttl=3600,
    ) as acquired:
        if not acquired:
            raise RuntimeError("同一清洗契约正在规划发布，请稍后重试")
        return _plan_post_publication_unlocked(session, batch_id)


def _fallback_failed_model_documents(
    session,
    batch_id: str,
    *,
    reason_code: str,
    reason_message: str,
) -> tuple[bool, list[str], list[str]]:
    """Restore a new-policy batch after a task-level model failure.

    Historical model tasks do not have the source-fallback policy snapshot.
    They must retain their original approval/retry semantics instead of being
    silently reinterpreted by a newer worker.
    """
    from ...cleaning import source_markdown_fallback_enabled
    from .cleaning import _finish_batch, _refresh_file_state

    batch = session.get(CleaningBatch, batch_id)
    if batch is None or batch.strategy_code == "course_route_catalog":
        return False, [], []
    documents = list(_iter_documents_for_batch(session, batch_id))
    candidates = [
        document
        for document in documents
        if document.status in {"awaiting_model", "model_processing"}
    ]
    if not candidates:
        return False, [], []
    if not any(
        isinstance((document.metadata_json or {}).get("_cleaning_review_policy"), dict)
        for document in candidates
    ):
        return False, [], []
    if batch.strategy_code in CONTENT_MODEL_STRATEGIES:
        touched_files: set[str] = set()
        for document in candidates:
            document.status = "failed"
            document.stage = "failed"
            document.error_code = reason_code
            document.error_message = reason_message
            document.retryable = False
            document.audit_json = dict(document.audit_json or {})
            document.audit_json.pop("source_markdown_fallback", None)
            document.__dict__.pop("_model_review_payload_for_artifact", None)
            document.__dict__.pop("_model_review_request_for_artifact", None)
            touched_files.add(str(document.file_id))
        for file_id in touched_files:
            _refresh_file_state(session, file_id, commit=False)
        _finish_batch(session, batch_id)
        return True, [], []
    touched_files: set[str] = set()
    for document in candidates:
        source_markdown = document.cleaned_markdown
        source_metadata = dict(document.metadata_json or {})
        source_chunking = dict(document.chunking_json or {})
        document.status = "failed"
        document.stage = "failed"
        document.error_code = reason_code
        document.error_message = reason_message
        document.retryable = True
        if source_markdown.strip() and source_markdown_fallback_enabled(
            batch.strategy_code, document.metadata_json or {}
        ):
            _apply_source_markdown_fallback(
                document,
                batch.strategy_code,
                source_markdown=source_markdown,
                source_metadata=source_metadata,
                source_chunking=source_chunking,
            )
        else:
            document.retryable = False
            document.error_code = "MODEL_FALLBACK_SOURCE_EMPTY"
            document.error_message = (
                "模型处理失败且当前策略未启用源 Markdown 降级"
                if source_markdown.strip()
                else "模型处理失败且没有可入库的清洗源 Markdown"
            )
        touched_files.add(str(document.file_id))
    for file_id in touched_files:
        _refresh_file_state(session, file_id, commit=False)
    pending_ids: list[str] = []
    shard_ids: list[str] = []
    if batch.strategy_code in POST_STRATEGIES:
        shard_ids = _plan_post_publication(session, batch_id)
    else:
        first_document_by_file: dict[str, str] = {}
        for document in documents:
            if document.status == "pending":
                first_document_by_file.setdefault(str(document.file_id), str(document.id))
        pending_ids = list(first_document_by_file.values())
    _finish_batch(session, batch_id)
    return True, pending_ids, shard_ids


def _handle_terminal_batch_result_error(
    session,
    task: ModelCleaningTask,
    batch: CleaningBatch,
    exc: Exception,
) -> tuple[list[str], list[str]]:
    """Close a terminal Batch whose output cannot be parsed or reconciled."""

    from .cleaning import _finish_batch, _refresh_file_state

    pending_ids: list[str] = []
    shard_ids: list[str] = []
    fallback_applied, pending_ids, shard_ids = _fallback_failed_model_documents(
        session,
        batch.id,
        reason_code="MODEL_BATCH_RESULT_INVALID",
        reason_message="百炼 Batch 结果无法解析或未通过 custom_id 守恒校验",
    )
    if not fallback_applied:
        touched_files: set[str] = set()
        for document in _iter_documents_for_batch(session, batch.id):
            _mark_contract_failure(
                document,
                exc,
                final=True,
            )
            document.error_code = "MODEL_BATCH_RESULT_INVALID"
            touched_files.add(str(document.file_id))
        for file_id in touched_files:
            _refresh_file_state(session, file_id, commit=False)
        _finish_batch(session, batch.id)
    else:
        batch.status = "processing"

    documents = list(
        session.scalars(
            select(CleaningDocument).join(CleaningFile).where(CleaningFile.batch_id == batch.id)
        )
    )
    previous = {
        key: value
        for key, value in (task.result_counts or {}).items()
        if key != "provider_terminal_batches"
    }
    task.result_counts = merge_result_counts_preserving_provider_ledger(
        task,
        {
            **previous,
            "candidate_count": task.candidate_count,
            "succeeded": int(previous.get("succeeded") or 0),
            "excluded": int(previous.get("excluded") or 0),
            "review": int(previous.get("review") or 0),
            "fallback": sum(
                1
                for document in documents
                if (document.audit_json or {}).get("source_markdown_fallback", {}).get("applied")
            ),
            "failed": sum(1 for document in documents if document.status == "failed"),
        },
    )
    task.provider_batch_id = None
    task.provider_batch_ids = []
    task.provider_input_file_ids = []
    task.provider_output_file_ids = []
    task.provider_error_file_ids = []
    task.status = "failed"
    task.last_error = (
        "百炼Batch结果解析失败，已停止轮询：" + safe_exception_message(exc, limit=1800)
    )[:2000]
    return pending_ids, shard_ids


def _queue_publication_outputs(
    pending_document_ids: Iterable[str], shard_ids: Iterable[str]
) -> None:
    for document_id in pending_document_ids:
        from .cleaning import process_cleaning_document

        process_cleaning_document.apply_async(args=[document_id], queue="ai_publication")
    for shard_id in shard_ids:
        publish_dify_shard.apply_async(args=[shard_id], queue="ai_publication")


def _validate_shard_structure(structure: dict[str, Any], *, item_count: int) -> None:
    """Allow Dify to split an oversized source item without losing any item.

    Each source item is separated before publication.  Dify may legitimately
    split that item again at its configured parent-token limit, so the remote
    parent count is a lower-bound check, never an equality check.
    """

    parent_count = int(structure.get("parent_count") or 0)
    if parent_count < item_count:
        raise ValueError(f"Dify父块数量不足：{parent_count} < {item_count}")


def _finish_model_task(task_id: str, *, lease_token: str | None = None) -> None:
    with SessionLocal() as session:
        task = session.scalar(
            select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
        )
        if task is None:
            return
        if task.status == "cancelled":
            return
        if lease_token is not None:
            _assert_active_model_lease(session, task_id, lease_token)
        batch = session.get(CleaningBatch, task.batch_id)
        task.status = "completed"
        task.last_error = ""
        task.lease_token = None
        task.lease_expires_at = None
        # Model completion changes document statuses directly.  Refresh every
        # source file before aggregating the batch so a normal strategy with a
        # review-required document cannot remain falsely in awaiting_model /
        # processing after the model task is terminal.
        from .cleaning import _finish_batch, _refresh_file_state

        file_ids = list(
            session.scalars(select(CleaningFile.id).where(CleaningFile.batch_id == task.batch_id))
        )
        for file_id in file_ids:
            _refresh_file_state(session, str(file_id), commit=False)
        if (
            batch
            and batch.strategy_code == "course_route_catalog"
            and settings.course_catalog_auto_publish
        ):
            catalog_documents = list(
                session.scalars(
                    select(CleaningDocument)
                    .join(CleaningFile)
                    .where(CleaningFile.batch_id == batch.id)
                )
            )
            if any(item.status == "failed" for item in catalog_documents):
                # A catalogue source is atomic.  Successful candidates must
                # not be published when a sibling model result failed.
                for item in catalog_documents:
                    if item.status not in {"failed", "excluded"}:
                        item.status = "failed"
                        item.stage = "failed"
                        item.error_code = "CATALOG_AUTO_PUBLISH_BLOCKED"
                        item.error_message = "同源课程目录存在模型失败条目，整文件未发布"
                        item.retryable = False
                # The first refresh ran before the atomic sibling failure was
                # applied, so refresh again to prevent a terminal failed
                # catalogue from being left at file-level ``processing``.
                for file_id in file_ids:
                    _refresh_file_state(session, str(file_id), commit=False)
                _finish_batch(session, batch.id)
                session.commit()
                return
        if batch and batch.strategy_code in POST_STRATEGIES:
            # A batch can contain a publishable post file and an all-review
            # file.  Refresh every file before queuing shards so the latter
            # never remains falsely stuck in awaiting_model.
            shard_ids = _plan_post_publication(session, batch.id)
            if shard_ids:
                batch.status = "partially_completed"
            else:
                _finish_batch(session, batch.id)
            session.commit()
            for shard_id in shard_ids:
                publish_dify_shard.apply_async(args=[shard_id], queue="ai_publication")
        elif batch:
            pending_documents = list(
                session.execute(
                    select(CleaningDocument.file_id, CleaningDocument.id)
                    .join(CleaningFile)
                    .where(
                        CleaningFile.batch_id == batch.id,
                        CleaningDocument.status == "pending",
                    )
                    .order_by(
                        CleaningFile.created_at,
                        CleaningDocument.sequence,
                    )
                )
            )
            first_document_by_file: dict[str, str] = {}
            for file_id, document_id in pending_documents:
                first_document_by_file.setdefault(str(file_id), str(document_id))
            if first_document_by_file:
                from .cleaning import process_cleaning_document

                for document_id in first_document_by_file.values():
                    process_cleaning_document.apply_async(
                        args=[document_id], queue="ai_publication"
                    )
            has_pending = bool(first_document_by_file)
            if has_pending:
                batch.status = "processing"
            else:
                remaining = session.scalar(
                    select(CleaningDocument.id)
                    .join(CleaningFile)
                    .where(
                        CleaningFile.batch_id == batch.id,
                        CleaningDocument.status.in_(
                            (
                                "awaiting_model",
                                "model_processing",
                                "pending",
                                "processing",
                                "staged",
                            )
                        ),
                    )
                    .limit(1)
                )
                if remaining is None:
                    from .cleaning import _finish_batch

                    _finish_batch(session, batch.id)
            session.commit()


@celery_app.task(
    name="ai_management.process_realtime_model_task",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def process_realtime_model_task(self, task_id: str) -> bool:
    client: BailianClient | None = None
    token = ""
    fallback_pending_ids: list[str] = []
    fallback_shard_ids: list[str] = []
    try:
        client = _client()
        with SessionLocal() as session:
            task = session.get(ModelCleaningTask, task_id)
            if task is None or task.status not in {"queued", "running"}:
                return False
            batch = session.get(CleaningBatch, task.batch_id)
            if batch is None:
                return False
            now = datetime.now(timezone.utc)
            token = uuid.uuid4().hex
            claimed = session.execute(
                update(ModelCleaningTask)
                .where(ModelCleaningTask.id == task_id)
                .where(ModelCleaningTask.status.in_(["queued", "running"]))
                .where(
                    (ModelCleaningTask.lease_expires_at.is_(None))
                    | (ModelCleaningTask.lease_expires_at < now)
                )
                .values(
                    status="running",
                    attempts=ModelCleaningTask.attempts + 1,
                    lease_token=token,
                    lease_expires_at=now + _MODEL_LEASE_DURATION,
                )
            ).rowcount
            if claimed != 1:
                return False
            task = session.get(ModelCleaningTask, task_id)
            if task is None:
                return False
            session.refresh(task)
            session.execute(
                update(CleaningDocument)
                .where(
                    CleaningDocument.file_id.in_(
                        select(CleaningFile.id).where(CleaningFile.batch_id == batch.id)
                    ),
                    CleaningDocument.status == "model_processing",
                )
                .values(status="awaiting_model", stage="model_retry")
            )
            session.commit()
            counts = {"succeeded": 0, "excluded": 0, "review": 0, "failed": 0}
            usage = {
                key: max(0, int((task.actual_usage or {}).get(key) or 0))
                for key in ("input_tokens", "output_tokens")
            }
            for page in _iter_realtime_document_pages(session, batch.id):
                for document in page:
                    _renew_active_model_lease(session, task_id, token)
                    document.status = "model_processing"
                    document.stage = "model_running"
                    source_markdown = document.cleaned_markdown
                    source_metadata = dict(document.metadata_json or {})
                    source_chunking = dict(document.chunking_json or {})
                    session.commit()
                    payload: dict[str, Any] | None = None
                    try:
                        request = _request(document, batch.strategy_code)
                        _store_fenced_model_review_request_context(
                            session,
                            document,
                            request,
                            task_id=task_id,
                            lease_token=token,
                            model_name=task.model,
                            prompt_version=task.prompt_version,
                        )
                        _wait_for_realtime_capacity(
                            request,
                            model=task.model,
                            before_request=lambda: _renew_active_model_lease(
                                session, task_id, token
                            ),
                        )
                        call_id = _reserve_provider_call(
                            session, task, document, request, lease_token=token
                        )
                        payload, item_usage = _realtime_chat(client, request, model=task.model)
                        _persist_realtime_usage(
                            session, task, usage, item_usage, call_id=call_id, lease_token=token
                        )
                        _assert_active_model_lease(session, task_id, token)
                        _apply_model_payload(
                            document,
                            batch.strategy_code,
                            request,
                            payload,
                            session=session,
                            model_name=task.model,
                            prompt_version=task.prompt_version,
                        )
                        _assert_active_model_lease(session, task_id, token)
                        _finalize_model_document(document, strategy_code=batch.strategy_code)
                        _apply_source_markdown_fallback(
                            document,
                            batch.strategy_code,
                            source_markdown=source_markdown,
                            source_metadata=source_metadata,
                            source_chunking=source_chunking,
                        )
                    except ModelNoResponseError:
                        try:
                            request = _request(document, batch.strategy_code)
                            _store_fenced_model_review_request_context(
                                session,
                                document,
                                request,
                                task_id=task_id,
                                lease_token=token,
                                model_name=task.model,
                                prompt_version=task.prompt_version,
                            )
                            _wait_for_realtime_capacity(
                                request,
                                model=task.model,
                                before_request=lambda: _renew_active_model_lease(
                                    session, task_id, token
                                ),
                            )
                            call_id = _reserve_provider_call(
                                session, task, document, request, lease_token=token
                            )
                            payload, item_usage = _realtime_chat(client, request, model=task.model)
                            _persist_realtime_usage(
                                session, task, usage, item_usage, call_id=call_id, lease_token=token
                            )
                            _assert_active_model_lease(session, task_id, token)
                            _apply_model_payload(
                                document,
                                batch.strategy_code,
                                request,
                                payload,
                                session=session,
                                model_name=task.model,
                                prompt_version=task.prompt_version,
                            )
                            _assert_active_model_lease(session, task_id, token)
                            _finalize_model_document(document, strategy_code=batch.strategy_code)
                            _apply_source_markdown_fallback(
                                document,
                                batch.strategy_code,
                                source_markdown=source_markdown,
                                source_metadata=source_metadata,
                                source_chunking=source_chunking,
                            )
                        except Exception as retry_exc:
                            _raise_if_model_lease_lost(retry_exc)
                            _renew_active_model_lease(session, task_id, token)
                            _mark_contract_failure(
                                document,
                                retry_exc,
                                final=True,
                                auto_fail=(
                                    batch.strategy_code == "course_route_catalog"
                                    and settings.course_catalog_auto_publish
                                ),
                                review_on_final=(
                                    batch.strategy_code == "course_route_catalog"
                                    and not settings.course_catalog_auto_publish
                                ),
                            )
                            _apply_source_markdown_fallback(
                                document,
                                batch.strategy_code,
                                source_markdown=source_markdown,
                                source_metadata=source_metadata,
                                source_chunking=source_chunking,
                            )
                    except Exception as original_exc:
                        _raise_if_model_lease_lost(original_exc)
                        contract_exc = original_exc
                        repaired = False
                        if batch.strategy_code in CONTENT_MODEL_STRATEGIES and (
                            not isinstance(original_exc, _ModelCallLimitExceeded)
                            and (
                                batch.strategy_code != "personal_business_consultation"
                                or not (
                                    isinstance(payload, dict)
                                    and str(payload.get("schema_version") or "")
                                    == "consultation-rag-v1"
                                )
                            )
                        ):
                            # Content-model semantic/transport failures get one
                            # complete replay of the same anonymized request.
                            # Keeping this in the same worker attempt prevents a
                            # Celery/message redelivery from creating a third
                            # provider call.
                            try:
                                request = _request(document, batch.strategy_code)
                                _store_fenced_model_review_request_context(
                                    session,
                                    document,
                                    request,
                                    task_id=task_id,
                                    lease_token=token,
                                    model_name=task.model,
                                    prompt_version=task.prompt_version,
                                )
                                _wait_for_realtime_capacity(
                                    request,
                                    model=task.model,
                                    before_request=lambda: _renew_active_model_lease(
                                        session, task_id, token
                                    ),
                                )
                                call_id = _reserve_provider_call(
                                    session, task, document, request, lease_token=token
                                )
                                payload, item_usage = _realtime_chat(
                                    client, request, model=task.model
                                )
                                _persist_realtime_usage(
                                    session,
                                    task,
                                    usage,
                                    item_usage,
                                    call_id=call_id,
                                    lease_token=token,
                                )
                                _assert_active_model_lease(session, task_id, token)
                                _apply_model_payload(
                                    document,
                                    batch.strategy_code,
                                    request,
                                    payload,
                                    session=session,
                                    model_name=task.model,
                                    prompt_version=task.prompt_version,
                                )
                                _assert_active_model_lease(session, task_id, token)
                                _finalize_model_document(
                                    document, strategy_code=batch.strategy_code
                                )
                                repaired = True
                            except Exception as retry_exc:
                                _raise_if_model_lease_lost(retry_exc)
                                contract_exc = retry_exc
                        elif (
                            batch.strategy_code == "personal_business_consultation"
                            and not isinstance(original_exc, _ModelCallLimitExceeded)
                            and isinstance(payload, dict)
                            and str(payload.get("schema_version") or "") == "consultation-rag-v1"
                        ):
                            # The standalone cleaner allows one bounded
                            # semantic repair before taking the conservative
                            # whole-source fallback.  Keep the hint structural
                            # and never echo the failed model payload.
                            try:
                                audit = dict(document.audit_json or {})
                                audit["model_repair_attempted"] = True
                                document.audit_json = audit
                                request = _request(document, batch.strategy_code)
                                repair_hint = (
                                    "只修复 consultation-rag-v1 的 JSON/证据契约；"
                                    "每个案例必须有核心问题、必要情境和至少一条可回溯证据，"
                                    "retrieval_queries 可为空且不得新增事实；"
                                    "不要改变事实、不要新增案例或人物。\n"
                                    + _model_contract_retry_hint(contract_exc)
                                )
                                _store_fenced_model_review_request_context(
                                    session,
                                    document,
                                    request,
                                    task_id=task_id,
                                    lease_token=token,
                                    model_name=task.model,
                                    prompt_version=task.prompt_version,
                                    repair_hint=repair_hint,
                                )
                                _wait_for_realtime_capacity(
                                    request,
                                    model=task.model,
                                    repair_hint=repair_hint,
                                    before_request=lambda: _renew_active_model_lease(
                                        session, task_id, token
                                    ),
                                )
                                call_id = _reserve_provider_call(
                                    session,
                                    task,
                                    document,
                                    request,
                                    repair_hint=repair_hint,
                                    lease_token=token,
                                )
                                payload, item_usage = _realtime_chat(
                                    client,
                                    request,
                                    model=task.model,
                                    repair_hint=repair_hint,
                                )
                                _persist_realtime_usage(
                                    session,
                                    task,
                                    usage,
                                    item_usage,
                                    call_id=call_id,
                                    lease_token=token,
                                )
                                _assert_active_model_lease(session, task_id, token)
                                _apply_model_payload(
                                    document,
                                    batch.strategy_code,
                                    request,
                                    payload,
                                    session=session,
                                    model_name=task.model,
                                    prompt_version=task.prompt_version,
                                    repair_hint=repair_hint,
                                )
                                _assert_active_model_lease(session, task_id, token)
                                _finalize_model_document(
                                    document, strategy_code=batch.strategy_code
                                )
                                _apply_source_markdown_fallback(
                                    document,
                                    batch.strategy_code,
                                    source_markdown=source_markdown,
                                    source_metadata=source_metadata,
                                    source_chunking=source_chunking,
                                )
                                repaired = True
                            except Exception as repair_exc:
                                _raise_if_model_lease_lost(repair_exc)
                                contract_exc = repair_exc
                        if not repaired:
                            _renew_active_model_lease(session, task_id, token)
                            _mark_contract_failure(
                                document,
                                contract_exc,
                                final=True,
                                auto_fail=(
                                    batch.strategy_code == "course_route_catalog"
                                    and settings.course_catalog_auto_publish
                                ),
                                review_on_final=(
                                    batch.strategy_code == "course_route_catalog"
                                    and not settings.course_catalog_auto_publish
                                ),
                            )
                            _apply_source_markdown_fallback(
                                document,
                                batch.strategy_code,
                                source_markdown=source_markdown,
                                source_metadata=source_metadata,
                                source_chunking=source_chunking,
                            )
                    _renew_active_model_lease(session, task_id, token)
                    _count_model_result(counts, document)
                    session.commit()
            _assert_active_model_lease(session, task_id, token)
            task.result_counts = merge_result_counts_preserving_provider_ledger(task, counts)
            task.actual_usage = usage
            session.commit()
        _finish_model_task(task_id, lease_token=token)
        return True
    except Exception as exc:
        if isinstance(exc, _ModelLeaseLost):
            logger.info("realtime model worker lost lease; discarded stale side effects")
            return False
        logger.exception("realtime model cleaning task failed")
        with SessionLocal() as session:
            task = session.get(ModelCleaningTask, task_id)
            if task is not None:
                try:
                    _assert_active_model_lease(session, task_id, token)
                except _ModelLeaseLost:
                    logger.info("realtime model worker lost lease during failure recovery")
                    return False
                task.status = "failed"
                task.last_error = safe_exception_message(exc, limit=2000)
                task.lease_token = None
                task.lease_expires_at = None
                batch = session.get(CleaningBatch, task.batch_id)
                if (
                    batch is not None
                    and batch.strategy_code == "course_route_catalog"
                    and settings.course_catalog_auto_publish
                ):
                    _fail_catalog_auto_run(session, batch, "模型任务执行异常")
                elif batch is not None:
                    _, fallback_pending_ids, fallback_shard_ids = _fallback_failed_model_documents(
                        session,
                        batch.id,
                        reason_code="MODEL_TASK_FAILED",
                        reason_message="模型任务执行失败，已使用清洗源 Markdown 继续入库",
                    )
                    if fallback_pending_ids or fallback_shard_ids:
                        batch.status = "processing"
                record_event(
                    level="ERROR",
                    category="task",
                    component="model_cleaning_worker",
                    event_code="MODEL_TASK_FAILED",
                    status="failed",
                    message="模型清洗任务失败",
                    resource_type="model_cleaning_task",
                    resource_id=task_id,
                    details=exception_details(
                        exc,
                        phase="realtime_model_task",
                        task_id=task_id,
                        batch_id=task.batch_id,
                        strategy_code=(batch.strategy_code if batch else ""),
                    ),
                    session=session,
                )
                session.commit()
        _queue_publication_outputs(fallback_pending_ids, fallback_shard_ids)
        return False
    finally:
        if client:
            client.close()


@dataclass
class _BatchInputFile:
    """One local JSONL and the exact provider requests it contains."""

    path: Path
    requests: list[tuple[CleaningDocument, PromptRequest, str]] = field(default_factory=list)


def _record_provider_batch_files(
    task: ModelCleaningTask,
    provider_batch_id: str,
    **file_ids: str,
) -> None:
    """Durably merge only one provider Batch's owned file IDs."""

    result_counts = dict(task.result_counts or {})
    batch_file_map = dict(result_counts.get("provider_batch_file_map") or {})
    owned_files = dict(batch_file_map.get(provider_batch_id) or {})
    owned_files.update({key: value for key, value in file_ids.items() if value})
    batch_file_map[provider_batch_id] = owned_files
    result_counts["provider_batch_file_map"] = batch_file_map
    task.result_counts = result_counts


def _write_batch_files(
    directory: Path,
    task: ModelCleaningTask,
    batch: CleaningBatch,
    documents: Iterable[CleaningDocument],
    *,
    session=None,
    repair_hints: dict[str, str] | None = None,
) -> list[_BatchInputFile]:
    inputs: list[_BatchInputFile] = []
    handle = None
    request_count = 0
    current_bytes = 0
    try:
        for document in documents:
            request = _request(document, batch.strategy_code)
            repair_hint = (repair_hints or {}).get(document.id, "")
            _store_model_review_request_context(
                document,
                request,
                model_name=task.model,
                prompt_version=task.prompt_version,
                repair_hint=repair_hint,
            )
            line = batch_request_line(
                request,
                prompt_version=task.prompt_version,
                model=task.model,
                repair_hint=repair_hint,
                max_tokens=settings.cleaning_model_max_tokens,
            )
            if len(line) > 1_000_000:
                raise ValueError("单条Batch请求超过1MB")
            if handle is None or (
                request_count >= settings.cleaning_batch_max_requests
                or current_bytes + len(line) > settings.cleaning_batch_max_bytes
            ):
                if handle is not None:
                    handle.close()
                path = directory / f"batch-{len(inputs) + 1:04d}.jsonl"
                inputs.append(_BatchInputFile(path=path))
                handle = path.open("wb")
                request_count = 0
                current_bytes = 0
            handle.write(line)
            inputs[-1].requests.append((document, request, repair_hint))
            request_count += 1
            current_bytes += len(line)
    finally:
        if handle is not None:
            handle.close()
    return inputs


def _recover_created_provider_batches(
    task_id: str,
    provider_batch_ids: list[str],
    provider_input_file_ids: list[str],
    reason: str,
) -> bool | str:
    """Persist remote IDs after a commit failure in the create window."""
    if not provider_batch_ids:
        return False
    with SessionLocal() as session:
        task = session.scalar(
            select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
        )
        if task is None:
            return False
        batch = session.get(CleaningBatch, task.batch_id)
        if task.status == "cancelled":
            # The remote create may have completed after the cancelling
            # request committed.  Persist the IDs without reviving the local
            # task; the submitter will cancel these exact IDs before return.
            task.provider_batch_ids = list(provider_batch_ids)
            task.provider_batch_id = provider_batch_ids[0]
            task.provider_input_file_ids = list(provider_input_file_ids)
            task.last_error = reason[:2000]
            session.commit()
            return "cancelled"
        task.provider_batch_ids = list(provider_batch_ids)
        task.provider_batch_id = provider_batch_ids[0]
        task.provider_input_file_ids = list(provider_input_file_ids)
        task.provider_output_file_ids = []
        task.provider_error_file_ids = []
        result_counts = dict(task.result_counts or {})
        result_counts.pop("provider_terminal_batches", None)
        task.result_counts = result_counts
        task.status = "batch_running"
        task.last_error = reason[:2000]
        if batch is not None:
            batch.status = "model_batch_running"
        for document in _iter_documents_for_batch(session, task.batch_id):
            document.status = "model_processing"
            document.stage = "model_batch_running"
        session.commit()
    return True


@celery_app.task(
    name="ai_management.submit_batch_model_task",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def submit_batch_model_task(self, task_id: str) -> bool:
    client: BailianClient | None = None
    created_batch_ids: list[str] = []
    created_input_ids: list[str] = []
    fallback_pending_ids: list[str] = []
    fallback_shard_ids: list[str] = []
    try:
        with SessionLocal() as session:
            task = session.scalar(
                select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
            )
            if task is None or task.status not in {"approved", "batch_uploading"}:
                return False
            if task.approval_fingerprint != task.candidate_fingerprint:
                raise ValueError("候选集已变化，费用确认失效")
            batch = session.get(CleaningBatch, task.batch_id)
            candidate_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(CleaningDocument)
                    .join(CleaningFile)
                    .where(
                        CleaningFile.batch_id == task.batch_id,
                        CleaningDocument.status.in_(("awaiting_model", "model_processing")),
                    )
                )
                or 0
            )
            if batch is None or candidate_count != task.candidate_count:
                raise ValueError("模型候选数量与审批快照不一致")
            current_fingerprint = _candidate_fingerprint(
                _iter_documents_for_batch(session, task.batch_id),
                task.prompt_version,
            )
            if current_fingerprint != task.candidate_fingerprint:
                raise ValueError("模型候选内容已变化，费用确认失效")
            task.status = "batch_uploading"
            batch.status = "model_batch_uploading"
            session.commit()
            task = session.scalar(
                select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
            )
            if task is None or task.status != "batch_uploading":
                return False
            persisted_batch_ids = get_provider_batch_ids(task)
            if persisted_batch_ids:
                # Acknowledged remote Batch IDs are durable proof that a
                # provider request may already be executing or billable.  A
                # Celery redelivery therefore resumes polling; cancelling and
                # recreating here would violate the document-level two-call
                # cap and can create duplicate charges.
                task.status = "batch_running"
                task.provider_batch_ids = list(persisted_batch_ids)
                task.provider_batch_id = persisted_batch_ids[0]
                batch.status = "model_batch_running"
                for document in _iter_documents_for_batch(session, task.batch_id):
                    document.status = "model_processing"
                    document.stage = "model_batch_running"
                session.commit()
                poll_batch_model_task.apply_async(
                    args=[task_id], queue="ai_model_batch", countdown=60
                )
                return True
            if task.provider_input_file_ids:
                # If upload succeeded but the create response was lost, a
                # second create is not provably free of a duplicate provider
                # call.  Preserve the slot reservation and fail closed rather
                # than treating an uncertain remote side effect as retryable.
                from .cleaning import _finish_batch, _refresh_file_state

                for document in _iter_documents_for_batch(session, task.batch_id):
                    _mark_contract_failure(document, ValueError("Batch提交状态不确定"), final=True)
                    document.error_code = "MODEL_BATCH_SUBMIT_UNCERTAIN"
                    document.error_message = "Batch 输入已上传但未保存远端任务 ID，未重新提交"
                    document.retryable = False
                for file_row in batch.files:
                    _refresh_file_state(session, file_row.id, commit=False)
                task.status = "failed"
                task.last_error = "Batch输入已上传但未保存远端任务 ID，未重新提交"
                _finish_batch(session, batch.id)
                session.commit()
                return False
            client = _client()
            with tempfile.TemporaryDirectory(prefix="jx-bailian-") as directory:
                inputs = _write_batch_files(
                    Path(directory),
                    task,
                    batch,
                    _iter_documents_for_batch(session, task.batch_id),
                    session=session,
                )
                session.commit()
                if not inputs:
                    from .cleaning import _finish_batch, _refresh_file_state

                    task = session.scalar(
                        select(ModelCleaningTask)
                        .where(ModelCleaningTask.id == task_id)
                        .with_for_update()
                    )
                    if task is None:
                        return False
                    for file_row in batch.files:
                        _refresh_file_state(session, file_row.id, commit=False)
                    task.status = "failed"
                    task.last_error = "没有可提交的模型候选；每个候选已保留各自失败原因"
                    _finish_batch(session, batch.id)
                    session.commit()
                    return False
                input_ids: list[str] = []
                batch_ids: list[str] = []
                for batch_input in inputs:
                    task = session.scalar(
                        select(ModelCleaningTask)
                        .where(ModelCleaningTask.id == task_id)
                        .with_for_update()
                    )
                    if task is None or task.status != "batch_uploading":
                        session.rollback()
                        return False
                    input_id = client.upload_batch_path(str(batch_input.path))
                    input_ids.append(input_id)
                    created_input_ids.append(input_id)
                    task = session.scalar(
                        select(ModelCleaningTask)
                        .where(ModelCleaningTask.id == task_id)
                        .with_for_update()
                    )
                    if task is not None:
                        session.refresh(task)
                    if task is None or task.status != "batch_uploading":
                        session.rollback()
                        _cleanup_cancelled_batch_input(client, task_id, input_id)
                        return False
                    task.provider_input_file_ids = list(input_ids)
                    session.commit()
                    task = session.scalar(
                        select(ModelCleaningTask)
                        .where(ModelCleaningTask.id == task_id)
                        .with_for_update()
                    )
                    if task is not None:
                        session.refresh(task)
                    if task is None or task.status != "batch_uploading":
                        session.rollback()
                        _cleanup_cancelled_batch_input(client, task_id, input_id)
                        return False
                    for document, request, repair_hint in batch_input.requests:
                        _reserve_provider_call(
                            session,
                            task,
                            document,
                            request,
                            repair_hint=repair_hint,
                        )
                    batch_id = client.create_batch(input_id)
                    batch_ids.append(batch_id)
                    created_batch_ids.append(batch_id)
                    task.provider_batch_ids = list(batch_ids)
                    task.provider_batch_id = batch_ids[0]
                    _record_provider_batch_files(task, batch_id, input_file_id=input_id)
                    session.commit()
            task = session.scalar(
                select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
            )
            if task is None or task.status != "batch_uploading":
                return False
            task.provider_input_file_ids = input_ids
            task.provider_batch_ids = batch_ids
            task.provider_batch_id = batch_ids[0] if batch_ids else None
            task.status = "batch_running"
            batch.status = "model_batch_running"
            for document in _iter_documents_for_batch(session, task.batch_id):
                document.status = "model_processing"
                document.stage = "model_batch_running"
            session.commit()
        poll_batch_model_task.apply_async(args=[task_id], queue="ai_model_batch", countdown=60)
        return True
    except Exception as exc:
        logger.exception("batch model submit failed")
        if created_batch_ids:
            try:
                recovered = _recover_created_provider_batches(
                    task_id,
                    created_batch_ids,
                    created_input_ids,
                    "Batch 已在供应商创建，但本地提交失败；已恢复远端 ID 并继续轮询",
                )
            except Exception:
                logger.exception("failed to recover provider batch IDs after commit failure")
                recovered = False
            if recovered == "cancelled":
                if client is not None:
                    _cleanup_cancelled_provider_batches(
                        client,
                        task_id,
                        created_batch_ids,
                        created_input_ids,
                    )
                return False
            if recovered:
                poll_batch_model_task.apply_async(
                    args=[task_id], queue="ai_model_batch", countdown=60
                )
                return False
            if client is not None and (created_batch_ids or created_input_ids):
                # Recovery can fail after the provider accepted a Batch. Do
                # exact compensating cleanup before entering the local retry
                # path so returned output/error files are not orphaned.
                cleanup_pending = _cleanup_cancelled_provider_batches(
                    client,
                    task_id,
                    created_batch_ids,
                    created_input_ids,
                )
                if cleanup_pending:
                    # A missing/unknown/cancelling response is not evidence
                    # that the remote Batch is gone.  Keep its precise IDs,
                    # cleanup ownership and original approval durable rather
                    # than falling through to a second cancel/reapproval.
                    return False
        replan_batch_id = ""
        with SessionLocal() as session:
            task = session.scalar(
                select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
            )
            if task:
                cancelled_task = task.status == "cancelled"
                task.last_error = safe_exception_message(exc, limit=2000)
                batch = session.get(CleaningBatch, task.batch_id)
                if client:
                    partial_cancel_failed = False
                    cleanup_file_ids = {
                        str(file_id)
                        for file_id in [
                            *(task.provider_input_file_ids or []),
                            *(task.provider_output_file_ids or []),
                            *(task.provider_error_file_ids or []),
                        ]
                        if file_id
                    }
                    for provider_batch_id in get_provider_batch_ids(task):
                        try:
                            cancelled = client.cancel_batch(provider_batch_id)
                            cleanup_file_ids.update(
                                str(cancelled.get(field) or "")
                                for field in ("output_file_id", "error_file_id")
                                if cancelled.get(field)
                            )
                        except Exception as cancel_exc:
                            partial_cancel_failed = True
                            logger.exception("failed to cancel partial Bailian batch")
                            existing = session.scalar(
                                select(ExternalDeletionTask)
                                .where(
                                    ExternalDeletionTask.kind == "bailian_batch",
                                    ExternalDeletionTask.external_id == provider_batch_id,
                                )
                                .limit(1)
                            )
                            if existing is None:
                                session.add(
                                    ExternalDeletionTask(
                                        kind="bailian_batch",
                                        external_id=provider_batch_id,
                                        payload={"model_task_id": task.id},
                                        attempts=0,
                                        last_error=safe_exception_message(
                                            cancel_exc,
                                            limit=1000,
                                        ),
                                    )
                                )
                    _delete_provider_files(
                        client,
                        task,
                        sorted(cleanup_file_ids),
                    )
                    if partial_cancel_failed:
                        task.cleanup_status = "pending_retry"
                    task.provider_batch_ids = []
                    task.provider_batch_id = None
                    task.provider_input_file_ids = []
                    task.provider_output_file_ids = []
                    task.provider_error_file_ids = []
                    if not cancelled_task:
                        if (
                            batch is not None
                            and batch.strategy_code == "course_route_catalog"
                            and settings.course_catalog_auto_publish
                        ):
                            task.status = "failed"
                            _fail_catalog_auto_run(session, batch, "模型批量任务提交异常")
                        else:
                            task.status = "awaiting_approval"
                            task.approval_fingerprint = ""
                            task.approved_at = None
                            if batch:
                                batch.status = "awaiting_model_approval"
                elif not cancelled_task:
                    if (
                        batch is not None
                        and batch.strategy_code == "course_route_catalog"
                        and settings.course_catalog_auto_publish
                    ):
                        task.status = "failed"
                        _fail_catalog_auto_run(session, batch, "模型批量任务初始化异常")
                    else:
                        task.status = "superseded"
                        replan_batch_id = task.batch_id
                        if batch:
                            batch.status = "processing"
                if not cancelled_task and not (
                    batch is not None
                    and batch.strategy_code == "course_route_catalog"
                    and settings.course_catalog_auto_publish
                ):
                    for document in _iter_documents_for_batch(session, task.batch_id):
                        document.status = "awaiting_model"
                        document.stage = "model_retry"
                        document.error_code = "MODEL_BATCH_SUBMIT_FAILED"
                        document.error_message = "Batch提交未完成，已进入模型重试"
                    if batch is not None:
                        fallback_applied, fallback_pending_ids, fallback_shard_ids = (
                            _fallback_failed_model_documents(
                                session,
                                batch.id,
                                reason_code="MODEL_BATCH_SUBMIT_FAILED",
                                reason_message="Batch 提交失败，已使用清洗源 Markdown 继续入库",
                            )
                        )
                        if fallback_applied:
                            task.status = "failed"
                            task.approval_fingerprint = ""
                            task.approved_at = None
                            batch.status = "processing"
                session.commit()
        _queue_publication_outputs(fallback_pending_ids, fallback_shard_ids)
        if replan_batch_id and not (fallback_pending_ids or fallback_shard_ids):
            maybe_plan_model_task(replan_batch_id)
        return False
    finally:
        if client:
            client.close()


def _parse_provider_output(content: bytes) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(content.splitlines(), start=1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        custom_id = str(row.get("custom_id") or "")
        if not custom_id or custom_id in results:
            raise ValueError(f"Batch结果custom_id缺失或重复：第{line_number}行")
        response = row.get("response") or {}
        body = response.get("body") or {}
        content_text = body["choices"][0]["message"]["content"]
        payload = json.loads(content_text)
        if not isinstance(payload, dict):
            raise ValueError("Batch模型结果必须是JSON对象")
        results[custom_id] = {
            "payload": payload,
            "usage": body.get("usage") or {},
        }
    return results


def _delete_provider_files(
    client: BailianClient,
    task: ModelCleaningTask,
    file_ids: list[str],
) -> list[str]:
    failed: list[str] = []
    for file_id in sorted(set(file_ids)):
        try:
            client.delete_file(file_id)
        except Exception as exc:
            failed.append(file_id)
            with SessionLocal() as cleanup_session:
                cleanup_session.add(
                    ExternalDeletionTask(
                        kind="bailian_file",
                        external_id=file_id,
                        payload={"model_task_id": task.id},
                        attempts=0,
                        last_error=safe_exception_message(exc, limit=1000),
                    )
                )
                cleanup_session.commit()
    if failed:
        task.cleanup_status = "pending_retry"
    elif task.cleanup_status != "pending_retry":
        task.cleanup_status = "completed"
    return failed


def _cleanup_provider_files_for_task(
    client: BailianClient,
    task_id: str,
    file_ids: list[str],
) -> None:
    """Delete provider files only after the local result is durable."""

    if not file_ids:
        return
    with SessionLocal() as session:
        task = session.scalar(
            select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
        )
        if task is None:
            return
        failed_file_ids = _delete_provider_files(client, task, file_ids)
        clear_successful_bailian_file_tasks(
            session,
            [file_id for file_id in file_ids if file_id not in set(failed_file_ids)],
        )
        session.commit()


def _cleanup_cancelled_provider_batches(
    client: BailianClient,
    task_id: str,
    provider_batch_ids: list[str],
    provider_input_file_ids: list[str],
) -> bool:
    """Cancel recovered provider Batches and delete their exact files."""

    with SessionLocal() as session:
        task = session.scalar(
            select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
        )
        if task is None:
            return False
        cleanup_file_ids = {str(file_id) for file_id in provider_input_file_ids if file_id}
        pending_batch_ids: set[str] = set()
        terminal_output_ids: set[str] = set()
        terminal_error_ids: set[str] = set()
        cancel_errors: list[str] = []
        for provider_batch_id in provider_batch_ids:
            try:
                cancelled = client.cancel_batch(provider_batch_id)
                if not isinstance(cancelled, dict):
                    raise ValueError("供应商取消响应格式无效")
                provider_status = str(cancelled.get("status") or "").strip().lower()
                returned_output_id = str(cancelled.get("output_file_id") or "")
                returned_error_id = str(cancelled.get("error_file_id") or "")
                if returned_output_id:
                    cleanup_file_ids.add(returned_output_id)
                    terminal_output_ids.add(returned_output_id)
                if returned_error_id:
                    cleanup_file_ids.add(returned_error_id)
                    terminal_error_ids.add(returned_error_id)
                if provider_status not in PROVIDER_TERMINAL:
                    pending_batch_ids.add(provider_batch_id)
                    state = provider_status or "未确认"
                    cancel_errors.append(f"{provider_batch_id}:供应商取消仍处于 {state}")
            except Exception as exc:
                pending_batch_ids.add(provider_batch_id)
                cancel_errors.append(
                    f"{provider_batch_id}:" + safe_exception_message(exc, limit=1000)
                )
        if pending_batch_ids:
            task.cleanup_status = "pending_retry"
            task.last_error = ("取消孤儿 Batch 未终态：" + "；".join(cancel_errors))[:2000]
            task.provider_batch_ids = sorted(pending_batch_ids)
            task.provider_batch_id = sorted(pending_batch_ids)[0]
            task.provider_input_file_ids = sorted(cleanup_file_ids)
            task.provider_output_file_ids = sorted(terminal_output_ids)
            task.provider_error_file_ids = sorted(terminal_error_ids)
            ensure_bailian_cleanup_tasks(
                session,
                task,
                provider_batch_ids=pending_batch_ids,
                provider_file_ids=cleanup_file_ids,
                include_file_tasks=False,
                last_error=task.last_error,
            )
            session.commit()
            return True
        failed_file_ids = _delete_provider_files(client, task, sorted(cleanup_file_ids))
        clear_successful_bailian_file_tasks(
            session,
            [file_id for file_id in cleanup_file_ids if file_id not in set(failed_file_ids)],
        )
        if failed_file_ids:
            task.cleanup_status = "pending_retry"
        task.provider_batch_ids = []
        task.provider_batch_id = None
        task.provider_input_file_ids = []
        task.provider_output_file_ids = []
        task.provider_error_file_ids = []
        session.commit()
        return False


def _cleanup_cancelled_batch_input(
    client: BailianClient,
    task_id: str,
    file_id: str,
) -> None:
    """Delete an input uploaded after cancellation, with exact retry fallback."""

    try:
        client.delete_file(file_id)
        return
    except Exception as exc:
        with SessionLocal() as session:
            existing = session.scalar(
                select(ExternalDeletionTask)
                .where(
                    ExternalDeletionTask.kind == "bailian_file",
                    ExternalDeletionTask.external_id == file_id,
                )
                .limit(1)
            )
            if existing is None:
                session.add(
                    ExternalDeletionTask(
                        kind="bailian_file",
                        external_id=file_id,
                        payload={"model_task_id": task_id},
                        attempts=0,
                        last_error=safe_exception_message(exc, limit=1000),
                    )
                )
            session.commit()


@celery_app.task(
    name="ai_management.poll_batch_model_task",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def poll_batch_model_task(self, task_id: str) -> bool:
    lock_context = cache.lock(f"ai:model-cleaning-poll:{task_id}", ttl=600)
    lock_state = lock_context.__enter__()
    if not lock_state:
        if getattr(lock_state, "available", True) is False:
            reschedule = False
            try:
                with SessionLocal() as session:
                    task = session.get(ModelCleaningTask, task_id)
                    if (
                        task is not None
                        and task.status in {"batch_running", "validating"}
                        and get_provider_batch_ids(task, include_terminal=True)
                    ):
                        task.last_error = "Batch轮询锁后端不可用，已保留活动状态并重试"
                        session.commit()
                        reschedule = True
            except Exception:
                logger.exception("failed to persist unavailable batch poll lock")
            if reschedule:
                poll_batch_model_task.apply_async(
                    args=[task_id], queue="ai_model_batch", countdown=60
                )
        lock_context.__exit__(None, None, None)
        return False
    client: BailianClient | None = None
    fallback_pending_ids: list[str] = []
    fallback_shard_ids: list[str] = []
    created_repair_batch_ids: list[str] = []
    created_repair_input_ids: list[str] = []
    try:
        client = _client()
        with SessionLocal() as session:
            task = session.scalar(
                select(ModelCleaningTask).where(ModelCleaningTask.id == task_id).with_for_update()
            )
            if task is None or task.status not in {"batch_running", "validating"}:
                return False
            provider_batch_ids = get_provider_batch_ids(task)
            if not provider_batch_ids:
                # A prior poll may have durably moved every provider Batch to
                # terminal while the local result transition was interrupted.
                # Only use the persisted terminal map as a recovery source;
                # normal active polling should not re-query completed Batches.
                provider_batch_ids = get_provider_batch_ids(task, include_terminal=True)
            if not provider_batch_ids:
                task.status = "failed"
                task.last_error = (
                    "Batch轮询失败：batch_running 但没有 provider_batch_ids，无法恢复供应商任务"
                )
                task.result_counts = merge_result_counts_preserving_provider_ledger(
                    task,
                    {
                        **(task.result_counts or {}),
                        "failure_code": "MODEL_BATCH_PROVIDER_IDS_MISSING",
                    },
                )
                if batch := session.get(CleaningBatch, task.batch_id):
                    batch.status = "processing"
                session.commit()
                return False
            if task.provider_batch_ids != provider_batch_ids:
                task.provider_batch_ids = provider_batch_ids
                task.provider_batch_id = provider_batch_ids[0]
            batch = session.get(CleaningBatch, task.batch_id)
            batch_file_map = dict((task.result_counts or {}).get("provider_batch_file_map") or {})
            previous_terminal = (task.result_counts or {}).get("provider_terminal_batches", {})
            if not isinstance(previous_terminal, dict):
                previous_terminal = {}
            active_batch_ids: list[str] = []
            fetched_statuses: list[dict[str, Any]] = []
            for provider_batch_id in provider_batch_ids:
                status = client.get_batch(provider_batch_id)
                status = {**status, "provider_batch_id": provider_batch_id}
                owned_files = dict(batch_file_map.get(provider_batch_id) or {})
                for status_field, map_field in (
                    ("input_file_id", "input_file_id"),
                    ("output_file_id", "output_file_id"),
                    ("error_file_id", "error_file_id"),
                ):
                    if status.get(status_field):
                        owned_files[map_field] = str(status[status_field])
                batch_file_map[provider_batch_id] = owned_files
                if str(status.get("status") or "") in PROVIDER_TERMINAL:
                    previous_terminal[provider_batch_id] = status
                else:
                    active_batch_ids.append(provider_batch_id)
                    fetched_statuses.append(status)
            terminal_output_ids = {
                str(file_id) for file_id in (task.provider_output_file_ids or []) if file_id
            }
            terminal_error_ids = {
                str(file_id) for file_id in (task.provider_error_file_ids or []) if file_id
            }
            for terminal_status in previous_terminal.values():
                if terminal_status.get("output_file_id"):
                    terminal_output_ids.add(str(terminal_status["output_file_id"]))
                if terminal_status.get("error_file_id"):
                    terminal_error_ids.add(str(terminal_status["error_file_id"]))
            task.provider_output_file_ids = sorted(terminal_output_ids)
            task.provider_error_file_ids = sorted(terminal_error_ids)
            statuses = [*previous_terminal.values(), *fetched_statuses]
            task.result_counts = {**(task.result_counts or {})}
            task.result_counts["provider_batch_file_map"] = batch_file_map
            if previous_terminal:
                # Keep terminal provider IDs until the local result transition
                # is durable.  If the worker crashes in validating/finalize,
                # the watchdog can replay the exact terminal result instead of
                # treating the task as an approval-only orphan.
                task.result_counts["provider_terminal_batches"] = previous_terminal
            if active_batch_ids:
                task.provider_batch_ids = active_batch_ids
                task.provider_batch_id = active_batch_ids[0]
            else:
                task.provider_batch_ids = []
                task.provider_batch_id = None
            state_values = {str(item.get("status") or "") for item in statuses}
            if active_batch_ids:
                # A provider Batch can legitimately remain in progress for its
                # whole completion window.  Persist only aggregate progress so
                # the admin page distinguishes that state from a stalled local
                # worker without retaining provider identifiers or payloads.
                request_totals = {
                    key: sum(
                        int((item.get("request_counts") or {}).get(key) or 0) for item in statuses
                    )
                    for key in ("total", "completed", "failed")
                }
                task.result_counts = {
                    **(task.result_counts or {}),
                    "provider_statuses": sorted(state_values),
                    "provider_request_counts": request_totals,
                }
                session.commit()
                poll_batch_model_task.apply_async(
                    args=[task_id], queue="ai_model_batch", countdown=60
                )
                return True
            output_ids = sorted(terminal_output_ids)
            error_ids = sorted(terminal_error_ids)
            task.provider_output_file_ids = output_ids
            task.provider_error_file_ids = error_ids
            if state_values != {"completed"}:
                cleanup_file_ids = [
                    *(task.provider_input_file_ids or []),
                    *output_ids,
                    *error_ids,
                ]
                task.result_counts = {
                    key: value
                    for key, value in (task.result_counts or {}).items()
                    if key != "provider_terminal_batches"
                }
                task.result_counts["provider_retry_reason"] = sorted(state_values)
                auto_catalog = (
                    batch is not None
                    and batch.strategy_code == "course_route_catalog"
                    and settings.course_catalog_auto_publish
                )
                task.status = "failed" if auto_catalog else "awaiting_approval"
                if not auto_catalog:
                    task.approval_fingerprint = ""
                    task.approved_at = None
                task.provider_batch_id = None
                task.provider_batch_ids = []
                task.provider_input_file_ids = []
                task.provider_output_file_ids = []
                task.provider_error_file_ids = []
                task.last_error = (
                    "百炼Batch未成功完成，精确清理后自动流程已阻断："
                    if auto_catalog
                    else "百炼Batch未成功完成，精确清理后等待重新批准："
                ) + f"{sorted(state_values)}"
                if auto_catalog:
                    _fail_catalog_auto_run(
                        session,
                        batch,
                        f"供应商 Batch 状态：{sorted(state_values)}",
                    )
                else:
                    for document in _iter_documents_for_batch(
                        session,
                        task.batch_id,
                    ):
                        document.status = "awaiting_model"
                        document.stage = "model_retry"
                        document.error_code = "MODEL_PROVIDER_RETRY_APPROVAL_REQUIRED"
                        document.error_message = "Batch未完成，需重新确认费用后重试"
                    if batch:
                        batch.status = "awaiting_model_approval"
                if batch is not None:
                    fallback_applied, fallback_pending_ids, fallback_shard_ids = (
                        _fallback_failed_model_documents(
                            session,
                            batch.id,
                            reason_code="MODEL_PROVIDER_FAILED",
                            reason_message="百炼 Batch 未成功完成，已使用清洗源 Markdown 继续入库",
                        )
                    )
                    if fallback_applied:
                        task.status = "failed"
                        task.approval_fingerprint = ""
                        task.approved_at = None
                        batch.status = "processing"
                session.commit()
                _cleanup_provider_files_for_task(client, task_id, cleanup_file_ids)
                _queue_publication_outputs(fallback_pending_ids, fallback_shard_ids)
                return True
            task.status = "validating"
            if batch:
                batch.status = "model_validating"
            session.commit()

            def abort_if_model_task_cancelled(*, expected_status: str | None = None) -> bool:
                locked_task = session.scalar(
                    select(ModelCleaningTask)
                    .where(ModelCleaningTask.id == task_id)
                    .with_for_update()
                )
                if (
                    locked_task is None
                    or locked_task.status == "cancelled"
                    or (expected_status is not None and locked_task.status != expected_status)
                ):
                    session.rollback()
                    return True
                return False

            results: dict[str, dict[str, Any]] = {}
            try:
                for output_id in output_ids:
                    try:
                        content = client.get_file_content(output_id)
                    except Exception as exc:
                        if abort_if_model_task_cancelled():
                            return False
                        previous_counts = dict(task.result_counts or {})
                        fetch_attempts = (
                            int(previous_counts.get("provider_result_fetch_attempts") or 0) + 1
                        )
                        task.result_counts = {
                            **previous_counts,
                            "provider_result_fetch_attempts": fetch_attempts,
                            "provider_result_fetch_error": safe_exception_message(exc, limit=1800),
                        }
                        task.last_error = (
                            "百炼 Batch 输出文件暂时不可读取，保留供应商结果并重试："
                            + safe_exception_message(exc, limit=1800)
                        )[:2000]
                        task.status = "batch_running"
                        if batch is not None:
                            batch.status = "model_batch_running"
                        session.commit()
                        poll_batch_model_task.apply_async(
                            args=[task_id], queue="ai_model_batch", countdown=60
                        )
                        return True
                    parsed = _parse_provider_output(content)
                    overlap = set(results) & set(parsed)
                    if overlap:
                        raise ValueError(f"Batch跨文件custom_id重复：{sorted(overlap)[:3]}")
                    results.update(parsed)
                expected_ids = {
                    build_custom_id(_request(document, batch.strategy_code), task.prompt_version)
                    for document in _iter_documents_for_batch(session, task.batch_id)
                }
                unknown_ids = set(results) - expected_ids
                if unknown_ids:
                    raise ValueError(
                        f"Batch结果包含不属于当前候选集的custom_id：{sorted(unknown_ids)[:3]}"
                    )
            except Exception as exc:
                if abort_if_model_task_cancelled():
                    return False
                cleanup_file_ids = [
                    *(task.provider_input_file_ids or []),
                    *output_ids,
                    *error_ids,
                ]
                (
                    fallback_pending_ids,
                    fallback_shard_ids,
                ) = _handle_terminal_batch_result_error(
                    session,
                    task,
                    batch,
                    exc,
                )
                session.commit()
                _cleanup_provider_files_for_task(client, task_id, cleanup_file_ids)
                _queue_publication_outputs(fallback_pending_ids, fallback_shard_ids)
                return True
            counts = {
                "succeeded": 0,
                "excluded": 0,
                "review": 0,
                "fallback": 0,
                "failed": 0,
            }
            usage = dict(task.actual_usage or {})
            usage.setdefault("input_tokens", 0)
            usage.setdefault("output_tokens", 0)
            retry_count = 0
            small_retry_documents: list[CleaningDocument] = []
            retry_hints: dict[str, str] = {}
            for document in _iter_documents_for_batch(session, task.batch_id):
                source_markdown = document.cleaned_markdown
                source_metadata = dict(document.metadata_json or {})
                source_chunking = dict(document.chunking_json or {})
                request = _request(document, batch.strategy_code)
                custom_id = build_custom_id(request, task.prompt_version)
                if document.status not in {"model_processing", "awaiting_model"}:
                    results.pop(custom_id, None)
                    continue
                item = results.pop(custom_id, None)
                if item is None:
                    _mark_contract_failure(
                        document,
                        ValueError("Batch结果缺少当前条目"),
                        final=False,
                    )
                    document.error_code = "MODEL_BATCH_RESULT_MISSING"
                    retry_hints[document.id] = "请输出完整 JSON，并逐一归类输入 blocks 的全部 key。"
                    retry_count += 1
                    if retry_count < 100:
                        small_retry_documents.append(document)
                    elif retry_count == 100:
                        small_retry_documents.clear()
                    continue
                item_usage = item.get("usage") or {}
                usage["input_tokens"] += int(item_usage.get("prompt_tokens") or 0)
                usage["output_tokens"] += int(item_usage.get("completion_tokens") or 0)
                try:
                    _apply_model_payload(
                        document,
                        batch.strategy_code,
                        request,
                        item["payload"],
                        session=session,
                        model_name=task.model,
                        prompt_version=task.prompt_version,
                    )
                    _finalize_model_document(document, strategy_code=batch.strategy_code)
                    fallback_applied = _apply_source_markdown_fallback(
                        document,
                        batch.strategy_code,
                        source_markdown=source_markdown,
                        source_metadata=source_metadata,
                        source_chunking=source_chunking,
                    )
                    if fallback_applied:
                        counts["fallback"] += 1
                    else:
                        counts[
                            "excluded"
                            if document.status == "excluded"
                            else ("review" if document.status == "review_required" else "succeeded")
                        ] += 1
                except Exception as exc:
                    _mark_contract_failure(document, exc, final=False)
                    retry_hints[document.id] = _model_contract_retry_hint(exc)
                    retry_count += 1
                    if retry_count < 100:
                        small_retry_documents.append(document)
                    elif retry_count == 100:
                        small_retry_documents.clear()
            task.actual_usage = usage
            retry_round = int((task.result_counts or {}).get("retry_round") or 0)
            if retry_count and retry_round < 1:
                old_file_ids = [
                    *(task.provider_input_file_ids or []),
                    *output_ids,
                    *error_ids,
                ]
                if retry_count < 100:
                    for document in small_retry_documents:
                        if document.status not in {"model_processing", "awaiting_model"}:
                            continue
                        source_markdown = document.cleaned_markdown
                        source_metadata = dict(document.metadata_json or {})
                        source_chunking = dict(document.chunking_json or {})
                        request = _request(document, batch.strategy_code)
                        repair_hint = retry_hints.get(document.id, "")
                        _store_model_review_request_context(
                            document,
                            request,
                            model_name=task.model,
                            prompt_version=task.prompt_version,
                            repair_hint=repair_hint,
                        )
                        session.commit()
                        try:
                            if abort_if_model_task_cancelled(expected_status="validating"):
                                return False
                            _wait_for_realtime_capacity(
                                request,
                                model=task.model,
                                repair_hint=repair_hint,
                            )
                            call_id = _reserve_provider_call(
                                session,
                                task,
                                document,
                                request,
                                repair_hint=repair_hint,
                                transport_mode="realtime",
                            )
                            payload, item_usage = _realtime_chat(
                                client,
                                request,
                                model=task.model,
                                repair_hint=repair_hint,
                            )
                            _persist_realtime_usage(
                                session, task, usage, item_usage, call_id=call_id
                            )
                            if abort_if_model_task_cancelled(expected_status="validating"):
                                return False
                            _apply_model_payload(
                                document,
                                batch.strategy_code,
                                request,
                                payload,
                                session=session,
                                model_name=task.model,
                                prompt_version=task.prompt_version,
                                repair_hint=repair_hint,
                            )
                            _finalize_model_document(document, strategy_code=batch.strategy_code)
                            fallback_applied = _apply_source_markdown_fallback(
                                document,
                                batch.strategy_code,
                                source_markdown=source_markdown,
                                source_metadata=source_metadata,
                                source_chunking=source_chunking,
                            )
                            if fallback_applied:
                                counts["fallback"] += 1
                            else:
                                counts[
                                    "excluded"
                                    if document.status == "excluded"
                                    else (
                                        "review"
                                        if document.status == "review_required"
                                        else "succeeded"
                                    )
                                ] += 1
                        except Exception as exc:
                            _mark_contract_failure(
                                document,
                                exc,
                                final=True,
                                auto_fail=(
                                    batch.strategy_code == "course_route_catalog"
                                    and settings.course_catalog_auto_publish
                                ),
                                review_on_final=(
                                    batch.strategy_code == "course_route_catalog"
                                    and not settings.course_catalog_auto_publish
                                ),
                            )
                            fallback_applied = _apply_source_markdown_fallback(
                                document,
                                batch.strategy_code,
                                source_markdown=source_markdown,
                                source_metadata=source_metadata,
                                source_chunking=source_chunking,
                            )
                            if fallback_applied:
                                counts["fallback"] += 1
                            else:
                                counts["failed"] += 1
                    task.actual_usage = usage
                else:
                    with tempfile.TemporaryDirectory(prefix="jx-bailian-retry-") as directory:
                        retry_inputs = _write_batch_files(
                            Path(directory),
                            task,
                            batch,
                            _iter_documents_for_batch(session, task.batch_id),
                            session=session,
                            repair_hints=retry_hints,
                        )
                        session.commit()
                        retry_input_ids: list[str] = []
                        retry_batch_ids: list[str] = []

                        def abort_cancelled_repair_submission() -> bool:
                            session.rollback()
                            if created_repair_batch_ids or created_repair_input_ids:
                                _cleanup_cancelled_provider_batches(
                                    client,
                                    task_id,
                                    created_repair_batch_ids,
                                    created_repair_input_ids,
                                )
                            return False

                        for batch_input in retry_inputs:
                            task = session.scalar(
                                select(ModelCleaningTask)
                                .where(ModelCleaningTask.id == task_id)
                                .with_for_update()
                            )
                            if task is None or task.status not in {
                                "batch_running",
                                "validating",
                            }:
                                return abort_cancelled_repair_submission()
                            input_id = client.upload_batch_path(str(batch_input.path))
                            retry_input_ids.append(input_id)
                            created_repair_input_ids.append(input_id)
                            task = session.scalar(
                                select(ModelCleaningTask)
                                .where(ModelCleaningTask.id == task_id)
                                .with_for_update()
                            )
                            if task is not None:
                                session.refresh(task)
                            if task is None or task.status not in {
                                "batch_running",
                                "validating",
                            }:
                                return abort_cancelled_repair_submission()
                            task.provider_input_file_ids = list(retry_input_ids)
                            session.commit()
                            task = session.scalar(
                                select(ModelCleaningTask)
                                .where(ModelCleaningTask.id == task_id)
                                .with_for_update()
                            )
                            if task is not None:
                                session.refresh(task)
                            if task is None or task.status not in {
                                "batch_running",
                                "validating",
                            }:
                                return abort_cancelled_repair_submission()
                            for document, request, repair_hint in batch_input.requests:
                                _reserve_provider_call(
                                    session,
                                    task,
                                    document,
                                    request,
                                    repair_hint=repair_hint,
                                )
                            retry_batch_id = client.create_batch(input_id)
                            retry_batch_ids.append(retry_batch_id)
                            created_repair_batch_ids.append(retry_batch_id)
                            task = session.scalar(
                                select(ModelCleaningTask)
                                .where(ModelCleaningTask.id == task_id)
                                .with_for_update()
                            )
                            if task is None or task.status not in {
                                "batch_running",
                                "validating",
                            }:
                                return abort_cancelled_repair_submission()
                            task.provider_batch_ids = list(retry_batch_ids)
                            task.provider_batch_id = retry_batch_ids[0]
                            result_counts = {
                                key: value
                                for key, value in (task.result_counts or {}).items()
                                if key != "provider_terminal_batches"
                            }
                            result_counts["provider_batch_generation"] = (
                                int(result_counts.get("provider_batch_generation") or 0) + 1
                            )
                            task.result_counts = result_counts
                            _record_provider_batch_files(
                                task, retry_batch_id, input_file_id=input_id
                            )
                            ensure_bailian_cleanup_tasks(
                                session,
                                task,
                                provider_file_ids=old_file_ids,
                            )
                            task.provider_output_file_ids = []
                            task.provider_error_file_ids = []
                            session.commit()
                        task = session.scalar(
                            select(ModelCleaningTask)
                            .where(ModelCleaningTask.id == task_id)
                            .with_for_update()
                        )
                        if task is None or task.status not in {
                            "batch_running",
                            "validating",
                        }:
                            return abort_cancelled_repair_submission()
                    if not retry_batch_ids:
                        retry_documents = list(
                            session.scalars(
                                select(CleaningDocument)
                                .join(CleaningFile)
                                .where(CleaningFile.batch_id == task.batch_id)
                            )
                        )
                        for document in retry_documents:
                            if document.status not in {
                                "model_processing",
                                "awaiting_model",
                            }:
                                continue
                            _mark_contract_failure(
                                document,
                                ValueError("Batch修复没有可提交的候选请求"),
                                final=True,
                            )
                            fallback_applied = _apply_source_markdown_fallback(
                                document,
                                batch.strategy_code,
                                source_markdown=document.cleaned_markdown,
                                source_metadata=dict(document.metadata_json or {}),
                                source_chunking=dict(document.chunking_json or {}),
                            )
                            if fallback_applied:
                                counts["fallback"] += 1
                            else:
                                counts["failed"] += 1
                        task.status = "failed"
                        task.result_counts = {
                            **{
                                key: value
                                for key, value in (task.result_counts or {}).items()
                                if key != "provider_terminal_batches"
                            },
                            "candidate_count": task.candidate_count,
                            "succeeded": counts["succeeded"],
                            "excluded": counts["excluded"],
                            "review": counts["review"],
                            "fallback": counts["fallback"],
                            "failed": counts["failed"],
                            "retry_pending": 0,
                        }
                        task.provider_input_file_ids = []
                        task.provider_batch_ids = []
                        task.provider_batch_id = None
                        task.last_error = "Batch修复没有可提交的候选请求"
                        from .cleaning import _refresh_file_state

                        touched_files = {str(document.file_id) for document in retry_documents}
                        for file_id in touched_files:
                            _refresh_file_state(session, file_id, commit=False)
                        if batch.strategy_code in POST_STRATEGIES:
                            fallback_shard_ids = _plan_post_publication(session, task.batch_id)
                        else:
                            fallback_pending_ids = [
                                str(document.id)
                                for document in retry_documents
                                if document.status == "pending"
                            ]
                        batch.status = "processing"
                        session.commit()
                        _cleanup_provider_files_for_task(client, task_id, old_file_ids)
                        _queue_publication_outputs(fallback_pending_ids, fallback_shard_ids)
                        return True
                    task.provider_input_file_ids = retry_input_ids
                    task.provider_output_file_ids = []
                    task.provider_error_file_ids = []
                    task.provider_batch_ids = retry_batch_ids
                    task.provider_batch_id = retry_batch_ids[0] if retry_batch_ids else None
                    result_counts = {
                        key: value
                        for key, value in (task.result_counts or {}).items()
                        if key != "provider_terminal_batches"
                    }
                    task.result_counts = merge_result_counts_preserving_provider_ledger(
                        task,
                        {
                            **result_counts,
                            "retry_round": 1,
                            "retry_candidate_count": retry_count,
                            "candidate_count": task.candidate_count,
                            "prior_succeeded": counts["succeeded"],
                            "prior_excluded": counts["excluded"],
                            "prior_review": counts["review"],
                            "prior_fallback": counts["fallback"],
                            "prior_failed": counts["failed"],
                            "retry_pending": retry_count,
                        },
                    )
                    task.cleanup_status = "not_started"
                    task.status = "batch_running"
                    session.commit()
                    _cleanup_provider_files_for_task(client, task_id, old_file_ids)
                    poll_batch_model_task.apply_async(
                        args=[task_id],
                        queue="ai_model_batch",
                        countdown=60,
                    )
                    return True
            elif retry_count:
                for document in _iter_documents_for_batch(session, task.batch_id):
                    if document.status not in {"model_processing", "awaiting_model"}:
                        continue
                    _mark_contract_failure(
                        document,
                        ValueError(document.error_message or "Batch补偿重试仍未得到有效结果"),
                        final=True,
                        auto_fail=(
                            batch.strategy_code == "course_route_catalog"
                            and settings.course_catalog_auto_publish
                        ),
                        review_on_final=(
                            batch.strategy_code == "course_route_catalog"
                            and not settings.course_catalog_auto_publish
                        ),
                    )
                    fallback_applied = _apply_source_markdown_fallback(
                        document,
                        batch.strategy_code,
                        source_markdown=document.cleaned_markdown,
                        source_metadata=dict(document.metadata_json or {}),
                        source_chunking=dict(document.chunking_json or {}),
                    )
                    if fallback_applied:
                        counts["fallback"] += 1
                    else:
                        counts["failed"] += 1
            if abort_if_model_task_cancelled():
                return False
            cleanup_file_ids = [
                *(task.provider_input_file_ids or []),
                *output_ids,
                *error_ids,
            ]
            previous = task.result_counts or {}
            task.result_counts = merge_result_counts_preserving_provider_ledger(
                task,
                {
                    "candidate_count": task.candidate_count,
                    "succeeded": counts["succeeded"] + int(previous.get("prior_succeeded") or 0),
                    "excluded": counts["excluded"] + int(previous.get("prior_excluded") or 0),
                    "review": counts["review"] + int(previous.get("prior_review") or 0),
                    "fallback": counts["fallback"] + int(previous.get("prior_fallback") or 0),
                    "failed": counts["failed"] + int(previous.get("prior_failed") or 0),
                    "retry_round": int(previous.get("retry_round") or 0),
                    "retry_pending": 0,
                },
            )
            if previous.get("provider_terminal_batches"):
                task.result_counts["provider_terminal_batches"] = previous[
                    "provider_terminal_batches"
                ]
            ensure_bailian_cleanup_tasks(
                session,
                task,
                provider_file_ids=cleanup_file_ids,
            )
            session.commit()
        _finish_model_task(task_id)
        _cleanup_provider_files_for_task(client, task_id, cleanup_file_ids)
        return True
    except Exception as exc:
        logger.exception("batch model polling failed")
        if created_repair_batch_ids:
            try:
                recovered = _recover_created_provider_batches(
                    task_id,
                    created_repair_batch_ids,
                    created_repair_input_ids,
                    "Batch 修复任务已在供应商创建，但本地提交失败；已恢复远端 ID 并继续轮询",
                )
            except Exception:
                logger.exception("failed to recover repair provider batch IDs")
                recovered = False
            if recovered == "cancelled":
                _cleanup_cancelled_provider_batches(
                    client,
                    task_id,
                    created_repair_batch_ids,
                    created_repair_input_ids,
                )
                return False
            if recovered:
                poll_batch_model_task.apply_async(
                    args=[task_id], queue="ai_model_batch", countdown=60
                )
                return False
        # A provider/DB connection failure is not evidence that the remote
        # Batch reached a terminal state.  Keep the task observable and poll
        # again; only an explicit provider terminal status may atomically fail
        # an automatic course catalogue run.  Scheduling is deliberately done
        # outside the database context so a dropped connection cannot orphan
        # the remote Batch by preventing the next poll from being queued.
        reschedule = True
        try:
            with SessionLocal() as session:
                task = session.get(ModelCleaningTask, task_id)
                if (
                    task is None
                    or task.status not in {"batch_running", "validating"}
                    or not get_provider_batch_ids(task, include_terminal=True)
                ):
                    reschedule = False
                else:
                    task.last_error = (
                        "Batch轮询暂时失败：" + safe_exception_message(exc, limit=1900)
                    )[:2000]
                    session.commit()
        except Exception:
            logger.exception("failed to persist transient batch polling error")
        if reschedule:
            poll_batch_model_task.apply_async(args=[task_id], queue="ai_model_batch", countdown=60)
        return False
    finally:
        if client:
            client.close()
        lock_context.__exit__(None, None, None)


@celery_app.task(
    name="ai_management.publish_dify_shard",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def publish_dify_shard(self, shard_id: str) -> bool:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from knowledge_cleaner.dify import DifyKnowledgeClient

    now_hour = datetime.now(ZoneInfo("Asia/Shanghai")).hour
    if not (
        settings.dify_publish_window_start_hour <= now_hour < settings.dify_publish_window_end_hour
    ):
        raise self.retry(countdown=900, max_retries=None)
    client: DifyKnowledgeClient | None = None
    created_id = ""
    cleanup_dataset_id = ""
    replaced_document_id = ""
    prior_remote_document_id = ""
    prior_remote_batch_id: str | None = None
    compensation_failed = False
    try:
        with SessionLocal() as session:
            shard = session.scalar(
                select(DifyPublicationShard)
                .where(DifyPublicationShard.id == shard_id)
                .with_for_update()
                .options(
                    selectinload(DifyPublicationShard.file).selectinload(CleaningFile.batch),
                    selectinload(DifyPublicationShard.documents),
                )
            )
            # Only a pending shard may be claimed.  A second queued delivery
            # must not republish a shard already being indexed; the
            # maintenance watchdog resets genuinely stale indexing rows back
            # to pending after its timeout.
            if shard is None or shard.status != "shard_pending":
                return False
            dataset_id = (
                settings.dify_general_dataset_id
                if shard.file.batch.knowledge_base_code == "general"
                else settings.dify_course_dataset_id
            )
            cleanup_dataset_id = dataset_id
            client = DifyKnowledgeClient(
                settings.knowledge_base_url,
                settings.dify_knowledge_api_key,
                dataset_id,
                timeout=180,
            )
            shard_metadata = dict(shard.metadata_json or {})
            pending_remote_id = str(shard_metadata.get("_pending_dify_document_id") or "")
            prior_remote_document_id = str(shard.dify_document_id or "")
            prior_remote_batch_id = shard.dify_batch_id
            ownership = str(shard_metadata.get("_dify_remote_reference_ownership") or "owned")
            # Only a remote ID created by this source can be a replacement
            # deletion target.  Cross-file candidates may be verified and
            # shared, but a failed verification or metadata mismatch must not
            # turn another source's result into this source's cleanup target.
            replaced_document_id = str(shard_metadata.get("_replacement_document_id") or "")
            if not replaced_document_id and ownership == "owned":
                replaced_document_id = prior_remote_document_id
            from ...cleaning import finalize_knowledge_markdown

            shard.markdown = finalize_knowledge_markdown(shard.markdown)
            shard.content_bytes = len(shard.markdown.encode("utf-8"))
            shard.content_sha256 = _normalized_publication_digest(shard.markdown)
            shard_metadata["publication_content_sha256"] = shard.content_sha256
            shard.metadata_json = dict(shard_metadata)
            if prior_remote_document_id and _shard_remote_is_live(
                session,
                client=client,
                shard=shard,
            ):
                shard.status = "succeeded"
                shard.last_error = ""
                shard.remote_deleted_at = None
                if ownership == "shared_candidate":
                    shard_metadata["_dify_remote_reference_ownership"] = "shared"
                    shard.metadata_json = dict(shard_metadata)
                for document in shard.documents:
                    document.dify_document_id = prior_remote_document_id
                    document.dify_batch_id = shard.dify_batch_id
                    document.remote_parent_count = shard.remote_parent_count
                    document.remote_child_count = shard.remote_child_count
                    document.status = "succeeded"
                    document.stage = "completed"
                session.commit()
                from .cleaning import _finish_batch, _refresh_file_state

                _refresh_file_state(session, shard.file_id, commit=False)
                _finish_batch(session, shard.file.batch_id)
                session.commit()
                return True
            shard.status = "shard_indexing"
            shard.attempts += 1
            for document in shard.documents:
                document.status = "shard_indexing"
                document.stage = "shard_indexing"
            session.commit()
            if pending_remote_id:
                try:
                    client.delete_document(pending_remote_id)
                    shard_metadata.pop("_pending_dify_document_id", None)
                except Exception as exc:
                    session.add(
                        ExternalDeletionTask(
                            kind="dify_document",
                            external_id=pending_remote_id,
                            payload={"dataset_id": dataset_id},
                            attempts=0,
                            last_error=safe_exception_message(exc, limit=1000),
                        )
                    )
                    shard_metadata.pop("_pending_dify_document_id", None)
                shard.metadata_json = dict(shard_metadata)
                session.commit()
            from ...cleaning import dify_document_name

            # The source filename is the stable external identity.  Model
            # titles remain content metadata only and must never replace it.
            # Normalize and recalculate the digest before deriving the
            # external name so historical shards cannot retain a legacy
            # pre-normalization digest in Dify.
            source_title = shard.file.original_filename
            remote_name = dify_document_name(
                source_title,
                shard.content_sha256,
                shard.shard_index,
                split=True,
            )
            chunking = dict((shard.metadata_json or {}).get("chunking") or {})
            document_id, dify_batch_id = client.create_document(
                remote_name,
                shard.markdown,
                doc_form=str(chunking.get("doc_form") or "hierarchical_model"),
                process_mode=str(chunking.get("process_mode") or "hierarchical"),
                parent_mode=str(chunking.get("parent_mode") or "paragraph"),
                parent_separator=chunking.get("parent_separator", "<<<DIFY_PARENT>>>"),
                parent_max_tokens=int(
                    chunking.get("parent_max_tokens") or settings.dify_parent_max_tokens
                ),
                child_separator=chunking.get("child_separator", "<<<DIFY_CHILD>>>"),
                child_max_tokens=int(chunking.get("child_max_tokens") or 800),
                parent_chunk_overlap=int(chunking.get("parent_chunk_overlap") or 0),
                child_chunk_overlap=int(chunking.get("child_chunk_overlap") or 0),
            )
            created_id = document_id
            # Keep the previous published ID authoritative until the new
            # document is fully verified.  The pending ID and replacement
            # target are durable local markers, so a worker crash cannot lose
            # either remote cleanup target.
            shard_metadata["_pending_dify_document_id"] = document_id
            if replaced_document_id:
                shard_metadata["_replacement_document_id"] = replaced_document_id
            shard.metadata_json = dict(shard_metadata)
            session.commit()
            client.wait_for_indexing(
                dify_batch_id,
                document_id=document_id,
                timeout_seconds=1800,
                interval=2,
            )
            structure = client.verify_document_structure(
                document_id,
                parent_mode="paragraph",
            )
            _validate_shard_structure(structure, item_count=shard.item_count)
            from ...cleaning import STRATEGY_REGISTRY
            from .cleaning import _project_dify_metadata

            strategy = STRATEGY_REGISTRY.get(shard.file.batch.strategy_code)
            remote_metadata = _project_dify_metadata(
                shard_metadata,
                strategy=strategy,
            )
            field_types = getattr(strategy, "dify_metadata_types", None)
            fields = client.ensure_metadata_names(
                sorted(remote_metadata),
                field_types=field_types,
            )
            client.set_document_metadata(
                document_id,
                remote_metadata,
                fields,
                field_types=field_types,
            )
            client.verify_document_metadata(
                document_id,
                remote_metadata,
            )
            shard_metadata["remote_content_witness_sha256"] = str(
                client.document_content_witness(document_id)
            ).strip()
            if not re.fullmatch(r"[0-9a-f]{64}", shard_metadata["remote_content_witness_sha256"]):
                raise RuntimeError("Dify分片发布后的正文见证无效")
            shard.dify_document_id = document_id
            shard.dify_batch_id = dify_batch_id
            shard_metadata["_dify_remote_reference_ownership"] = "owned"
            shard_metadata.pop("_pending_dify_document_id", None)
            shard_metadata.pop("_replacement_document_id", None)
            shard.metadata_json = dict(shard_metadata)
            shard.remote_parent_count = int(structure["parent_count"])
            shard.remote_child_count = int(structure["child_count"])
            shard.status = "succeeded"
            shard.last_error = ""
            for document in shard.documents:
                # A model task publishes one remote document per shard.  Keep
                # the local item projection in sync with the actual Dify
                # management name instead of retaining the pre-sharding item
                # name generated during the cleaning phase.
                document.dify_document_name = remote_name
                document.dify_document_id = document_id
                document.dify_batch_id = dify_batch_id
                document.remote_parent_count = int(structure["parent_count"])
                document.remote_child_count = int(structure["child_count"])
                document.status = "succeeded"
                document.stage = "completed"
            should_delete_replaced = bool(
                replaced_document_id
                and replaced_document_id != document_id
                and not has_other_live_dify_reference(session, replaced_document_id)
            )
            replacement_cleanup: ExternalDeletionTask | None = None
            if should_delete_replaced:
                replacement_cleanup = session.scalar(
                    select(ExternalDeletionTask)
                    .where(
                        ExternalDeletionTask.kind == "dify_document",
                        ExternalDeletionTask.external_id == replaced_document_id,
                    )
                    .limit(1)
                )
                if replacement_cleanup is None:
                    # Durable cleanup is committed with the successful
                    # replacement before the best-effort immediate delete.
                    replacement_cleanup = ExternalDeletionTask(
                            kind="dify_document",
                            external_id=replaced_document_id,
                            payload={"dataset_id": dataset_id},
                            attempts=0,
                            last_error="替换旧 Dify 文档，等待确认删除",
                    )
                    session.add(replacement_cleanup)
            session.commit()
            if should_delete_replaced:
                try:
                    client.delete_document(replaced_document_id)
                    if replacement_cleanup is not None:
                        session.delete(replacement_cleanup)
                        session.commit()
                except Exception:
                    try:
                        client.disable_document(replaced_document_id)
                        if replacement_cleanup is not None:
                            session.delete(replacement_cleanup)
                    except Exception as disable_exc:
                        if replacement_cleanup is not None:
                            replacement_cleanup.last_error = safe_exception_message(
                                disable_exc, limit=1000
                            )
                        session.commit()
            from .cleaning import _finish_batch, _refresh_file_state

            _refresh_file_state(session, shard.file_id, commit=False)
            _finish_batch(session, shard.file.batch_id)
            session.commit()
        return True
    except Exception as exc:
        logger.exception("Dify publication shard failed")
        from .cleaning import RemoteDuplicateVerificationError

        if client and created_id:
            try:
                client.delete_document(created_id)
            except Exception:
                compensation_failed = True
                logger.exception("failed to compensate Dify shard")
        with SessionLocal() as session:
            shard = session.get(DifyPublicationShard, shard_id)
            if shard:
                # If a new remote document was created and then failed during
                # indexing/metadata, it was compensated above.  Restore the
                # prior remote ID (if any) so the retry can replace it rather
                # than trying to delete the already-compensated new ID.
                shard.dify_document_id = prior_remote_document_id or None
                shard.dify_batch_id = prior_remote_batch_id if prior_remote_document_id else None
                if not compensation_failed:
                    metadata = dict(shard.metadata_json or {})
                    metadata.pop("_pending_dify_document_id", None)
                    shard.metadata_json = metadata
                if compensation_failed and created_id:
                    # Keep a durable exact cleanup target when compensation
                    # itself failed; the external-deletion worker can retry
                    # it after this task has been marked pending/failed.
                    session.add(
                        ExternalDeletionTask(
                            kind="dify_document",
                            external_id=created_id,
                            payload={"dataset_id": cleanup_dataset_id},
                            attempts=0,
                            last_error="发布异常后的 Dify 文档补偿删除失败，等待重试",
                        )
                    )
                # A failed read of an already published remote result is not a
                # failed document-creation attempt.  Keep its exact remote ID
                # and a runnable verification state even when historical
                # delivery attempts have reached their creation retry limit.
                verification_uncertain = (
                    isinstance(exc, RemoteDuplicateVerificationError) and not created_id
                )
                retryable = verification_uncertain or shard.attempts < 2
                shard.status = "shard_pending" if retryable else "failed"
                shard.last_error = safe_exception_message(exc, limit=2000)
                for document in shard.documents:
                    document.status = "shard_pending" if retryable else "failed"
                    document.stage = "shard_pending" if retryable else "failed"
                from .cleaning import _finish_batch, _refresh_file_state

                batch_id = session.scalar(
                    select(CleaningFile.batch_id).where(CleaningFile.id == shard.file_id)
                )
                _refresh_file_state(session, shard.file_id, commit=False)
                if batch_id:
                    _finish_batch(session, batch_id)
                session.commit()
                record_event(
                    level="ERROR",
                    category="task",
                    component="dify_publication_worker",
                    event_code="DIFY_PUBLICATION_SHARD_FAILED",
                    status="failed",
                    message="Dify 分片发布失败",
                    resource_type="dify_publication_shard",
                    resource_id=shard_id,
                    details=exception_details(
                        exc,
                        phase="dify_publication_shard",
                        shard_id=shard_id,
                        file_id=shard.file_id,
                        batch_id=batch_id,
                        attempts=shard.attempts,
                    ),
                )
                if shard.attempts < 2:
                    raise self.retry(exc=exc, countdown=60, max_retries=1)
        return False
    finally:
        if client:
            client.close()


__all__ = [
    "maybe_plan_model_task",
    "poll_batch_model_task",
    "process_realtime_model_task",
    "publish_dify_shard",
    "submit_batch_model_task",
]
