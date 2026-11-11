from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from knowledge_cleaner.dify import DifyKnowledgeClient

from ...activity_cleaning import EVENT_METADATA_FIELDS, ActivityCleaningError
from ...cache import cache
from ...cleaning import (
    CONTENT_MODEL_STRATEGIES,
    DISABLED_COURSE_STRATEGIES,
    STRATEGY_REGISTRY,
    ChunkingConfig,
    cleaning_batch_cache_key,
    content_review_reason,
    dify_activity_document_name,
    dify_document_name,
    finalize_knowledge_markdown,
    get_settings,
    normalized_publication_content_sha256,
    preserve_review_reason,
    review_policy_snapshot,
    source_markdown_fallback_enabled,
    user_visible_warnings,
    validate_chunking_boundaries,
)
from ...course_import import sync_cleaning_catalog_record
from ...courses import rebuild_snapshot
from ...db import SessionLocal
from ...models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    DifyPublicationShard,
    ExternalDeletionTask,
    utcnow,
)
from ...services.admin_settings import bump_catalog_revision
from ...services.observability.events import (
    exception_details,
    record_event,
    safe_exception_message,
)
from ..celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


class DuplicateActivityEventError(ValueError):
    """Raised when one source contains more than one revision of an event."""


class RemoteDuplicateVerificationError(RuntimeError):
    """A historical Dify document could not be safely verified for reuse."""


def _duplicate_activity_event_ids(metadata_rows: list[dict[str, Any]]) -> list[str]:
    event_ids = [str((metadata or {}).get("event_id") or "") for metadata in metadata_rows]
    return sorted(
        {event_id for event_id in event_ids if event_id and event_ids.count(event_id) > 1}
    )


def _mark_duplicate_activity_units(documents: list[Any]) -> list[Any]:
    """Fail only colliding activity units and leave independent units runnable."""

    duplicate_event_ids = set(
        _duplicate_activity_event_ids(
            [
                (
                    document.metadata_json
                    if hasattr(document, "metadata_json")
                    else document.metadata
                )
                or {}
                for document in documents
            ]
        )
    )
    if not duplicate_event_ids:
        return documents
    message = "同一源文件包含重复活动 event_id，未发布冲突活动：" + ",".join(
        sorted(duplicate_event_ids)
    )
    remaining: list[Any] = []
    for document in documents:
        metadata = (
            document.metadata_json if hasattr(document, "metadata_json") else document.metadata
        ) or {}
        if str(metadata.get("event_id") or "") not in duplicate_event_ids:
            remaining.append(document)
            continue
        if hasattr(document, "disposition"):
            document.disposition = "failed"
        else:
            document.status = "failed"
            document.stage = "failed"
            document.retryable = False
        document.error_code = "DUPLICATE_ACTIVITY_EVENT_ID"
        document.error_message = message
    return remaining


def _remote_metadata_value(remote: dict[str, Any], key: str) -> str:
    """Read one Dify metadata value from either supported response shape."""

    raw_metadata = remote.get("doc_metadata") or remote.get("metadata") or {}
    if isinstance(raw_metadata, dict):
        return str(raw_metadata.get(key) or "").strip()
    if isinstance(raw_metadata, list):
        for item in raw_metadata:
            if isinstance(item, dict) and str(item.get("name") or item.get("key") or "") == key:
                return str(item.get("value") or "").strip()
    return ""


