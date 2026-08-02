from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from knowledge_cleaner.chunking import prepare_chunks

from ...config import get_settings
from ...course_import import _course_text, _route_text
from ...db import SessionLocal
from ...model_cleaning import (
    BailianClient,
    CourseEnrichmentResponse,
    PromptRequest,
    batch_request_line,
    build_custom_id,
    build_prompt_messages,
    choose_call_mode,
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
from ...services.observability import record_event
from ..celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()
POST_STRATEGIES = {
    "wechat_public_account_cognition",
    "zsxq_post",
    "xiaobot_post",
}
PROVIDER_TERMINAL = {"completed", "failed", "expired", "cancelled", "cancelling"}
# Dify requires a 50-unit minimum for custom segments.  Keep a conservative
# buffer after sanitization and title/context assembly.
_DIFY_MIN_CUSTOM_SEGMENT_CHARS = 120
_DIFY_MIN_SOURCE_SEGMENT_CHARS = 8


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
    return session.scalars(
        _document_statement(batch_id).execution_options(yield_per=500)
    )


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


def _realtime_chat(
    client: BailianClient,
    request: PromptRequest,
    *,
    model: str,
    repair_hint: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    token_cost = _estimated_tokens(request) + 500
    while True:
        wait_seconds = model_rate_limiter.acquire(
            model=model,
            token_cost=token_cost,
        )
        if wait_seconds <= 0:
            break
        time.sleep(min(60, wait_seconds))
    return client.chat_json(
        build_prompt_messages(request, repair_hint=repair_hint), model=model
    )


def _model_contract_retry_hint(exc: Exception) -> str:
    """Return a safe, structural-only correction request for one retry.

    The provider never receives the failed payload or source filename.  The
    hint contains only a stable contract requirement, so it can repair common
    Qwen JSON omissions without weakening the evidence gate.
    """

    message = str(exc)
    if "完整覆盖" in message:
        return (
            "输入 blocks 的每一个 key 必须且只能出现一次："
            "保留的列入 keep_block_ids，其余列入 drop_block_ids。"
        )
    if "未知来源块" in message or "来源块不能重复归类" in message:
        return "只可使用输入 blocks 中已有的 key，每个 key 只能归类一次。"
    if "否定状态" in message:
        return "不得改变来源中的肯定或否定事实状态；没有证据的章节不要输出。"
    if "数字或日期" in message:
        return "不得增加来源块未出现的数字、日期、比例或金额。"
    if "人物归属" in message:
        return "不得增加或变更来源块中的人物归属。"
    if "课程目录模型未返回课程描述" in message:
        return "课程记录若有来源依据请填写 description；只能依据输入来源块概括，不得编造事实；无依据时留空，由服务按来源事实兜底或阻断。"
    if "课程目录模型未返回方向候选" in message:
        return "课程记录只有在输入来源有依据时才填写 direction_candidate；不得猜测或改名、改链接，无依据时留空并接受自动原子阻断。"
    if "学习路线模型未返回路线目标" in message:
        return "路线记录只有在输入来源有依据时才填写 route_target；只能概括来源中的适用目标，不得从名称或顺序猜测、不得编造收益承诺。"
    return "字段、枚举值、来源块引用和 JSON 类型必须完全符合系统提示。"


def _candidate_fingerprint(
    documents: Iterable[CleaningDocument], prompt_version: str
) -> str:
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
        select(CleaningDocument)
        .join(CleaningFile)
        .where(CleaningFile.batch_id == batch.id)
    )
    for document in documents:
        if document.status == "excluded":
            continue
        document.status = "failed"
        document.stage = "failed"
        document.error_code = error_code
        document.error_message = error_message
        document.retryable = False
    for file_row in session.scalars(
        select(CleaningFile).where(CleaningFile.batch_id == batch.id)
    ):
        file_row.status = "failed"
        file_row.outcome = "failed"
        file_row.stage = "failed"
        file_row.error_code = error_code
        file_row.error_message = error_message
    batch.status = "completed"


def maybe_plan_model_task(batch_id: str) -> str | None:
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
            item.stage in {"upload", "cleaning"}
            or item.status == "pending"
            for item in batch.files
        ):
            return None
        existing = session.scalar(
            select(ModelCleaningTask).where(
                ModelCleaningTask.batch_id == batch_id,
                ModelCleaningTask.status.not_in(
                    ("failed", "cancelled", "superseded")
                ),
            )
        )
        if existing is not None:
            return existing.id
        auto_catalog = (
            batch.strategy_code == "course_route_catalog"
            and settings.course_catalog_auto_publish
        )
        candidate_count = 0
        estimated_input = 0
        fingerprint_digest = hashlib.sha256()
        oversized_reason = ""
        for document in _iter_documents_for_batch(session, batch_id):
            request = _request(document, batch.strategy_code)
            tokens = _estimated_tokens(request)
            if tokens > settings.cleaning_candidate_max_tokens:
                if auto_catalog:
                    oversized_reason = "单候选超过模型输入上限"
                else:
                    document.status = "review_required"
                    document.stage = "review"
                    document.error_code = "MODEL_INPUT_TOO_LARGE"
                    document.error_message = "单候选超过自动模型清洗上限，需要人工复核"
                continue
            candidate_count += 1
            estimated_input += tokens
            fingerprint_digest.update(
                f"{document.id}:{document.content_sha256}\n".encode("utf-8")
            )
        if not candidate_count:
            if oversized_reason:
                _fail_catalog_auto_run(session, batch, oversized_reason)
            session.commit()
            return None
        fingerprint_digest.update(
            f"prompt:{settings.cleaning_prompt_version}".encode("utf-8")
        )
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
        input_cost = (
            estimated_input
            / 1_000_000
            * input_price_per_million
            * price_discount
        )
        estimated_output = max(500, candidate_count * 500)
        output_cost = (
            estimated_output
            / 1_000_000
            * output_price_per_million
            * price_discount
        )
        estimated_cost_high = input_cost + output_cost
        task = ModelCleaningTask(
            batch_id=batch_id,
            mode=mode,
            model=settings.cleaning_model,
            prompt_version=settings.cleaning_prompt_version,
            status="awaiting_approval" if mode == "batch" else "queued",
            candidate_fingerprint=fingerprint,
            candidate_count=candidate_count,
            estimated_input_tokens=estimated_input,
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
        process_realtime_model_task.apply_async(
            args=[task_id], queue="ai_model_realtime"
        )
    elif auto_submit_batch:
        try:
            submit_batch_model_task.apply_async(
                args=[task_id], queue="ai_model_batch"
            )
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


def _apply_model_payload(
    document: CleaningDocument,
    strategy_code: str,
    request: PromptRequest,
    payload: dict[str, Any],
) -> None:
    audit = dict(document.audit_json or {})
    available = set(request.blocks)
    if strategy_code in POST_STRATEGIES:
        result = validate_semantic_response(
            strategy_code, payload, available_block_ids=available
        )
        audit["model_result"] = result.model_dump(mode="json")
        if result.disposition == "exclude":
            document.status = "excluded"
            document.stage = "completed"
            document.error_code = result.reason_code
            document.error_message = "模型判定不适合商业顾问知识库"
        elif result.disposition == "review":
            document.status = "review_required"
            document.stage = "review"
            document.error_code = result.reason_code
            document.error_message = "模型判定需要人工复核"
        else:
            kept = [
                request.blocks[block_id]
                for block_id in result.keep_block_ids
            ]
            body = "\n\n".join(kept)
            chunks = prepare_chunks(
                body,
                title=document.title,
                content_type="article",
                topics=(result.topic,) if result.topic else (),
                overlap=0,
            )
            raw_children = [
                child
                for chunk in chunks
                for child in chunk.children
                if child.strip()
            ]
            children = [
                _post_child_for_dify(child, title=document.title)
                for child in raw_children
            ]
            if any(child is None for child in children):
                document.status = "review_required"
                document.stage = "review"
                document.error_code = "DIFY_CHILD_TOO_SHORT"
                document.error_message = (
                    "帖子有效内容不足以满足知识库最小分段长度，需要人工合并或复核"
                )
                document.audit_json = audit
                return
            children = [child for child in children if child is not None]
            context = (
                chunks[0].content.partition("\n")[0]
                if chunks
                else f"【上下文】{document.title}｜article"
            )
            one_parent = (
                f"{context}\n\n"
                + "\n\n<<<DIFY_CHILD>>>\n\n".join(children)
                if children
                else body
            )
            document.cleaned_markdown = sanitize_for_knowledge(
                one_parent
            )
            metadata = dict(document.metadata_json or {})
            if result.topic:
                metadata["topic"] = result.topic
            document.metadata_json = metadata
            document.status = "shard_pending"
            document.stage = "shard_pending"
            document.error_code = None
            document.error_message = None
    elif strategy_code == "personal_business_consultation":
        result = validate_consultation_response(
            payload,
            available_block_ids=available,
            source_blocks=request.blocks,
        )
        parts = [
            f"## {section.heading}\n\n{section.text}"
            for section in result.sections
        ]
        document.cleaned_markdown = sanitize_for_knowledge(
            f"# {document.title}\n\n" + "\n\n".join(parts)
        )
        from knowledge_cleaner.pdf_convert import scan_residual_pii

        residual = scan_residual_pii(document.cleaned_markdown)
        forbidden_names = [
            str(value)
            for value in audit.get("forbidden_public_names") or []
            if str(value)
        ]
        if residual or any(
            value in document.cleaned_markdown for value in forbidden_names
        ):
            raise ValueError("咨询模型输出仍含潜在PII或受保护身份")
        document.status = "pending"
        document.stage = "pending"
        audit["model_result"] = result.model_dump(mode="json")
    elif strategy_code == "course_route_catalog":
        result = CourseEnrichmentResponse.model_validate(payload)
        audit["ai_candidate"] = result.model_dump(mode="json")
        audit["model_result"] = result.model_dump(mode="json")
        draft = dict(audit.get("catalog_draft") or {})
        kind = str(audit.get("catalog_kind") or "")
        direction = result.direction_candidate.strip() or str(
            draft.get("direction") or ""
        ).strip()
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
            route_target = result.route_target.strip() or str(
                draft.get("target") or ""
            ).strip()
            if not route_target:
                raise ValueError("学习路线模型未返回路线目标")
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
        audit["catalog_record"] = record.model_dump(mode="json")
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
) -> None:
    if final and auto_fail:
        document.status = "failed"
        document.stage = "failed"
        document.error_code = "CATALOG_AUTO_PUBLISH_BLOCKED"
        document.retryable = False
    else:
        document.status = "review_required" if final else "awaiting_model"
        document.stage = "review" if final else "model_retry"
        document.error_code = "MODEL_CONTRACT_INVALID"
    document.error_message = str(exc)[:1000]


def _plan_post_publication(session, batch_id: str) -> list[str]:
    shard_ids: list[str] = []
    files = list(
        session.scalars(
            select(CleaningFile).where(CleaningFile.batch_id == batch_id)
        )
    )
    for file_row in files:
        existing = list(
            session.scalars(
                select(DifyPublicationShard).where(
                    DifyPublicationShard.file_id == file_row.id
                )
            )
        )
        if existing:
            shard_ids.extend(item.id for item in existing)
            continue
        documents = list(
            session.scalars(
                select(CleaningDocument)
                .where(
                    CleaningDocument.file_id == file_row.id,
                    CleaningDocument.status == "shard_pending",
                )
                .order_by(CleaningDocument.sequence)
            )
        )
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
        for plan in plans:
            digest = hashlib.sha256(plan.markdown.encode("utf-8")).hexdigest()
            shard = DifyPublicationShard(
                file_id=file_row.id,
                shard_index=plan.index,
                status="shard_pending",
                item_count=len(plan.items),
                content_bytes=len(plan.markdown.encode("utf-8")),
                content_sha256=digest,
                markdown=plan.markdown,
                metadata_json={"record_type": "article_shard"},
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


def _validate_shard_structure(structure: dict[str, Any], *, item_count: int) -> None:
    """Allow Dify to split an oversized source item without losing any item.

    Each source item is separated before publication.  Dify may legitimately
    split that item again at its configured parent-token limit, so the remote
    parent count is a lower-bound check, never an equality check.
    """

    parent_count = int(structure.get("parent_count") or 0)
    if parent_count < item_count:
        raise ValueError(f"Dify父块数量不足：{parent_count} < {item_count}")


def _finish_model_task(task_id: str) -> None:
    with SessionLocal() as session:
        task = session.get(ModelCleaningTask, task_id)
        if task is None:
            return
        batch = session.get(CleaningBatch, task.batch_id)
        task.status = "completed"
        task.last_error = ""
        # Model completion changes document statuses directly.  Refresh every
        # source file before aggregating the batch so a normal strategy with a
        # review-required document cannot remain falsely in awaiting_model /
        # processing after the model task is terminal.
        from .cleaning import _finish_batch, _refresh_file_state

        file_ids = list(
            session.scalars(
                select(CleaningFile.id).where(CleaningFile.batch_id == task.batch_id)
            )
        )
        for file_id in file_ids:
            _refresh_file_state(session, str(file_id), commit=False)
        if batch and batch.strategy_code == "course_route_catalog" and settings.course_catalog_auto_publish:
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
                publish_dify_shard.apply_async(
                    args=[shard_id], queue="ai_publication"
                )
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
    try:
        client = _client()
        with SessionLocal() as session:
            task = session.get(ModelCleaningTask, task_id)
            if task is None or task.status not in {"queued", "running"}:
                return False
            batch = session.get(CleaningBatch, task.batch_id)
            if batch is None:
                return False
            task.status = "running"
            task.attempts += 1
            documents = _documents_for_batch(session, batch.id)
            session.commit()
            counts = {"succeeded": 0, "excluded": 0, "review": 0}
            usage = {"input_tokens": 0, "output_tokens": 0}
            for document in documents:
                request = _request(document, batch.strategy_code)
                document.status = "model_processing"
                document.stage = "model_running"
                session.commit()
                try:
                    payload, item_usage = _realtime_chat(
                        client, request, model=task.model
                    )
                    _apply_model_payload(
                        document, batch.strategy_code, request, payload
                    )
                    counts[
                        "excluded"
                        if document.status == "excluded"
                        else (
                            "review"
                            if document.status == "review_required"
                            else "succeeded"
                        )
                    ] += 1
                    usage["input_tokens"] += item_usage["input_tokens"]
                    usage["output_tokens"] += item_usage["output_tokens"]
                except Exception as exc:
                    # Retry each item once immediately; the second invalid
                    # result becomes review instead of poisoning the batch.
                    try:
                        payload, item_usage = _realtime_chat(
                            client,
                            request,
                            model=task.model,
                            repair_hint=_model_contract_retry_hint(exc),
                        )
                        _apply_model_payload(
                            document, batch.strategy_code, request, payload
                        )
                        usage["input_tokens"] += item_usage["input_tokens"]
                        usage["output_tokens"] += item_usage["output_tokens"]
                    except Exception as retry_exc:
                        _mark_contract_failure(
                            document,
                            retry_exc,
                            final=True,
                            auto_fail=(
                                batch.strategy_code == "course_route_catalog"
                                and settings.course_catalog_auto_publish
                            ),
                        )
                        counts["review"] += 1
                session.commit()
            task.result_counts = counts
            task.actual_usage = usage
            session.commit()
        _finish_model_task(task_id)
        return True
    except Exception as exc:
        logger.exception("realtime model cleaning task failed")
        with SessionLocal() as session:
            task = session.get(ModelCleaningTask, task_id)
            if task is not None:
                task.status = "failed"
                task.last_error = str(exc)[:2000]
                batch = session.get(CleaningBatch, task.batch_id)
                if (
                    batch is not None
                    and batch.strategy_code == "course_route_catalog"
                    and settings.course_catalog_auto_publish
                ):
                    _fail_catalog_auto_run(session, batch, "模型任务执行异常")
                session.commit()
        return False
    finally:
        if client:
            client.close()


def _write_batch_files(
    directory: Path,
    task: ModelCleaningTask,
    batch: CleaningBatch,
    documents: Iterable[CleaningDocument],
    *,
    repair_hints: dict[str, str] | None = None,
) -> list[Path]:
    paths: list[Path] = []
    handle = None
    request_count = 0
    current_bytes = 0
    try:
        for document in documents:
            request = _request(document, batch.strategy_code)
            line = batch_request_line(
                request,
                prompt_version=task.prompt_version,
                model=task.model,
                repair_hint=(repair_hints or {}).get(document.id, ""),
            )
            if len(line) > 1_000_000:
                raise ValueError("单条Batch请求超过1MB")
            if handle is None or (
                request_count >= settings.cleaning_batch_max_requests
                or current_bytes + len(line) > settings.cleaning_batch_max_bytes
            ):
                if handle is not None:
                    handle.close()
                path = directory / f"batch-{len(paths) + 1:04d}.jsonl"
                paths.append(path)
                handle = path.open("wb")
                request_count = 0
                current_bytes = 0
            handle.write(line)
            request_count += 1
            current_bytes += len(line)
    finally:
        if handle is not None:
            handle.close()
    return paths


@celery_app.task(
    name="ai_management.submit_batch_model_task",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def submit_batch_model_task(self, task_id: str) -> bool:
    client: BailianClient | None = None
    try:
        with SessionLocal() as session:
            task = session.get(ModelCleaningTask, task_id)
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
                        CleaningDocument.status.in_(
                            ("awaiting_model", "model_processing")
                        ),
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
            client = _client()
            if task.provider_batch_ids or task.provider_input_file_ids:
                for provider_batch_id in task.provider_batch_ids or []:
                    client.cancel_batch(provider_batch_id)
                _delete_provider_files(
                    client,
                    task,
                    list(task.provider_input_file_ids or []),
                )
                task.provider_batch_ids = []
                task.provider_batch_id = None
                task.provider_input_file_ids = []
                task.cleanup_status = "not_started"
                session.commit()
            with tempfile.TemporaryDirectory(prefix="jx-bailian-") as directory:
                paths = _write_batch_files(
                    Path(directory),
                    task,
                    batch,
                    _iter_documents_for_batch(session, task.batch_id),
                )
                input_ids: list[str] = []
                batch_ids: list[str] = []
                for path in paths:
                    input_id = client.upload_batch_path(str(path))
                    input_ids.append(input_id)
                    task.provider_input_file_ids = list(input_ids)
                    session.commit()
                    batch_id = client.create_batch(input_id)
                    batch_ids.append(batch_id)
                    task.provider_batch_ids = list(batch_ids)
                    task.provider_batch_id = batch_ids[0]
                    session.commit()
            task.provider_input_file_ids = input_ids
            task.provider_batch_ids = batch_ids
            task.provider_batch_id = batch_ids[0] if batch_ids else None
            task.status = "batch_running"
            batch.status = "model_batch_running"
            for document in _iter_documents_for_batch(session, task.batch_id):
                document.status = "model_processing"
                document.stage = "model_batch_running"
            session.commit()
        poll_batch_model_task.apply_async(
            args=[task_id], queue="ai_model_batch", countdown=60
        )
        return True
    except Exception as exc:
        logger.exception("batch model submit failed")
        replan_batch_id = ""
        with SessionLocal() as session:
            task = session.get(ModelCleaningTask, task_id)
            if task:
                task.last_error = str(exc)[:2000]
                batch = session.get(CleaningBatch, task.batch_id)
                if client:
                    for provider_batch_id in task.provider_batch_ids or []:
                        try:
                            client.cancel_batch(provider_batch_id)
                        except Exception:
                            logger.exception("failed to cancel partial Bailian batch")
                    _delete_provider_files(
                        client,
                        task,
                        [
                            *(task.provider_input_file_ids or []),
                            *(task.provider_output_file_ids or []),
                            *(task.provider_error_file_ids or []),
                        ],
                    )
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
                else:
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
                if not (
                    batch is not None
                    and batch.strategy_code == "course_route_catalog"
                    and settings.course_catalog_auto_publish
                ):
                    for document in _iter_documents_for_batch(session, task.batch_id):
                        document.status = "awaiting_model"
                        document.stage = "model_retry"
                    document.error_code = "MODEL_BATCH_SUBMIT_FAILED"
                    document.error_message = "Batch提交未完成，未发布到知识库"
                session.commit()
        if replan_batch_id:
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
) -> None:
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
                        last_error=str(exc)[:1000],
                    )
                )
                cleanup_session.commit()
    task.cleanup_status = "pending_retry" if failed else "completed"