def _remote_duplicate_is_live(
    session,
    *,
    client: DifyKnowledgeClient,
    candidate: CleaningDocument,
) -> bool:
    """Return true only for a currently readable and indexed Dify document.

    Local ``succeeded`` is provenance, not proof that a remote replica still
    exists. A confirmed 404 releases only this exact local association. A
    timeout or malformed response remains retryable and must never become a
    permanent duplicate decision or a blind replacement write.
    """

    document_id = str(candidate.dify_document_id or "").strip()
    if not document_id:
        return False
    try:
        remote = client.get_document(document_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            candidate.remote_deleted_at = utcnow()
            audit = dict(candidate.audit_json or {})
            audit["remote_duplicate_verification"] = "missing"
            candidate.audit_json = audit
            session.flush()
            return False
        raise RemoteDuplicateVerificationError("Dify重复成果读取失败") from exc
    except Exception as exc:
        raise RemoteDuplicateVerificationError("Dify重复成果读取未确认") from exc

    if not isinstance(remote, dict):
        raise RemoteDuplicateVerificationError("Dify重复成果响应格式无效")
    remote_id = str(remote.get("id") or remote.get("document_id") or "").strip()
    if remote_id and remote_id != document_id:
        return False
    expected_digest = str(
        (candidate.metadata_json or {}).get("publication_content_sha256") or ""
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        # Legacy rows predate the non-sensitive remote content witness.  Their
        # local body cannot prove what is still indexed under this remote ID.
        audit = dict(candidate.audit_json or {})
        audit["remote_duplicate_verification"] = "content_witness_missing"
        candidate.audit_json = audit
        session.flush()
        return False
    if _remote_metadata_value(remote, "publication_content_sha256") != expected_digest:
        audit = dict(candidate.audit_json or {})
        audit["remote_duplicate_verification"] = "content_mismatch"
        candidate.audit_json = audit
        session.flush()
        return False
    # The metadata digest is an association check, not proof of the content
    # currently exposed by Dify: an operator could replace a document body
    # without changing its metadata.  A successful publish records a digest
    # over Dify's actual indexed segments; reuse requires that witness to be
    # readable and unchanged as well.
    expected_remote_witness = str(
        (candidate.audit_json or {}).get("remote_content_witness_sha256") or ""
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_remote_witness):
        audit = dict(candidate.audit_json or {})
        audit["remote_duplicate_verification"] = "remote_content_witness_missing"
        candidate.audit_json = audit
        session.flush()
        return False
    try:
        actual_remote_witness = str(client.document_content_witness(document_id)).strip()
    except AttributeError as exc:
        raise RemoteDuplicateVerificationError("Dify重复成果正文见证未确认") from exc
    except Exception as exc:
        raise RemoteDuplicateVerificationError("Dify重复成果正文见证未确认") from exc
    if actual_remote_witness != expected_remote_witness:
        audit = dict(candidate.audit_json or {})
        audit["remote_duplicate_verification"] = "remote_content_mismatch"
        candidate.audit_json = audit
        session.flush()
        return False
    # Dify persists the upload name alongside the indexed content.  When the
    # API returns it, it is a bounded content-association check: generated
    # names include the normalized publication digest, so a replacement under
    # the same remote id cannot be reused merely because its status is live.
    remote_name = str(
        remote.get("name") or remote.get("document_name") or remote.get("display_name") or ""
    ).strip()
    if remote_name and remote_name != str(candidate.dify_document_name or "").strip():
        audit = dict(candidate.audit_json or {})
        audit["remote_duplicate_verification"] = "content_mismatch"
        candidate.audit_json = audit
        session.flush()
        return False
    indexing_status = str(remote.get("indexing_status") or "").strip().lower()
    if indexing_status != "completed":
        return False
    if remote.get("enabled") is False or remote.get("is_enabled") is False:
        return False
    try:
        client.verify_document_metadata(
            document_id,
            _project_dify_metadata(
                candidate.metadata_json or {},
                strategy=STRATEGY_REGISTRY.get(candidate.file.batch.strategy_code),
            ),
        )
    except RuntimeError as exc:
        # ``verify_document_metadata`` uses this exact, deterministic failure
        # for a completed read whose metadata differs.  It is not a transport
        # uncertainty, so holding the local duplicate forever would be wrong.
        if str(exc).startswith("Dify metadata 回读不一致："):
            audit = dict(candidate.audit_json or {})
            audit["remote_duplicate_verification"] = "metadata_mismatch"
            candidate.audit_json = audit
            session.flush()
            return False
        raise RemoteDuplicateVerificationError("Dify重复成果元数据未确认") from exc
    except Exception as exc:
        raise RemoteDuplicateVerificationError("Dify重复成果元数据未确认") from exc
    audit = dict(candidate.audit_json or {})
    audit["remote_duplicate_verification"] = "live"
    candidate.audit_json = audit
    session.flush()
    return True


# Dify is a retrieval replica, not the protected provenance store.  These
# fields describe the cleaning semantics and are safe for Dify-side filtering
# and audits. The publication digest is a non-reversible witness of the final
# indexed body; file identities, upload IDs, build IDs and UI cleanup markers
# remain in PostgreSQL only.
REMOTE_PROVENANCE_FIELDS = frozenset(
    {
        "cleaning_strategy",
        "cleaning_spec_revision",
        "content_format",
        "publication_content_sha256",
    }
)
REMOTE_FORBIDDEN_METADATA_FIELDS = frozenset(
    {
        "source_file_id",
        "source_filename",
        "source_extension",
        "source_sha256",
        "upload_batch_id",
        "source_document_index",
        "build_version",
        "_test_ui_cleanup_run_id",
    }
)
RAW_UPLOAD_DIFY_METADATA_FIELDS = frozenset({"document_type", "review_status"})


def _record_pdf_conversion_events(
    session,
    *,
    file_row: CleaningFile,
    batch: CleaningBatch,
    documents: list[Any],
) -> None:
    """Persist redacted warning/blocker summaries in the common event log."""

    for document in documents:
        audit = (getattr(document, "audit", None) or {}).get("pdf_conversion") or {}
        if not audit:
            continue
        reasons = [str(item)[:120] for item in (audit.get("review_reasons") or [])]
        status = str(audit.get("status") or "")
        disposition = str(getattr(document, "disposition", "") or "")
        blocked = status in {"failed", "blocked"} or disposition == "failed"
        # Core converters may expose an explicit blocking list; otherwise a
        # missing body/failed OCR status is the only safe blocker inference.
        blocked = blocked or bool(audit.get("blocking_reasons"))
        if not reasons and not blocked and not audit.get("needs_review"):
            continue
        severity = "blocked" if blocked else "warning"
        record_event(
            level="ERROR" if blocked else "WARNING",
            category="pdf_conversion",
            component="cleaning_worker",
            event_code=f"PDF_CONVERSION_{severity.upper()}",
            status="open",
            message="PDF 转换需要处理" if blocked else "PDF 转换存在可复核问题",
            resource_type="cleaning_file",
            resource_id=file_row.id,
            details={
                "knowledge_base_code": batch.knowledge_base_code,
                "severity": severity,
                "codes": reasons[:20],
                "pages": audit.get("page_count") or audit.get("pages"),
                "backend": audit.get("backend"),
                "conversion_status": status,
            },
            session=session,
        )


def _knowledge_client(batch: CleaningBatch) -> DifyKnowledgeClient:
    dataset_id = (
        settings.dify_general_dataset_id
        if batch.knowledge_base_code == "general"
        else settings.dify_course_dataset_id
    )
    if not settings.dify_knowledge_api_key or not dataset_id:
        raise ValueError("Dify知识库 API Key 或 Dataset ID 未配置")
    return DifyKnowledgeClient(
        settings.knowledge_base_url,
        settings.dify_knowledge_api_key,
        dataset_id,
        timeout=180,
    )


def _remove_or_disable_document(client: DifyKnowledgeClient, document_id: str) -> bool:
    try:
        client.delete_document(document_id)
        return True
    except Exception:
        logger.warning("failed to delete Dify document %s; trying disable", document_id)
    try:
        client.disable_document(document_id)
        return True
    except Exception:
        logger.exception("failed to delete or disable Dify document %s", document_id)
        return False


def _queue_document_cleanup(document_id: str, dataset_id: str) -> None:
    with SessionLocal() as session:
        _ensure_document_cleanup_task(session, document_id, dataset_id)
        session.commit()


def _ensure_document_cleanup_task(session, document_id: str, dataset_id: str):
    task = session.scalar(
        select(ExternalDeletionTask)
        .where(
            ExternalDeletionTask.kind == "dify_document",
            ExternalDeletionTask.external_id == document_id,
        )
        .limit(1)
    )
    if task is not None:
        return task
    task = ExternalDeletionTask(
        kind="dify_document",
        external_id=document_id,
        payload={"dataset_id": dataset_id},
        attempts=0,
        last_error="Dify文档删除和停用均失败",
    )
    session.add(task)
    return task


def _chunking_from_json(payload: dict[str, Any] | None) -> ChunkingConfig:
    if not payload:
        return ChunkingConfig()
    return ChunkingConfig(
        doc_form=str(payload.get("doc_form") or "hierarchical_model"),
        process_mode=str(payload.get("process_mode") or "hierarchical"),
        parent_mode=str(payload.get("parent_mode") or "paragraph"),
        parent_separator=payload.get("parent_separator"),
        parent_max_tokens=int(payload.get("parent_max_tokens") or 1000),
        child_separator=payload.get("child_separator"),
        child_max_tokens=int(payload.get("child_max_tokens") or 350),
        parent_chunk_overlap=int(payload.get("parent_chunk_overlap") or 0),
        child_chunk_overlap=int(payload.get("child_chunk_overlap") or 0),
    )


def _publication_markdown_for_document(
    document: CleaningDocument,
    batch: CleaningBatch,
) -> str:
    """Reconstruct the exact final body used for a succeeded publication."""

    publication_markdown = finalize_knowledge_markdown(
        document.cleaned_markdown or "",
    )
    if batch.strategy_code == "personal_business_consultation":
        from knowledge_cleaner.consultation_rag import render_publication_markdown

        chunking = _chunking_from_json(document.chunking_json)
        publication_markdown = render_publication_markdown(
            publication_markdown,
            child_max_chars=max(300, min(800, chunking.child_max_tokens * 2)),
        )
    return publication_markdown


def _content_sha(markdown: str, metadata: dict[str, Any], chunking: dict[str, Any]) -> str:
    provenance_fields = {
        "source_file_id",
        "source_filename",
        "upload_batch_id",
        "source_document_index",
    }
    payload = {
        "markdown": markdown,
        "metadata": {key: value for key, value in metadata.items() if key not in provenance_fields},
        "chunking": chunking,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _activity_content_version_sha(
    markdown: str,
    metadata: dict[str, Any],
    chunking: dict[str, Any],
) -> str:
    stable_metadata = {
        key: value for key, value in metadata.items() if key in EVENT_METADATA_FIELDS
    }
    return _content_sha(markdown, stable_metadata, chunking)


def _project_dify_metadata(
    metadata: dict[str, Any],
    *,
    strategy: Any | None,
) -> dict[str, Any]:
    """Project local metadata into the intentionally narrow Dify boundary."""

    allowed_business_fields = (
        RAW_UPLOAD_DIFY_METADATA_FIELDS
        if strategy is None
        else frozenset(getattr(strategy, "dify_metadata_fields", ()) or ())
    )
    remote_metadata = {
        key: value
        for key, value in metadata.items()
        if key in allowed_business_fields and key not in REMOTE_FORBIDDEN_METADATA_FIELDS
    }
    for field in REMOTE_PROVENANCE_FIELDS:
        # ``recent_activity`` has a separately approved metadata contract.
        # This task must not add a hash field or remote-read dedupe dependency
        # to its established publication behavior.
        if (
            field == "publication_content_sha256"
            and getattr(strategy, "code", "") == "recent_activity"
        ):
            continue
        value = metadata.get(field)
        if value not in (None, "", [], {}):
            remote_metadata[field] = value
    return remote_metadata


def _course_document_publish_order(documents: list[CleaningDocument]) -> list[str]:
    """Order one uploaded course file so same-file prerequisites publish first.

    Dify indexing is intentionally asynchronous, but database catalogue writes
    must observe prerequisite records.  A Celery chain gives that ordering
    without inventing a route or mutating the course content.  Invalid cycles
    and unknown prerequisites retain their original order and are rejected by
    the normal catalogue validation with a user-visible reason.
    """

    entries: dict[str, tuple[CleaningDocument, set[str]]] = {}
    for document in sorted(documents, key=lambda item: item.sequence):
        payload = (document.audit_json or {}).get("catalog_record")
        course_id = payload.get("course_id") if isinstance(payload, dict) else ""
        if not isinstance(course_id, str) or not course_id or course_id in entries:
            continue
        prerequisites = payload.get("prerequisite_course_ids", [])
        entries[course_id] = (
            document,
            {str(item) for item in prerequisites if str(item).strip()},
        )

    unresolved = set(entries)
    ordered_ids: list[str] = []
    while unresolved:
        ready = sorted(
            (course_id for course_id in unresolved if not (entries[course_id][1] & unresolved)),
            key=lambda course_id: entries[course_id][0].sequence,
        )
        if not ready:
            # Leave invalid dependency graphs to the existing strict validator.
            ordered_ids.extend(
                entries[course_id][0].id
                for course_id in sorted(unresolved, key=lambda item: entries[item][0].sequence)
            )
            break
        for course_id in ready:
            unresolved.remove(course_id)
            ordered_ids.append(entries[course_id][0].id)

    known_ids = {document.id for document in documents}
    ordered_set = set(ordered_ids)
    # Documents without a usable catalogue record still go through the normal
    # failure path after the well-formed records, preserving source order.
    ordered_ids.extend(
        document.id
        for document in sorted(documents, key=lambda item: item.sequence)
        if document.id in known_ids and document.id not in ordered_set
    )
    return ordered_ids


def _finish_batch(session, batch_id: str) -> None:
    """Recompute a batch status without taking locks on sibling source files.

    A file worker already owns (and has refreshed) its source file before it
    reaches this function.  Refreshing every sibling here used to make two
    workers lock their own files first and then update the same sibling set in
    opposite orders, which is a PostgreSQL deadlock.  Serialize only the
    batch-summary calculation; the query below is read-only for file rows.
    """
    batch = session.scalar(
        select(CleaningBatch).where(CleaningBatch.id == batch_id).with_for_update()
    )
    if batch is None:
        return
    outcomes = list(
        session.scalars(
            select(CleaningFile.outcome)
            .where(CleaningFile.batch_id == batch_id)
            .order_by(CleaningFile.id)
        )
    )
    terminal = {"succeeded", "excluded", "review_required", "failed", "mixed"}
    batch.status = (
        "completed"
        if outcomes and all(str(outcome) in terminal for outcome in outcomes)
        else "processing"
    )
    cache.delete(cleaning_batch_cache_key(batch_id))


def _refresh_file_state(session, file_id: str, *, commit: bool = True) -> str:
    row = session.get(CleaningFile, file_id)
    if row is None:
        return "failed"
    documents = list(
        session.scalars(
            select(CleaningDocument)
            .where(CleaningDocument.file_id == file_id)
            .order_by(CleaningDocument.sequence)
        )
    )
    if not documents:
        if row.status in {"failed", "review_required", "succeeded", "excluded"}:
            row.outcome = row.status
            if row.status in {"succeeded", "excluded"} and row.source_deleted_at is None:
                row.source_deleted_at = utcnow()
        elif row.status == "processing":
            row.outcome = "processing"
        if commit:
            cache.delete(cleaning_batch_cache_key(row.batch_id))
            session.commit()
        return row.outcome or row.status

    pending = sum(
        item.status
        in {
            "pending",
            "awaiting_model",
            "model_processing",
            "shard_pending",
            "shard_indexing",
            "staged",
        }
        for item in documents
    )
    processing = sum(item.status == "processing" for item in documents)
    succeeded = sum(item.status == "succeeded" for item in documents)
    excluded = sum(item.status == "excluded" for item in documents)
    review_required = sum(item.status == "review_required" for item in documents)
    failed = sum(item.status == "failed" for item in documents)
    total = len(documents)
    if pending or processing:
        if any(item.status == "awaiting_model" for item in documents):
            row.status = "awaiting_model"
            row.outcome = "processing"
            row.stage = "awaiting_model"
        elif any(item.status.startswith("shard_") for item in documents):
            row.status = "processing"
            row.outcome = "processing"
            row.stage = "shard_indexing"
        else:
            row.status = "processing"
            row.outcome = "processing"
            row.stage = "publishing"
    elif excluded == total:
        row.status = "excluded"
        row.outcome = "excluded"
        row.stage = "completed"
    elif succeeded + excluded == total:
        row.status = "succeeded"
        row.outcome = "succeeded"
        row.stage = "completed"
    elif review_required == total:
        row.status = "review_required"
        row.outcome = "review_required"
        row.stage = "review"
    elif failed == total:
        row.status = "failed"
        row.outcome = "failed"
        row.stage = "failed"
    else:
        row.status = "mixed"
        row.outcome = "mixed"
        row.stage = "completed"
    # A prior atomic rollback or transient worker error must not remain as the
    # file-level error after every retryable child has reached a terminal
    # succeeded/excluded/review state.  The item-level audit retains the
    # original evidence.
    if row.outcome in {"succeeded", "excluded", "review_required", "mixed"}:
        row.error_code = None
        row.error_message = None
    if row.outcome in {"succeeded", "excluded"}:
        target = settings.upload_root.resolve() / row.batch_id / row.storage_filename
        target.unlink(missing_ok=True)
        row.source_deleted_at = utcnow()
    if commit:
        cache.delete(cleaning_batch_cache_key(row.batch_id))
        session.commit()
    return row.outcome


def _set_file_state(
    file_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    with SessionLocal() as session:
        row = session.get(CleaningFile, file_id)
        if row is None:
            return
        if status is not None:
            row.status = status
        if stage is not None:
            row.stage = stage
        row.error_code = error_code
        row.error_message = error_message
        _refresh_file_state(session, row.id, commit=False)
        _finish_batch(session, row.batch_id)
        session.commit()


def _set_document_state(
    document_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool | None = None,
) -> None:
    with SessionLocal() as session:
        row = session.get(CleaningDocument, document_id)
        if row is None:
            return
        if status is not None:
            row.status = status
        if stage is not None:
            row.stage = stage
        row.error_code = error_code
        row.error_message = error_message
        if retryable is not None:
            row.retryable = retryable
        _refresh_file_state(session, row.file_id, commit=False)
        _finish_batch(session, row.file.batch_id)
        session.commit()


def _publish_document(
    session,
    row: CleaningDocument,
    batch: CleaningBatch,
    file_row: CleaningFile,
    *,
    stage_only: bool = False,
) -> bool:
    client: DifyKnowledgeClient | None = None
    created_remote_id = ""
    replaced_catalog_document_id = ""
    replaced_activity_document_ids: list[str] = []
    previous_activity_document_id = ""
    previous_activity_batch_id = ""
    try:
        client = _knowledge_client(batch)
        row.status = "processing"
        row.stage = "dify_upload"
        row.attempts += 1
        row.error_code = None
        row.error_message = None
        session.commit()
        if batch.strategy_code == "recent_activity":
            event_id = str((row.metadata_json or {}).get("event_id") or "")
            if event_id:
                if row.dify_document_id:
                    previous_activity_document_id = str(row.dify_document_id)
                    previous_activity_batch_id = str(row.dify_batch_id or "")
                    replaced_activity_document_ids.append(str(row.dify_document_id))
                candidates = session.scalars(
                    select(CleaningDocument)
                    .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
                    .join(CleaningBatch, CleaningFile.batch_id == CleaningBatch.id)
                    .where(
                        CleaningBatch.strategy_code == "recent_activity",
                        CleaningDocument.status == "succeeded",
                        CleaningDocument.dify_document_id.is_not(None),
                        CleaningDocument.id != row.id,
                    )
                ).all()
                replaced_activity_document_ids.extend(
                    [
                        str(item.dify_document_id)
                        for item in candidates
                        if (item.metadata_json or {}).get("event_id") == event_id
                    ]
                )
                if replaced_activity_document_ids:
                    audit = dict(row.audit_json or {})
                    pending_ids = audit.get("_pending_activity_replacement_document_ids")
                    if not isinstance(pending_ids, list):
                        pending_ids = []
                    audit["_pending_activity_replacement_document_ids"] = list(
                        dict.fromkeys(
                            [
                                *(str(item) for item in pending_ids if str(item).strip()),
                                *replaced_activity_document_ids,
                            ]
                        )
                    )
                    row.audit_json = audit
                    session.commit()
        if row.dify_document_id and batch.strategy_code != "recent_activity":
            if not _remove_or_disable_document(client, row.dify_document_id):
                _queue_document_cleanup(row.dify_document_id, client.dataset_id)
            row.dify_document_id = None
            row.dify_batch_id = None
            session.commit()

        audit = dict(row.audit_json or {})
        row.cleaned_markdown = finalize_knowledge_markdown(
            row.cleaned_markdown,
            audit=audit,
        )
        row.audit_json = audit
        if not row.cleaned_markdown.strip():
            raise ValueError("知识库正文为空，禁止发布")
        row.warnings_json = user_visible_warnings(row.warnings_json or [])
        row.content_sha256 = _content_sha(
            row.cleaned_markdown,
            row.metadata_json or {},
            row.chunking_json or {},
        )
        chunking = _chunking_from_json(row.chunking_json)
        publication_markdown = row.cleaned_markdown
        if batch.strategy_code == "personal_business_consultation":
            # Keep local cleaned Markdown marker-free.  Insert deterministic
            # child boundaries only in the Dify request payload so source
            # fallback/reprocessing never sees forged platform separators.
            from knowledge_cleaner.consultation_rag import render_publication_markdown

            publication_markdown = render_publication_markdown(
                row.cleaned_markdown,
                child_max_chars=max(300, min(800, chunking.child_max_tokens * 2)),
            )
            if not publication_markdown.strip():
                raise ValueError("知识库发布正文为空，无法生成咨询案例子块")
        publication_digest = normalized_publication_content_sha256(publication_markdown)
        if batch.strategy_code != "recent_activity":
            row.metadata_json = {
                **(row.metadata_json or {}),
                "publication_content_sha256": publication_digest,
            }
        candidates = session.scalars(
            select(CleaningDocument)
            .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
            .join(CleaningBatch, CleaningFile.batch_id == CleaningBatch.id)
            .where(
                CleaningBatch.knowledge_base_code == batch.knowledge_base_code,
                CleaningBatch.strategy_code == batch.strategy_code,
                CleaningBatch.spec_revision == batch.spec_revision,
                CleaningDocument.status == "succeeded",
                CleaningDocument.dify_document_id.is_not(None),
                CleaningDocument.remote_deleted_at.is_(None),
                CleaningDocument.id != row.id,
            )
            .options(selectinload(CleaningDocument.file).selectinload(CleaningFile.batch))
        ).all()
        duplicate = None
        for candidate in candidates if batch.strategy_code != "recent_activity" else ():
            if not str(candidate.dify_document_id or "").strip():
                continue
            if (
                normalized_publication_content_sha256(
                    _publication_markdown_for_document(candidate, candidate.file.batch)
                )
                != publication_digest
            ):
                continue
            if _remote_duplicate_is_live(session, client=client, candidate=candidate):
                duplicate = candidate
                break
        if duplicate is not None:
            row.status = "excluded"
            row.stage = "duplicate_noop"
            row.error_code = "EXACT_CONTENT_DUPLICATE"
            row.error_message = "同一知识库与清洗契约已存在相同正文，未重复发布"
            row.retryable = False
            row.audit_json = {
                **audit,
                "publication_content_sha256": publication_digest,
                "duplicate_of_item_id": duplicate.id,
                "duplicate_scope": {
                    "knowledge_base_code": batch.knowledge_base_code,
                    "strategy_code": batch.strategy_code,
                    "spec_revision": batch.spec_revision,
                },
            }
            session.commit()
            return True
        audit["publication_input"] = {
            "content_format": "markdown",
            "content_bytes": len(publication_markdown.encode("utf-8")),
            "markdown_sha256": hashlib.sha256(publication_markdown.encode("utf-8")).hexdigest(),
            "local_markdown_sha256": hashlib.sha256(
                row.cleaned_markdown.encode("utf-8")
            ).hexdigest(),
        }
        audit["publication_content_sha256"] = publication_digest
        row.audit_json = audit
        session.commit()

        document_id, dify_batch_id = client.create_document(
            row.dify_document_name,
            publication_markdown,
            doc_form=chunking.doc_form,
            process_mode=chunking.process_mode,
            parent_mode=chunking.parent_mode,
            parent_separator=chunking.parent_separator,
            parent_max_tokens=chunking.parent_max_tokens,
            child_separator=chunking.child_separator,
            child_max_tokens=chunking.child_max_tokens,
            parent_chunk_overlap=chunking.parent_chunk_overlap,
            child_chunk_overlap=chunking.child_chunk_overlap,
        )
        expected_parent_count = 1 if chunking.parent_mode == "full-doc" else None
        expected_child_counts: list[int] = []
        created_remote_id = document_id
        row.dify_document_id = document_id
        row.dify_batch_id = dify_batch_id
        row.stage = "dify_indexing"
        session.commit()

        client.wait_for_indexing(
            dify_batch_id,
            document_id=document_id,
            timeout_seconds=1800,
            interval=2,
        )
        structure = client.verify_document_structure(
            document_id,
            parent_mode=_chunking_from_json(row.chunking_json).parent_mode,
            expected_parent_count=expected_parent_count,
            expected_child_counts=expected_child_counts or None,
        )
        row.remote_parent_count = int(structure["parent_count"])
        row.remote_child_count = int(structure["child_count"])
        # Dify's segment read is the only available observation of the
        # completed remote body.  Persist its opaque digest locally; it is
        # compared before a future duplicate_noop and never exposed as source
        # content or a remote metadata substitute.
        # ``recent_activity`` deliberately retains its established publication
        # contract. It does not consume the new content-witness protocol, so
        # an extra segment read must not turn a successful activity upload into
        # a compensating publication failure.
        witness_reader = getattr(client, "document_content_witness", None)
        if batch.strategy_code != "recent_activity" and callable(witness_reader):
            remote_witness = str(witness_reader(document_id)).strip()
            if not re.fullmatch(r"[0-9a-f]{64}", remote_witness):
                raise RuntimeError("Dify已索引正文见证无效")
            audit["remote_content_witness_sha256"] = remote_witness
            row.audit_json = audit
        row.stage = "metadata"
        session.commit()

        strategy = STRATEGY_REGISTRY.get(batch.strategy_code)
        remote_metadata = _project_dify_metadata(
            row.metadata_json or {},
            strategy=strategy,
        )
        field_types = getattr(strategy, "dify_metadata_types", None)
        if field_types:
            field_ids = client.ensure_metadata_names(
                sorted(remote_metadata),
                field_types=field_types,
            )
            client.set_document_metadata(
                document_id,
                remote_metadata,
                field_ids,
                field_types=field_types,
            )
        else:
            field_ids = client.ensure_metadata_names(sorted(remote_metadata))
            client.set_document_metadata(document_id, remote_metadata, field_ids)
        client.verify_document_metadata(document_id, remote_metadata)
        if stage_only:
            row.status = "staged"
            row.stage = "source_commit"
            row.retryable = False
            session.commit()
            return True
        if batch.strategy_code in {"course_information", "learning_route"}:
            payload = (row.audit_json or {}).get("catalog_record")
            if not isinstance(payload, dict):
                raise ValueError("课程目录清洗结果缺少受控结构化记录")
            replaced_catalog_document_id = sync_cleaning_catalog_record(
                session,
                strategy_code=batch.strategy_code,
                payload=payload,
                dify_document_id=document_id,
                source_file=file_row.original_filename,
            )
        row.status = "succeeded"
        row.stage = "completed"
        row.retryable = False
        if batch.strategy_code in {"course_information", "learning_route"}:
            bump_catalog_revision(session)
        if batch.strategy_code == "recent_activity":
            for document_id in dict.fromkeys(replaced_activity_document_ids):
                _ensure_document_cleanup_task(session, document_id, client.dataset_id)
        session.commit()
        if replaced_activity_document_ids:
            cleanup_completed = True
            for document_id in dict.fromkeys(replaced_activity_document_ids):
                cleanup_task = session.scalar(
                    select(ExternalDeletionTask)
                    .where(
                        ExternalDeletionTask.kind == "dify_document",
                        ExternalDeletionTask.external_id == document_id,
                    )
                    .limit(1)
                )
                if _remove_or_disable_document(client, document_id):
                    if cleanup_task is not None:
                        session.delete(cleanup_task)
            if cleanup_completed:
                audit = dict(row.audit_json or {})
                audit.pop("_pending_activity_replacement_document_ids", None)
                row.audit_json = audit
                session.commit()
        if batch.strategy_code in {"course_information", "learning_route"}:
            # DB is authoritative.  Rebuild the effective cache only after its
            # transaction succeeds; an old Dify copy is then safe to retire.
            try:
                rebuild_snapshot(session)
            except Exception:
                # The next scheduled rebuild repairs the cache.  Do not turn a
                # committed catalogue/Dify publish into a destructive retry.
                logger.exception("course snapshot rebuild failed after catalogue commit")
            if replaced_catalog_document_id and replaced_catalog_document_id != document_id:
                if not _remove_or_disable_document(client, replaced_catalog_document_id):
                    _queue_document_cleanup(replaced_catalog_document_id, client.dataset_id)
        return True
    except Exception as exc:
        logger.exception("cleaning document publish failed", extra={"document_id": row.id})
        if client and created_remote_id:
            if not _remove_or_disable_document(client, created_remote_id):
                _queue_document_cleanup(created_remote_id, client.dataset_id)
        if batch.strategy_code == "recent_activity" and previous_activity_document_id:
            row.dify_document_id = previous_activity_document_id
            row.dify_batch_id = previous_activity_batch_id or None
        retry = row.attempts < 2
        row.status = "pending" if retry else "failed"
        row.stage = "retrying" if retry else "failed"
        row.error_code = (
            "DIFY_DUPLICATE_VERIFICATION_FAILED"
            if isinstance(exc, RemoteDuplicateVerificationError)
            else "DIFY_PUBLICATION_FAILED"
        )
        row.error_message = safe_exception_message(exc, limit=2000)
        row.retryable = not retry
        session.commit()
        if retry:
            raise
        return False
    finally:
        if client:
            client.close()


def _commit_source_publication(
    session,
    *,
    file_row: CleaningFile,
    batch: CleaningBatch,
    documents: list[CleaningDocument],
) -> None:
    replaced_document_ids: list[str] = []
    replaced_activity_document_ids: list[str] = []
    activity_replacement_markers: list[CleaningDocument] = []
    activity_cleanup_client = None
    try:
        if batch.strategy_code in {
            "course_information",
            "learning_route",
            "course_route_catalog",
        }:
            for document in documents:
                payload = (document.audit_json or {}).get("catalog_record")
                if not isinstance(payload, dict):
                    raise ValueError("课程目录清洗结果缺少受控结构化记录")
                effective_strategy = batch.strategy_code
                if batch.strategy_code == "course_route_catalog":
                    effective_strategy = (
                        "course_information"
                        if (document.audit_json or {}).get("catalog_kind") == "course"
                        else "learning_route"
                    )
                old_id = sync_cleaning_catalog_record(
                    session,
                    strategy_code=effective_strategy,
                    payload=payload,
                    dify_document_id=str(document.dify_document_id or ""),
                    source_file=file_row.original_filename,
                )
                if old_id and old_id != document.dify_document_id:
                    replaced_document_ids.append(old_id)
            bump_catalog_revision(session)
        for document in documents:
            document.status = "succeeded"
            document.stage = "completed"
            document.retryable = False
            document.error_code = None
            document.error_message = None
        if batch.strategy_code == "recent_activity":
            for document in documents:
                pending_ids = (document.audit_json or {}).get(
                    "_pending_activity_replacement_document_ids"
                )
                if isinstance(pending_ids, list):
                    replaced_activity_document_ids.extend(
                        str(item) for item in pending_ids if str(item).strip()
                    )
                    activity_replacement_markers.append(document)
            current_document_ids = {document.id for document in documents}
            event_ids = {
                str((document.metadata_json or {}).get("event_id") or "") for document in documents
            }
            event_ids.discard("")
            if event_ids:
                candidates = session.scalars(
                    select(CleaningDocument)
                    .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
                    .join(CleaningBatch, CleaningFile.batch_id == CleaningBatch.id)
                    .where(
                        CleaningBatch.strategy_code == "recent_activity",
                        CleaningDocument.status == "succeeded",
                        CleaningDocument.dify_document_id.is_not(None),
                        CleaningDocument.id.not_in(current_document_ids),
                    )
                ).all()
                replaced_activity_document_ids.extend(
                    str(item.dify_document_id)
                    for item in candidates
                    if (item.metadata_json or {}).get("event_id") in event_ids
                )
            if replaced_activity_document_ids:
                activity_cleanup_client = _knowledge_client(batch)
                for document_id in dict.fromkeys(replaced_activity_document_ids):
                    _ensure_document_cleanup_task(
                        session,
                        document_id,
                        activity_cleanup_client.dataset_id,
                    )
        session.commit()
    except Exception:
        session.rollback()
        raise

    if batch.strategy_code in {
        "course_information",
        "learning_route",
        "course_route_catalog",
    }:
        try:
            rebuild_snapshot(session)
        except Exception:
            logger.exception(
                "course snapshot rebuild failed after source commit",
                extra={"file_id": file_row.id},
            )
        if replaced_document_ids:
            client = _knowledge_client(batch)
            try:
                for document_id in dict.fromkeys(replaced_document_ids):
                    if not _remove_or_disable_document(client, document_id):
                        _queue_document_cleanup(document_id, client.dataset_id)
            finally:
                client.close()
    if replaced_activity_document_ids:
        cleanup_completed = True
        client = activity_cleanup_client
        try:
            if client is None:
                client = _knowledge_client(batch)
            for document_id in dict.fromkeys(replaced_activity_document_ids):
                if _remove_or_disable_document(client, document_id):
                    cleanup_task = session.scalar(
                        select(ExternalDeletionTask)
                        .where(
                            ExternalDeletionTask.kind == "dify_document",
                            ExternalDeletionTask.external_id == document_id,
                        )
                        .limit(1)
                    )
                    if cleanup_task is not None:
                        session.delete(cleanup_task)
        except Exception:
            cleanup_completed = False
            logger.exception("activity replacement cleanup failed after source commit")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    cleanup_completed = False
                    logger.exception("activity replacement client close failed")
        if cleanup_completed:
            for document in activity_replacement_markers:
                audit = dict(document.audit_json or {})
                audit.pop("_pending_activity_replacement_document_ids", None)
                document.audit_json = audit
            session.commit()


def _compensate_source_publication(
    session,
    *,
    file_row: CleaningFile,
    batch: CleaningBatch,
    retry: bool,
) -> None:
    documents = list(
        session.scalars(
            select(CleaningDocument)
            .where(CleaningDocument.file_id == file_row.id)
            .order_by(CleaningDocument.sequence)
        )
    )
    remote_ids = [
        str(document.dify_document_id) for document in documents if document.dify_document_id
    ]
    if remote_ids:
        client = _knowledge_client(batch)
        try:
            for document_id in remote_ids:
                if not _remove_or_disable_document(client, document_id):
                    _queue_document_cleanup(document_id, client.dataset_id)
        finally:
            client.close()
    for document in documents:
        if document.status in {
            "pending",
            "processing",
            "staged",
            "succeeded",
            "failed",
        }:
            document.dify_document_id = None
            document.dify_batch_id = None
            document.remote_parent_count = 0
            document.remote_child_count = 0
            document.status = "pending" if retry else "failed"
            document.stage = "retrying" if retry else "failed"
            document.error_code = "SOURCE_ATOMIC_ROLLBACK"
            document.error_message = "同一源文件的任一子文档失败，已补偿全部远端写入"
            document.retryable = retry
    file_row.status = "processing" if retry else "failed"
    file_row.outcome = "processing" if retry else "failed"
    file_row.stage = "retrying" if retry else "failed"
    file_row.error_code = "SOURCE_ATOMIC_ROLLBACK"
    file_row.error_message = "同一源文件发布失败，已执行整文件补偿"
    session.commit()


def _publish_source_file(
    session,
    *,
    file_row: CleaningFile,
    batch: CleaningBatch,
) -> bool:
    # Serialize all publication strategies within one knowledge-base/contract
    # scope. Activity's event-specific lock alone allowed different event IDs
    # to race on the same active-document/dedup contract.
    with cache.lock(
        f"ai:content-publication:{batch.knowledge_base_code}:{batch.strategy_code}:{batch.spec_revision}",
        ttl=3600,
    ) as acquired:
        if not acquired:
            raise RuntimeError("同一清洗契约正在发布，请稍后重试")
        if batch.strategy_code != "recent_activity":
            return _publish_source_file_unlocked(session, file_row=file_row, batch=batch)

        event_ids = {
            str((metadata or {}).get("event_id") or "")
            for metadata in session.scalars(
                select(CleaningDocument.metadata_json).where(
                    CleaningDocument.file_id == file_row.id,
                    CleaningDocument.status.in_(("pending", "processing", "staged")),
                )
            )
        }
        event_ids.discard("")
        with ExitStack() as locks:
            for event_id in sorted(event_ids):
                acquired = locks.enter_context(
                    cache.lock(
                        f"ai:activity-publication:{batch.knowledge_base_code}:{event_id}",
                        ttl=3600,
                    )
                )
                if not acquired:
                    raise RuntimeError("同名活动正在发布，请稍后重试")
            return _publish_source_file_unlocked(session, file_row=file_row, batch=batch)


def _publish_source_file_unlocked(
    session,
    *,
    file_row: CleaningFile,
    batch: CleaningBatch,
) -> bool:
    documents = list(
        session.scalars(
            select(CleaningDocument)
            .where(
                CleaningDocument.file_id == file_row.id,
                CleaningDocument.status.in_(("pending", "processing", "staged")),
            )
            .order_by(CleaningDocument.sequence)
        )
    )
    if not documents:
        return False
    if batch.strategy_code == "course_information":
        by_id = {document.id: document for document in documents}
        documents = [
            by_id[document_id] for document_id in _course_document_publish_order(documents)
        ]
    elif batch.strategy_code == "course_route_catalog":
        documents = sorted(
            documents,
            key=lambda item: (
                0 if (item.audit_json or {}).get("catalog_kind") == "course" else 1,
                item.sequence,
            ),
        )
    all_file_documents = list(
        session.scalars(select(CleaningDocument).where(CleaningDocument.file_id == file_row.id))
    )
    case_documents = {
        str((item.metadata_json or {}).get("case_id") or ""): item
        for item in all_file_documents
        if (item.metadata_json or {}).get("record_type") == "consulting_case"
    }
    try:
        if batch.strategy_code == "recent_activity":
            documents = _mark_duplicate_activity_units(documents)
            if not documents:
                _refresh_file_state(session, file_row.id, commit=False)
                _finish_batch(session, batch.id)
                session.commit()
                return True
        publishable_documents: list[CleaningDocument] = []
        for document in documents:
            metadata = document.metadata_json or {}
            if metadata.get("record_type") == "methodology":
                source_case = case_documents.get(str(metadata.get("candidate_for_case_id") or ""))
                # A methodology is meaningful only when this invocation has
                # actually published its referenced case.  ``duplicate_noop``
                # is not a local case publication and may point at a distinct
                # historical remote id, so it must not leave a dangling
                # candidate_for_case_id in the knowledge base.
                # Within this source-level transaction a verified case is
                # deliberately ``staged`` until every sibling has passed its
                # own remote validation.  It is therefore a valid source for
                # its case-scoped method, unlike an historical duplicate or
                # any pending/failed case.
                if source_case is None or (
                    source_case.status != "succeeded"
                    and not (
                        source_case.status == "staged" and source_case.stage == "source_commit"
                    )
                ):
                    document.status = "failed"
                    document.stage = "failed"
                    document.error_code = "METHODOLOGY_SOURCE_CASE_FAILED"
                    document.error_message = "关联咨询案例不可发布，方法候选未发布"
                    document.retryable = False
                    continue
                if source_case.status == "succeeded":
                    source_client = _knowledge_client(batch)
                    try:
                        source_live = _remote_duplicate_is_live(
                            session,
                            client=source_client,
                            candidate=source_case,
                        )
                    finally:
                        source_client.close()
                    if not source_live:
                        document.status = "failed"
                        document.stage = "failed"
                        document.error_code = "METHODOLOGY_SOURCE_CASE_UNAVAILABLE"
                        document.error_message = "关联咨询案例的远端成果不可验证，方法候选未发布"
                        document.retryable = False
                        continue
            published = _publish_document(
                session,
                document,
                batch,
                file_row,
                stage_only=True,
            )
            if not published:
                raise RuntimeError("同一源文件存在发布失败的子文档")
            if document.status != "excluded":
                publishable_documents.append(document)
        if not publishable_documents:
            _refresh_file_state(session, file_row.id, commit=False)
            _finish_batch(session, batch.id)
            session.commit()
            return True
        _commit_source_publication(
            session,
            file_row=file_row,
            batch=batch,
            documents=publishable_documents,
        )
        _refresh_file_state(session, file_row.id, commit=False)
        _finish_batch(session, batch.id)
        session.commit()
        return True
    except Exception as exc:
        logger.exception(
            "source file publication failed; compensating",
            extra={"file_id": file_row.id},
        )
        session.expire_all()
        current_documents = list(
            session.scalars(select(CleaningDocument).where(CleaningDocument.file_id == file_row.id))
        )
        duplicate_activity = isinstance(exc, DuplicateActivityEventError)
        retry = (
            False
            if duplicate_activity
            else any(document.attempts < 2 for document in current_documents)
        )
        _compensate_source_publication(
            session,
            file_row=session.get(CleaningFile, file_row.id),
            batch=session.get(CleaningBatch, batch.id),
            retry=retry,
        )
        if duplicate_activity:
            session.expire_all()
            current_documents = list(
                session.scalars(
                    select(CleaningDocument).where(CleaningDocument.file_id == file_row.id)
                )
            )
            for document in current_documents:
                document.error_code = "DUPLICATE_ACTIVITY_EVENT_ID"
                document.error_message = safe_exception_message(exc, limit=2000)
                document.retryable = False
            failed_file = session.get(CleaningFile, file_row.id)
            if failed_file is not None:
                failed_file.error_code = "DUPLICATE_ACTIVITY_EVENT_ID"
                failed_file.error_message = safe_exception_message(exc, limit=2000)
            session.commit()
            return False
        if retry:
            raise
        return False


@celery_app.task(
    name="ai_management.process_cleaning_file",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def process_cleaning_file(self, file_id: str) -> bool:
    try:
        with SessionLocal() as session:
            row = session.scalar(
                select(CleaningFile)
                .where(CleaningFile.id == file_id)
                .options(
                    selectinload(CleaningFile.batch),
                    selectinload(CleaningFile.documents),
                )
            )
            if row is None or row.status not in {"pending", "processing"}:
                return False
            now = datetime.now(timezone.utc)
            token = uuid.uuid4().hex
            claimed = session.execute(
                update(CleaningFile)
                .where(CleaningFile.id == file_id)
                .where(CleaningFile.status.in_(["pending", "processing"]))
                .where(
                    (CleaningFile.lease_expires_at.is_(None))
                    | (CleaningFile.lease_expires_at < now)
                )
                .values(
                    lease_token=token,
                    lease_expires_at=now + timedelta(minutes=30),
                    status="processing",
                    attempts=CleaningFile.attempts + 1,
                )
            ).rowcount
            if claimed != 1:
                return False
            session.expire(row)
            row = session.scalar(
                select(CleaningFile)
                .where(CleaningFile.id == file_id)
                .options(selectinload(CleaningFile.batch), selectinload(CleaningFile.documents))
            )
            batch = row.batch
            if (
                batch.knowledge_base_code == "course"
                or batch.strategy_code in DISABLED_COURSE_STRATEGIES
            ):
                row.status = "failed"
                row.outcome = "failed"
                row.stage = "failed"
                row.error_code = "STRATEGY_DISABLED"
                row.error_message = "课程与学习路线清洗入口已关闭"
                _refresh_file_state(session, row.id, commit=False)
                _finish_batch(session, row.batch_id)
                record_event(
                    level="WARNING",
                    category="task",
                    component="cleaning_worker",
                    event_code="STRATEGY_DISABLED",
                    status="failed",
                    message="课程与学习路线清洗任务已阻断",
                    resource_type="cleaning_file",
                    resource_id=file_id,
                    session=session,
                )
                session.commit()
                return True
            strategy = STRATEGY_REGISTRY.get(batch.strategy_code)
            if batch.upload_mode != "raw" and strategy is None:
                raise ValueError("清洗策略已从当前代码中移除")
            target = settings.upload_root.resolve() / batch.id / row.storage_filename
            if not target.exists():
                raise FileNotFoundError("临时源文件不存在或已经过期")

            row.status = "processing"
            row.outcome = "processing"
            row.stage = "cleaning"
            row.error_code = None
            row.error_message = None
            for existing in list(row.documents):
                if existing.status != "succeeded":
                    session.delete(existing)
            session.commit()

            if batch.upload_mode == "raw":
                from ...cleaning import CleanedDocument, _load_markdown_like

                conversion: dict[str, Any] = {}
                raw_title, raw_markdown = _load_markdown_like(
                    target,
                    row.original_filename,
                    diagnostics=conversion,
                    clean_tables=True,
                )
                cleaned_documents = [
                    CleanedDocument(
                        markdown=raw_markdown,
                        title=raw_title,
                        metadata={"document_type": "raw_upload", "review_status": "approved"},
                        audit={"pdf_conversion": conversion} if conversion else {},
                    )
                ]
            else:
                assert strategy is not None
                cleaned_documents = strategy.clean(target, row.original_filename)
            if not cleaned_documents:
                row.status = "failed"
                row.outcome = "failed"
                row.stage = "failed"
                row.error_code = "CONTENT_EMPTY_AFTER_CLEANING"
                row.error_message = "清洗策略没有生成可入库正文"
                _refresh_file_state(session, row.id, commit=False)
                _finish_batch(session, row.batch_id)
                session.commit()
                record_event(
                    level="ERROR",
                    category="task",
                    component="cleaning_worker",
                    event_code="CONTENT_EMPTY_AFTER_CLEANING",
                    status="failed",
                    message="清洗策略没有生成可入库正文",
                    resource_type="cleaning_file",
                    resource_id=file_id,
                    session=session,
                )
                session.commit()
                return True
            if batch.strategy_code == "recent_activity":
                _mark_duplicate_activity_units(cleaned_documents)
            if Path(row.original_filename).suffix.lower() == ".pdf":
                _record_pdf_conversion_events(
                    session,
                    file_row=row,
                    batch=batch,
                    documents=cleaned_documents,
                )
            if batch.strategy_code == "course_route_catalog":
                resolved_name_conflicts = sum(
                    warning.get("code") == "duplicate_link_name_auto_resolved"
                    for document in cleaned_documents
                    for warning in (document.audit or {})
                    .get("catalog_audit", {})
                    .get("warnings", [])
                )
                if resolved_name_conflicts:
                    record_event(
                        level="WARNING",
                        category="task",
                        component="cleaning_worker",
                        event_code="CATALOG_NAME_CONFLICT_AUTO_RESOLVED",
                        status="succeeded",
                        message="课程目录名称冲突已按具体标题规则确定",
                        resource_type="cleaning_file",
                        resource_id=row.id,
                        details={"resolved_count": resolved_name_conflicts},
                        session=session,
                    )

            for sequence, document in enumerate(cleaned_documents, start=1):
                document.audit = dict(document.audit or {})
                document.markdown = finalize_knowledge_markdown(
                    document.markdown,
                    audit=document.audit,
                )
                document.warnings = user_visible_warnings(document.warnings or [])
                review_policy = (batch.common_metadata or {}).get(
                    "_cleaning_review_policy"
                ) or review_policy_snapshot(batch.strategy_code)
                review_metadata = {
                    "_cleaning_review_policy": review_policy,
                }
                if not document.markdown.strip():
                    document.disposition = "failed"
                    document.error_code = "CONTENT_EMPTY_AFTER_SANITIZE"
                    document.error_message = "公共清理后没有可入库正文"
                elif (
                    document.disposition == "review_required"
                    and source_markdown_fallback_enabled(
                        batch.strategy_code,
                        review_metadata,
                    )
                    and document.markdown.strip()
                ):
                    document.audit = preserve_review_reason(
                        document.audit,
                        document.error_code,
                        document.error_message,
                    )
                    document.disposition = (
                        "shard_pending"
                        if batch.strategy_code
                        in {
                            "wechat_public_account_cognition",
                            "zsxq_post",
                            "xiaobot_post",
                        }
                        else "pending"
                    )
                    document.error_code = None
                    document.error_message = None
                metadata = {
                    **(batch.common_metadata or {}),
                    **(document.metadata or {}),
                    "source_file_id": row.id,
                    "source_filename": row.original_filename,
                    "source_extension": row.extension,
                    "source_sha256": row.source_sha256,
                    "upload_batch_id": batch.id,
                    "cleaning_strategy": batch.strategy_code,
                    "cleaning_spec_revision": batch.spec_revision,
                    "source_document_index": sequence,
                    "build_version": batch.build_version,
                    "content_format": "markdown",
                    "_cleaning_review_policy": review_policy,
                }
                metadata = {
                    key: value for key, value in metadata.items() if value not in (None, "", [], {})
                }
                if (
                    document.disposition not in {"excluded", "review_required"}
                    and document.markdown.strip()
                ):
                    short_reason = content_review_reason(
                        document.markdown,
                        batch.strategy_code,
                        metadata,
                    )
                    if short_reason:
                        document.disposition = (
                            "failed"
                            if batch.strategy_code in CONTENT_MODEL_STRATEGIES
                            else "review_required"
                        )
                        document.error_code, document.error_message = short_reason
                chunking = document.chunking or ChunkingConfig()
                validate_chunking_boundaries(document.markdown, chunking)
                status = document.disposition or "pending"
                structure_review = status == "review_required" and (
                    bool((document.audit or {}).get("structure_conflicts"))
                    or document.error_code
                    in {
                        "CATALOG_STRUCTURE_REVIEW_REQUIRED",
                        "CATALOG_STRUCTURE_REVIEW_BLOCKED",
                    }
                )
                item = CleaningDocument(
                    file_id=row.id,
                    sequence=sequence,
                    title=document.title[:300],
                    dify_document_name=dify_document_name(
                        row.original_filename,
                        row.source_sha256,
                        sequence,
                        split=len(cleaned_documents) > 1,
                    ),
                    document_key=f"{row.id}:{sequence}:{row.source_sha256[:12]}",
                    status=status,
                    stage=(
                        "catalog_structure_review"
                        if structure_review
                        else (
                            "review"
                            if status == "review_required"
                            else (
                                "failed"
                                if status == "failed"
                                else (
                                    "excluded"
                                    if status == "excluded"
                                    else (
                                        "shard_pending" if status == "shard_pending" else "pending"
                                    )
                                )
                            )
                        )
                    ),
                    error_code=document.error_code,
                    error_message=document.error_message,
                    metadata_json=metadata,
                    chunking_json=asdict(chunking),
                    audit_json=document.audit or {},
                    warnings_json=document.warnings or [],
                    cleaned_markdown=document.markdown,
                    # Initial conversion/quality/model-admission failures may
                    # retain diagnostic Markdown, but that body is never a
                    # publication retry payload.  They must be retried from
                    # the source file so OCR, anonymisation and the model
                    # gate run again.
                    retryable=False,
                )
                item.content_sha256 = _content_sha(
                    item.cleaned_markdown, item.metadata_json, item.chunking_json
                )
                if batch.strategy_code == "recent_activity":
                    event_id = str((metadata or {}).get("event_id") or "")
                    activity_name = str((metadata or {}).get("source_title") or document.title)
                    if event_id and activity_name:
                        item.dify_document_name = dify_activity_document_name(
                            activity_name,
                            event_id,
                            _activity_content_version_sha(
                                item.cleaned_markdown,
                                item.metadata_json,
                                item.chunking_json,
                            ),
                            source_filename=row.original_filename,
                        )
                # A local historical success is provenance only.  Do not
                # terminally deduplicate while creating the source item:
                # publication owns the bounded remote live/content check and
                # can release a deleted, disabled or mismatched association.
                # This point may only create a pending item; same-run
                # serialization is handled by the publication lock.
                session.add(item)
                session.flush()
            session.commit()

            pending_documents = list(
                session.scalars(
                    select(CleaningDocument)
                    .where(
                        CleaningDocument.file_id == row.id,
                        CleaningDocument.status == "pending",
                    )
                    .order_by(CleaningDocument.sequence)
                )
            )
            blocking_reviews = list(
                session.scalars(
                    select(CleaningDocument).where(
                        CleaningDocument.file_id == row.id,
                        CleaningDocument.status == "review_required",
                    )
                )
            )
            if blocking_reviews and source_markdown_fallback_enabled(
                batch.strategy_code,
                batch.common_metadata or review_policy_snapshot(batch.strategy_code),
            ):
                for item in blocking_reviews:
                    if not item.cleaned_markdown.strip():
                        continue
                    item.audit_json = preserve_review_reason(
                        item.audit_json,
                        item.error_code,
                        item.error_message,
                    )
                    item.audit_json["source_markdown_fallback"] = True
                    item.status = (
                        "shard_pending"
                        if batch.strategy_code
                        in {
                            "wechat_public_account_cognition",
                            "zsxq_post",
                            "xiaobot_post",
                        }
                        else "pending"
                    )
                    item.stage = item.status
                    item.error_code = None
                    item.error_message = None
                    item.retryable = True
                session.commit()
                blocking_reviews = [
                    item for item in blocking_reviews if item.status == "review_required"
                ]
                pending_documents = list(
                    session.scalars(
                        select(CleaningDocument)
                        .where(
                            CleaningDocument.file_id == row.id,
                            CleaningDocument.status == "pending",
                        )
                        .order_by(CleaningDocument.sequence)
                    )
                )
            if (
                blocking_reviews
                and pending_documents
                and batch.strategy_code
                not in {
                    "personal_business_consultation",
                    "wechat_public_account_cognition",
                    "zsxq_post",
                    "xiaobot_post",
                }
            ):
                for item in pending_documents:
                    item.status = "review_required"
                    item.stage = "review"
                    item.error_code = "SOURCE_REVIEW_REQUIRED"
                    item.error_message = "同一源文件存在需复核子文档，整文件暂不发布"
                    item.audit_json = {
                        **(item.audit_json or {}),
                        "source_atomic_original_disposition": "pending",
                    }
                pending_documents = []
                session.commit()
            if pending_documents:
                ordered_ids = (
                    _course_document_publish_order(pending_documents)
                    if batch.strategy_code == "course_information"
                    else [item.id for item in pending_documents]
                )
                process_cleaning_document.apply_async(
                    args=[ordered_ids[0]],
                    # All Dify writes share the dedicated single-concurrency
                    # publication worker. OCR is complete before this point.
                    queue="ai_publication",
                )
            shard_pending_documents = list(
                session.scalars(
                    select(CleaningDocument).where(
                        CleaningDocument.file_id == row.id,
                        CleaningDocument.status == "shard_pending",
                    )
                )
            )
            if shard_pending_documents:
                from .model_cleaning import _plan_post_publication, publish_dify_shard

                shard_ids = _plan_post_publication(session, row.batch_id)
                session.commit()
                pending_shard_ids = list(
                    session.scalars(
                        select(DifyPublicationShard.id).where(
                            DifyPublicationShard.id.in_(shard_ids),
                            DifyPublicationShard.status == "shard_pending",
                        )
                    )
                )
                for shard_id in pending_shard_ids:
                    publish_dify_shard.apply_async(args=[shard_id], queue="ai_publication")
            awaiting_model_documents = list(
                session.scalars(
                    select(CleaningDocument).where(
                        CleaningDocument.file_id == row.id,
                        CleaningDocument.status == "awaiting_model",
                    )
                )
            )
            _refresh_file_state(session, row.id, commit=False)
            _finish_batch(session, row.batch_id)
            session.commit()
            if awaiting_model_documents:
                from .model_cleaning import maybe_plan_model_task

                maybe_plan_model_task(row.batch_id)
        record_event(
            level="INFO",
            category="task",
            component="cleaning_worker",
            event_code="CLEANING_FILE_PARSED",
            status="succeeded",
            message="清洗文件解析完成",
            resource_type="cleaning_file",
            resource_id=file_id,
        )
        return True
    except Exception as exc:
        logger.exception("cleaning file failed", extra={"file_id": file_id})
        terminal_failure = False
        content_missing = isinstance(exc, ValueError) and any(
            marker in str(exc)
            for marker in (
                "没有可清洗正文",
                "没有可清洗的",
                "没有可入库文档",
                "正文为空",
                "未生成可入库正文",
                "没有活动通过硬事实校验",
            )
        )
        failure_context: dict[str, Any] = {}
        sensitive_values: tuple[str, ...] = ()
        with SessionLocal() as session:
            row = session.get(CleaningFile, file_id)
            if row:
                failure_context = {
                    "file_id": row.id,
                    "batch_id": row.batch_id,
                    "strategy_code": row.batch.strategy_code,
                    "knowledge_base_code": row.batch.knowledge_base_code,
                    "attempts": row.attempts,
                    "source_extension": row.extension,
                    "source_sha256_prefix": row.source_sha256[:12],
                }
                if isinstance(exc, ActivityCleaningError):
                    failure_context["activity_audit"] = exc.audit
                sensitive_values = (row.original_filename,)
                retry = not content_missing and row.attempts < 2
                terminal_failure = not retry
                if content_missing:
                    row.status = "failed"
                    row.outcome = "failed"
                    row.stage = "failed"
                    row.error_code = "CONTENT_EMPTY_AFTER_CLEANING"
                    row.error_message = "清洗流程未生成可入库正文"
                else:
                    row.status = "pending" if retry else "failed"
                    row.outcome = "pending" if retry else "failed"
                    row.stage = "retrying" if retry else "failed"
                    row.error_code = "CLEANING_FAILED"
                    failure_context.update(
                        {
                            "stage": row.stage,
                            "attempts": row.attempts,
                            "error_code": row.error_code,
                        }
                    )
                    row.error_message = exception_details(
                        exc,
                        phase="cleaning_file",
                        sensitive_values=sensitive_values,
                        **failure_context,
                    )["exception_message"]
                _finish_batch(session, row.batch_id)
                session.commit()
                if retry:
                    raise self.retry(exc=exc, countdown=10, max_retries=1)
        if content_missing:
            record_event(
                level="ERROR",
                category="task",
                component="cleaning_worker",
                event_code="CONTENT_EMPTY_AFTER_CLEANING",
                status="failed",
                message="清洗流程未生成可入库正文",
                resource_type="cleaning_file",
                resource_id=file_id,
                details=exception_details(
                    exc,
                    phase="cleaning_file_content_empty",
                    sensitive_values=sensitive_values,
                    **failure_context,
                    error_code="CONTENT_EMPTY_AFTER_CLEANING",
                ),
            )
            return True
        if terminal_failure:
            with SessionLocal() as session:
                row = session.get(CleaningFile, file_id)
                if row and Path(row.original_filename).suffix.lower() == ".pdf":
                    record_event(
                        level="ERROR",
                        category="pdf_conversion",
                        component="cleaning_worker",
                        event_code="PDF_CONVERSION_BLOCKED",
                        status="open",
                        message="PDF 转换失败，未生成可入库正文",
                        resource_type="cleaning_file",
                        resource_id=file_id,
                        details={
                            "severity": "blocked",
                            "codes": [type(exc).__name__],
                            "knowledge_base_code": row.batch.knowledge_base_code,
                            "exception": exception_details(
                                exc,
                                phase="pdf_conversion",
                                sensitive_values=(row.original_filename,),
                            ),
                        },
                        session=session,
                    )
                    session.commit()
        record_event(
            level="ERROR",
            category="task",
            component="cleaning_worker",
            event_code="CLEANING_FILE_FAILED",
            status="failed",
            message="清洗文件任务失败",
            resource_type="cleaning_file",
            resource_id=file_id,
            details=exception_details(
                exc,
                phase="cleaning_file",
                sensitive_values=sensitive_values,
                **failure_context,
            ),
        )
        return False


@celery_app.task(
    name="ai_management.process_cleaning_document",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def process_cleaning_document(self, document_id: str) -> bool:
    try:
        with SessionLocal() as session:
            row = session.scalar(
                select(CleaningDocument)
                .where(CleaningDocument.id == document_id)
                .options(selectinload(CleaningDocument.file).selectinload(CleaningFile.batch))
            )
            if row is None or row.status not in {
                "pending",
                "processing",
                "staged",
            }:
                return False
            batch = row.file.batch
            file_row = row.file
            result = _publish_source_file(
                session,
                file_row=file_row,
                batch=batch,
            )
            _refresh_file_state(session, file_row.id, commit=False)
            _finish_batch(session, file_row.batch_id)
            session.commit()
            return result
    except Exception as exc:
        failure_context: dict[str, Any] = {"document_id": document_id}
        sensitive_values: tuple[str, ...] = ()
        with SessionLocal() as session:
            row = session.get(CleaningDocument, document_id)
            if row is not None and row.attempts < 2:
                _refresh_file_state(session, row.file_id, commit=False)
                _finish_batch(session, row.file.batch_id)
                session.commit()
                raise self.retry(exc=exc, countdown=10, max_retries=1)
            if row is not None:
                _refresh_file_state(session, row.file_id, commit=False)
                _finish_batch(session, row.file.batch_id)
                session.commit()
                failure_context = {
                    "document_id": row.id,
                    "file_id": row.file_id,
                    "batch_id": row.file.batch_id,
                    "strategy_code": row.file.batch.strategy_code,
                    "knowledge_base_code": row.file.batch.knowledge_base_code,
                    "stage": row.stage,
                    "attempts": row.attempts,
                    "error_code": row.error_code,
                }
                sensitive_values = (row.file.original_filename,)
        record_event(
            level="ERROR",
            category="task",
            component="cleaning_worker",
            event_code="CLEANING_DOCUMENT_FAILED",
            status="failed",
            message="清洗条目入库失败",
            resource_type="cleaning_document",
            resource_id=document_id,
            details=exception_details(
                exc,
                phase="dify_publication",
                sensitive_values=sensitive_values,
                **failure_context,
            ),
        )
        return False


__all__ = [
    "_finish_batch",
    "_knowledge_client",
    "_queue_document_cleanup",
    "_refresh_file_state",
    "_remove_or_disable_document",
    "_set_document_state",
    "_set_file_state",
    "process_cleaning_document",
    "process_cleaning_file",
]