@celery_app.task(
    name="ai_management.poll_batch_model_task",
    bind=True,
    autoretry_for=(),
    acks_late=True,
)
def poll_batch_model_task(self, task_id: str) -> bool:
    client: BailianClient | None = None
    try:
        client = _client()
        with SessionLocal() as session:
            task = session.get(ModelCleaningTask, task_id)
            if task is None or task.status != "batch_running":
                return False
            batch = session.get(CleaningBatch, task.batch_id)
            statuses = [
                client.get_batch(batch_id)
                for batch_id in (task.provider_batch_ids or [])
            ]
            state_values = {str(item.get("status") or "") for item in statuses}
            if any(state not in PROVIDER_TERMINAL for state in state_values):
                # A provider Batch can legitimately remain in progress for its
                # whole completion window.  Persist only aggregate progress so
                # the admin page distinguishes that state from a stalled local
                # worker without retaining provider identifiers or payloads.
                request_totals = {
                    key: sum(
                        int((item.get("request_counts") or {}).get(key) or 0)
                        for item in statuses
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
            output_ids = [
                str(item.get("output_file_id") or "")
                for item in statuses
                if item.get("output_file_id")
            ]
            error_ids = [
                str(item.get("error_file_id") or "")
                for item in statuses
                if item.get("error_file_id")
            ]
            task.provider_output_file_ids = output_ids
            task.provider_error_file_ids = error_ids
            if state_values != {"completed"}:
                _delete_provider_files(
                    client,
                    task,
                    [
                        *(task.provider_input_file_ids or []),
                        *output_ids,
                        *error_ids,
                    ],
                )
                task.result_counts = {
                    **(task.result_counts or {}),
                    "provider_retry_reason": sorted(state_values),
                }
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
                        document.error_code = (
                            "MODEL_PROVIDER_RETRY_APPROVAL_REQUIRED"
                        )
                        document.error_message = "Batch未完成，需重新确认费用后重试"
                    if batch:
                        batch.status = "awaiting_model_approval"
                session.commit()
                return True
            task.status = "validating"
            if batch:
                batch.status = "model_validating"
            session.commit()
            results: dict[str, dict[str, Any]] = {}
            for output_id in output_ids:
                parsed = _parse_provider_output(
                    client.get_file_content(output_id)
                )
                overlap = set(results) & set(parsed)
                if overlap:
                    raise ValueError(f"Batch跨文件custom_id重复：{sorted(overlap)[:3]}")
                results.update(parsed)
            counts = {"succeeded": 0, "excluded": 0, "review": 0}
            usage = dict(task.actual_usage or {})
            usage.setdefault("input_tokens", 0)
            usage.setdefault("output_tokens", 0)
            retry_count = 0
            small_retry_documents: list[CleaningDocument] = []
            retry_hints: dict[str, str] = {}
            for document in _iter_documents_for_batch(session, task.batch_id):
                request = _request(document, batch.strategy_code)
                custom_id = build_custom_id(request, task.prompt_version)
                item = results.pop(custom_id, None)
                if item is None:
                    document.error_code = "MODEL_BATCH_RESULT_MISSING"
                    document.error_message = "Batch结果缺少当前条目"
                    retry_hints[document.id] = (
                        "请输出完整 JSON，并逐一归类输入 blocks 的全部 key。"
                    )
                    retry_count += 1
                    if retry_count < 100:
                        small_retry_documents.append(document)
                    elif retry_count == 100:
                        small_retry_documents.clear()
                    continue
                item_usage = item.get("usage") or {}
                usage["input_tokens"] += int(
                    item_usage.get("prompt_tokens") or 0
                )
                usage["output_tokens"] += int(
                    item_usage.get("completion_tokens") or 0
                )
                try:
                    _apply_model_payload(
                        document,
                        batch.strategy_code,
                        request,
                        item["payload"],
                    )
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
                    document.error_code = "MODEL_CONTRACT_INVALID"
                    document.error_message = str(exc)[:1000]
                    retry_hints[document.id] = _model_contract_retry_hint(exc)
                    retry_count += 1
                    if retry_count < 100:
                        small_retry_documents.append(document)
                    elif retry_count == 100:
                        small_retry_documents.clear()
            if results:
                raise ValueError("Batch结果包含不属于当前候选集的custom_id")
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
                        request = _request(document, batch.strategy_code)
                        try:
                            payload, item_usage = _realtime_chat(
                                client,
                                request,
                                model=task.model,
                                repair_hint=retry_hints.get(document.id, ""),
                            )
                            _apply_model_payload(
                                document,
                                batch.strategy_code,
                                request,
                                payload,
                            )
                            usage["input_tokens"] += item_usage["input_tokens"]
                            usage["output_tokens"] += item_usage[
                                "output_tokens"
                            ]
                        except Exception as exc:
                            _mark_contract_failure(
                                document,
                                exc,
                                final=True,
                                auto_fail=(
                                    batch.strategy_code == "course_route_catalog"
                                    and settings.course_catalog_auto_publish
                                ),
                            )
                        counts[
                            "excluded"
                            if document.status == "excluded"
                            else (
                                "review"
                                if document.status == "review_required"
                                else "succeeded"
                            )
                        ] += 1
                    task.actual_usage = usage
                else:
                    with tempfile.TemporaryDirectory(
                        prefix="jx-bailian-retry-"
                    ) as directory:
                        retry_paths = _write_batch_files(
                            Path(directory),
                            task,
                            batch,
                            _iter_documents_for_batch(session, task.batch_id),
                            repair_hints=retry_hints,
                        )
                        retry_input_ids: list[str] = []
                        retry_batch_ids: list[str] = []
                        for path in retry_paths:
                            input_id = client.upload_batch_path(str(path))
                            retry_input_ids.append(input_id)
                            retry_batch_ids.append(client.create_batch(input_id))
                    _delete_provider_files(client, task, old_file_ids)
                    task.provider_input_file_ids = retry_input_ids
                    task.provider_output_file_ids = []
                    task.provider_error_file_ids = []
                    task.provider_batch_ids = retry_batch_ids
                    task.provider_batch_id = (
                        retry_batch_ids[0] if retry_batch_ids else None
                    )
                    task.result_counts = {
                        **(task.result_counts or {}),
                        "retry_round": 1,
                        "retry_candidate_count": retry_count,
                        "prior_succeeded": counts["succeeded"],
                        "prior_excluded": counts["excluded"],
                        "prior_review": counts["review"],
                    }
                    task.cleanup_status = "not_started"
                    task.status = "batch_running"
                    session.commit()
                    poll_batch_model_task.apply_async(
                        args=[task_id],
                        queue="ai_model_batch",
                        countdown=60,
                    )
                    return True
            elif retry_count:
                for document in _iter_documents_for_batch(session, task.batch_id):
                    _mark_contract_failure(
                        document,
                        ValueError(
                            document.error_message
                            or "Batch补偿重试仍未得到有效结果"
                        ),
                        final=True,
                        auto_fail=(
                            batch.strategy_code == "course_route_catalog"
                            and settings.course_catalog_auto_publish
                        ),
                    )
                    counts["review"] += 1
            previous = task.result_counts or {}
            task.result_counts = {
                "succeeded": counts["succeeded"]
                + int(previous.get("prior_succeeded") or 0),
                "excluded": counts["excluded"]
                + int(previous.get("prior_excluded") or 0),
                "review": counts["review"]
                + int(previous.get("prior_review") or 0),
                "retry_round": int(previous.get("retry_round") or 0),
            }
            _delete_provider_files(
                client,
                task,
                [
                    *(task.provider_input_file_ids or []),
                    *output_ids,
                    *error_ids,
                ],
            )
            session.commit()
        _finish_model_task(task_id)
        return True
    except Exception as exc:
        logger.exception("batch model polling failed")
        # A provider/DB connection failure is not evidence that the remote
        # Batch reached a terminal state.  Keep the task observable and poll
        # again; only an explicit provider terminal status may atomically fail
        # an automatic course catalogue run.  Scheduling is deliberately done
        # outside the database context so a dropped connection cannot orphan
        # the remote Batch by preventing the next poll from being queued.
        try:
            with SessionLocal() as session:
                task = session.get(ModelCleaningTask, task_id)
                if task is not None and task.status == "batch_running":
                    task.last_error = f"Batch轮询暂时失败：{exc}"[:2000]
                    session.commit()
        except Exception:
            logger.exception("failed to persist transient batch polling error")
        poll_batch_model_task.apply_async(
            args=[task_id], queue="ai_model_batch", countdown=60
        )
        return False
    finally:
        if client:
            client.close()


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
        settings.dify_publish_window_start_hour
        <= now_hour
        < settings.dify_publish_window_end_hour
    ):
        raise self.retry(countdown=900, max_retries=None)
    client: DifyKnowledgeClient | None = None
    created_id = ""
    try:
        with SessionLocal() as session:
            shard = session.scalar(
                select(DifyPublicationShard)
                .where(DifyPublicationShard.id == shard_id)
                .options(
                    selectinload(DifyPublicationShard.file).selectinload(
                        CleaningFile.batch
                    ),
                    selectinload(DifyPublicationShard.documents),
                )
            )
            if shard is None or shard.status not in {
                "shard_pending",
                "shard_indexing",
            }:
                return False
            dataset_id = (
                settings.dify_general_dataset_id
                if shard.file.batch.knowledge_base_code == "general"
                else settings.dify_course_dataset_id
            )
            client = DifyKnowledgeClient(
                settings.knowledge_base_url,
                settings.dify_knowledge_api_key,
                dataset_id,
                timeout=180,
            )
            replaced_document_id = str(shard.dify_document_id or "")
            shard.status = "shard_indexing"
            shard.attempts += 1
            for document in shard.documents:
                document.status = "shard_indexing"
                document.stage = "shard_indexing"
            session.commit()
            from ...cleaning import dify_document_name

            remote_name = dify_document_name(
                shard.file.original_filename,
                shard.content_sha256,
                shard.shard_index + 1,
                split=True,
            )
            document_id, dify_batch_id = client.create_document(
                remote_name,
                shard.markdown,
                doc_form="hierarchical_model",
                process_mode="hierarchical",
                parent_mode="paragraph",
                parent_separator="<<<DIFY_PARENT>>>",
                parent_max_tokens=settings.dify_parent_max_tokens,
                child_separator="<<<DIFY_CHILD>>>",
                child_max_tokens=800,
                parent_chunk_overlap=0,
                child_chunk_overlap=0,
            )
            created_id = document_id
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
            fields = client.ensure_metadata_names(["record_type"])
            client.set_document_metadata(
                document_id, {"record_type": "article_shard"}, fields
            )
            client.verify_document_metadata(
                document_id, {"record_type": "article_shard"}
            )
            shard.dify_document_id = document_id
            shard.dify_batch_id = dify_batch_id
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
                document.remote_parent_count = 1
                document.status = "succeeded"
                document.stage = "completed"
            session.commit()
            if (
                replaced_document_id
                and replaced_document_id != document_id
            ):
                try:
                    client.delete_document(replaced_document_id)
                except Exception as exc:
                    try:
                        client.disable_document(replaced_document_id)
                    except Exception:
                        session.add(
                            ExternalDeletionTask(
                                kind="dify_document",
                                external_id=replaced_document_id,
                                payload={"dataset_id": dataset_id},
                                attempts=0,
                                last_error=str(exc)[:1000],
                            )
                        )
                        session.commit()
            from .cleaning import _finish_batch, _refresh_file_state

            _refresh_file_state(session, shard.file_id, commit=False)
            _finish_batch(session, shard.file.batch_id)
            session.commit()
        return True
    except Exception as exc:
        logger.exception("Dify publication shard failed")
        if client and created_id:
            try:
                client.delete_document(created_id)
            except Exception:
                logger.exception("failed to compensate Dify shard")
        with SessionLocal() as session:
            shard = session.get(DifyPublicationShard, shard_id)
            if shard:
                shard.status = "shard_pending" if shard.attempts < 2 else "failed"
                shard.last_error = str(exc)[:2000]
                for document in shard.documents:
                    document.status = (
                        "shard_pending" if shard.attempts < 2 else "failed"
                    )
                    document.stage = (
                        "shard_pending" if shard.attempts < 2 else "failed"
                    )
                session.commit()
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
