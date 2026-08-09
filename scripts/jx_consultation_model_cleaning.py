"""Run consultation model cleaning against an existing Markdown directory.

This is an operator-facing, non-publishing workflow.  It deliberately keeps
the model call and the source-Markdown fallback logic outside the database and
Celery workers so an operator can inspect one isolated run before any later
publication step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ai_management.config import get_settings
from ai_management.model_cleaning import (
    BailianClient,
    ModelNoResponseError,
    PromptRequest,
    build_prompt_messages,
    validate_consultation_response,
)
from ai_management.sanitization import sanitize_for_knowledge
from knowledge_cleaner.chunking import DIFY_CHILD_SEPARATOR, DIFY_PARENT_SEPARATOR
from knowledge_cleaner.consultation_v2 import (
    CaseDisposition,
    render_case_markdown,
    validate_case_payload,
    verify_parent_child_blocks,
)
from knowledge_cleaner.markdown_table_cleaner import clean_markdown_tables
from knowledge_cleaner.pdf_convert import scan_residual_pii

STRATEGY_CODE = "personal_business_consultation"
RUN_KIND = "jx_consultation_model_cleaning_run"
RUN_SCHEMA_VERSION = 1
RUN_ID_RE = re.compile(r"^consultation-model-cleaning-\d{8}-\d{6}-[0-9a-f]{8}$")
DEFAULT_MAX_OUTPUT_TOKENS = 500
PRIVATE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


class RunInputError(RuntimeError):
    """The operator supplied an unsafe or incomplete run input."""


class SourceGateError(RuntimeError):
    """The source Markdown is not safe to use as a fallback."""


class ModelReviewRequired(RuntimeError):
    """The model returned a review disposition."""


@dataclass(slots=True)
class SourceItem:
    index: int
    path: Path
    item_id: str
    source_sha256: str
    source_markdown: str = ""
    source_blocks: dict[str, str] = field(default_factory=dict)
    source_chars: int = 0
    table_issues: int = 0
    status: str = "pending"
    selected: str = ""
    error_code: str = ""
    error_message: str = ""
    model_attempts: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_yuan: float = 0.0
    model_payload_path: str = ""
    validated_path: str = ""
    final_path: str = ""
    audit: dict[str, Any] = field(default_factory=dict)


class TraceLogger:
    """Write immediately flushed, safe, run-scoped JSONL trace entries."""

    def __init__(self, path: Path, *, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=PRIVATE_MODE, exist_ok=True)
        _chmod_private(path)

    def emit(
        self,
        *,
        item: SourceItem | None,
        phase: str,
        status: str,
        **details: Any,
    ) -> None:
        payload: dict[str, Any] = {
            "time": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "item": f"{item.index:03d}" if item else "",
            "item_id": item.item_id if item else "",
            "source_sha256": item.source_sha256 if item else "",
            "phase": phase,
            "status": status,
        }
        payload.update(_safe_log_values(details))
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
        print(line, flush=True)


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, PRIVATE_MODE)
    except OSError:
        pass


def _chmod_private_dir(path: Path) -> None:
    try:
        os.chmod(path, PRIVATE_DIR_MODE)
    except OSError:
        pass


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    _chmod_private(temporary)
    temporary.replace(path)
    _chmod_private(path)


def _write_text_atomic(path: Path, content: str) -> None:
    _write_bytes_atomic(path, content.encode("utf-8"))


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _write_text_atomic(path, rendered)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_text(content: str) -> str:
    return _sha256_bytes(content.encode("utf-8"))


def _source_blocks(text: str) -> dict[str, str]:
    paragraphs = [
        value.strip()
        for value in re.split(r"\n\s*\n+", text)
        if value.strip()
    ]
    return {
        f"b{index:04d}": paragraph
        for index, paragraph in enumerate(paragraphs, start=1)
    }


def _safe_log_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Keep trace fields structural; never serialize model/source text."""

    safe: dict[str, Any] = {}
    for key, value in values.items():
        if key in {"text", "content", "prompt", "payload", "exception"}:
            continue
        if isinstance(value, Path):
            safe[key] = str(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, Mapping):
            safe[key] = _safe_log_values(value)
        elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
            entries: list[Any] = []
            for item in value:
                if isinstance(item, Mapping):
                    entries.append(_safe_log_values(item))
                elif isinstance(item, Path):
                    entries.append(str(item))
                elif isinstance(item, (str, int, float, bool)) or item is None:
                    entries.append(item)
                else:
                    entries.append(type(item).__name__)
            safe[key] = entries
        else:
            safe[key] = str(value)
    return safe


def _safe_error(exc: Exception) -> tuple[str, str]:
    """Convert provider/Pydantic errors into stable, non-content diagnostics."""

    if isinstance(exc, ModelNoResponseError):
        return "MODEL_NO_RESPONSE", "模型未返回可用结果"
    errors_method = getattr(exc, "errors", None)
    if callable(errors_method):
        try:
            entries = errors_method()
        except Exception:
            entries = []
        paths: list[str] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            path = ".".join(str(value) for value in entry.get("loc") or ())
            error_type = str(entry.get("type") or "validation_error")
            paths.append(f"{path or 'root'}:{error_type}")
        if paths:
            return "MODEL_CONTRACT_INVALID", ";".join(paths[:8])
    message = str(exc)
    markers = (
        ("完整覆盖", "source_coverage"),
        ("未知来源块", "unknown_source_block"),
        ("数字或日期", "unsupported_source_number"),
        ("否定状态", "negation_state_mismatch"),
        ("人物归属", "person_attribution_mismatch"),
        ("父子块", "dify_parent_child"),
        ("子块数量", "dify_child_count"),
        ("缺少DIFY_CHILD", "dify_child_missing"),
        ("空块", "dify_empty_block"),
        ("案例正文超过", "model_markdown_too_long"),
        ("模型 Markdown 为空", "empty_markdown"),
        ("正文为空", "empty_markdown"),
        ("DIFY", "dify_boundary"),
        ("privacy", "privacy_review"),
    )
    for marker, code in markers:
        if marker.lower() in message.lower():
            return "MODEL_CONTRACT_INVALID", code
    return "MODEL_CONTRACT_INVALID", type(exc).__name__


def _safe_exception_message(exc: Exception) -> str:
    code, detail = _safe_error(exc)
    return f"{code}:{detail}"


def _estimated_tokens(request: PromptRequest) -> int:
    total_chars = len(request.title) + sum(
        len(key) + len(value) for key, value in request.blocks.items()
    )
    return max(1, (total_chars + 1) // 2)


def _estimated_cost_yuan(
    request: PromptRequest,
    *,
    input_price_per_million: float,
    output_price_per_million: float,
) -> float:
    input_tokens = _estimated_tokens(request)
    input_cost = input_tokens / 1_000_000 * input_price_per_million
    output_cost = (
        DEFAULT_MAX_OUTPUT_TOKENS / 1_000_000 * output_price_per_million
    )
    return input_cost + output_cost


def _normalize_markdown(markdown: str) -> tuple[str, int]:
    table_result = clean_markdown_tables(markdown)
    normalized = sanitize_for_knowledge(table_result.markdown)
    return normalized, len(table_result.issues)


def _validate_boundaries(markdown: str) -> None:
    if DIFY_PARENT_SEPARATOR in markdown:
        raise ValueError("正文包含保留的 Dify parent 分隔符")
    if DIFY_CHILD_SEPARATOR in markdown:
        raise ValueError("正文包含保留的 Dify child 分隔符")


def _privacy_categories(markdown: str) -> list[str]:
    findings = scan_residual_pii(markdown)
    return sorted(
        {
            str(getattr(item, "category", "unknown"))
            for item in findings
        }
    )


def _case_title(source_sha256: str) -> str:
    return f"匿名商业咨询案例 case_{source_sha256[:12]}"


def _make_request(item: SourceItem) -> PromptRequest:
    return PromptRequest(
        strategy_code=STRATEGY_CODE,
        item_id=item.item_id,
        content_sha256=item.source_sha256,
        title=_case_title(item.source_sha256),
        blocks=item.source_blocks,
    )


def _validate_source(item: SourceItem, trace: TraceLogger) -> None:
    try:
        raw = item.path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        trace.emit(
            item=item,
            phase="source_read",
            status="FAIL",
            error_code="SOURCE_READ_FAILED",
            detail=type(exc).__name__,
        )
        raise SourceGateError(f"源文件读取失败：{type(exc).__name__}") from exc
    source, table_issue_count = _normalize_markdown(raw)
    item.table_issues = table_issue_count
    item.source_markdown = source
    item.source_chars = len(source)
    if not source.strip():
        raise SourceGateError("源 Markdown 为空")
    trace.emit(
        item=item,
        phase="source_read",
        status="PASS",
        chars=len(source),
        table_issues=table_issue_count,
    )
    categories = _privacy_categories(source)
    if categories:
        trace.emit(
            item=item,
            phase="source_privacy_validation",
            status="FAIL",
            error_code="SOURCE_PRIVACY_REVIEW_REQUIRED",
            categories=categories,
        )
        raise SourceGateError("源 Markdown 包含隐私检测结果")
    trace.emit(
        item=item,
        phase="source_privacy_validation",
        status="PASS",
        categories=[],
    )
    try:
        _validate_boundaries(source)
    except Exception as exc:
        trace.emit(
            item=item,
            phase="source_boundary_validation",
            status="FAIL",
            error_code="SOURCE_DIFY_BOUNDARY_INVALID",
        )
        raise SourceGateError("源 Markdown 包含 Dify 保留分隔符") from exc
    item.source_blocks = _source_blocks(source)
    if not item.source_blocks:
        raise SourceGateError("源 Markdown 未生成来源块")
    trace.emit(
        item=item,
        phase="source_boundary_validation",
        status="PASS",
        block_count=len(item.source_blocks),
    )


def _render_legacy_payload(
    payload: Mapping[str, Any],
    *,
    request: PromptRequest,
) -> str:
    result = validate_consultation_response(
        dict(payload),
        available_block_ids=set(request.blocks),
        source_blocks=request.blocks,
    )
    parts = [
        f"## {section.heading}\n\n{section.text}"
        for section in result.sections
    ]
    return f"# {request.title}\n\n" + "\n\n".join(parts)


def _render_model_payload(
    payload: Mapping[str, Any],
    *,
    request: PromptRequest,
) -> tuple[str, dict[str, Any]]:
    if str(payload.get("schema_version") or "") == "consulting_case_v2":
        case = validate_case_payload(payload, source_blocks=request.blocks)
        if case.disposition == CaseDisposition.EXCLUDE:
            raise ModelReviewRequired("CONSULTATION_EXCLUDED")
        if case.disposition == CaseDisposition.REVIEW:
            raise ModelReviewRequired("MODEL_REVIEW_SKIPPED")
        disposition = getattr(case.disposition, "value", str(case.disposition))
        summary = {
            "schema_version": "consulting_case_v2",
            "disposition": disposition,
            "shared_context_count": len(getattr(case, "shared_context", ()) or ()),
            "objective_count": len(getattr(case, "objectives", ()) or ()),
            "decision_unit_count": len(getattr(case, "decision_units", ()) or ()),
            "boundary_mode": "consultation_v2_parent_child",
        }
        return render_case_markdown(case), summary
    result = validate_consultation_response(
        dict(payload),
        available_block_ids=set(request.blocks),
        source_blocks=request.blocks,
    )
    return _render_legacy_payload(payload, request=request), {
        "schema_version": "legacy_sections",
        "section_count": len(result.sections),
        "fact_states": sorted(
            {
                str(getattr(section.fact_state, "value", section.fact_state))
                for section in result.sections
            }
        ),
    }


def _save_source_snapshot(item: SourceItem, run_dir: Path) -> None:
    path = run_dir / "source" / f"{item.item_id}.md"
    _write_text_atomic(path, item.source_markdown)


def _save_model_payload(
    item: SourceItem,
    run_dir: Path,
    *,
    payload: Mapping[str, Any],
    usage: Mapping[str, int],
    model: str,
    prompt_version: str,
) -> None:
    path = run_dir / "model" / f"{item.item_id}.json"
    _write_json_atomic(
        path,
        {
            "strategy_code": STRATEGY_CODE,
            "model": model,
            "prompt_version": prompt_version,
            "source_sha256": item.source_sha256,
            "usage": dict(usage),
            "payload": dict(payload),
        },
    )
    item.model_payload_path = str(path.relative_to(run_dir))


def _fallback(
    item: SourceItem,
    run_dir: Path,
    trace: TraceLogger,
    *,
    reason_code: str,
    reason_message: str,
) -> None:
    item.error_code = reason_code
    item.error_message = reason_message
    if not item.source_markdown.strip():
        item.status = "failed"
        item.selected = "none"
        trace.emit(
            item=item,
            phase="fallback",
            status="FAIL",
            error_code="MODEL_FALLBACK_SOURCE_EMPTY",
            reason_code=reason_code,
            reason="源 Markdown 为空，不能降级",
        )
        return
    final_path = run_dir / "final" / f"{item.item_id}.md"
    _write_text_atomic(final_path, item.source_markdown)
    item.final_path = str(final_path.relative_to(run_dir))
    item.status = "fallback"
    item.selected = "source_markdown_fallback"
    item.audit.update(
        {
            "source_markdown_fallback": {
                "applied": True,
                "reason_code": reason_code,
                "model_output_ignored": True,
            },
            "review_original_error": {
                "code": reason_code,
                "message": reason_message,
            },
        }
    )
    trace.emit(
        item=item,
        phase="fallback",
        status="PASS",
        reason_code=reason_code,
        reason=reason_message,
        selected=item.selected,
        final_path=item.final_path,
        final_sha256=_sha256_text(item.source_markdown),
    )


def _model_call(
    client: BailianClient,
    request: PromptRequest,
    *,
    model: str,
    max_attempts: int,
    trace: TraceLogger,
    item: SourceItem,
) -> tuple[dict[str, Any], dict[str, int]]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        item.model_attempts = attempt
        trace.emit(
            item=item,
            phase="model_request",
            status="START",
            attempt=attempt,
            model=model,
            estimated_input_tokens=_estimated_tokens(request),
        )
        try:
            payload, usage = client.chat_json(
                build_prompt_messages(request),
                model=model,
            )
            item.usage = {
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            }
            trace.emit(
                item=item,
                phase="model_response",
                status="PASS",
                attempt=attempt,
                input_tokens=item.usage["input_tokens"],
                output_tokens=item.usage["output_tokens"],
            )
            return payload, item.usage
        except ModelNoResponseError as exc:
            last_error = exc
            trace.emit(
                item=item,
                phase="model_response",
                status="FAIL",
                attempt=attempt,
                error_code="MODEL_NO_RESPONSE",
                retry=(attempt < max_attempts),
            )
            if attempt < max_attempts:
                time.sleep(0)
        except Exception as exc:
            code, detail = _safe_error(exc)
            trace.emit(
                item=item,
                phase="model_response",
                status="FAIL",
                attempt=attempt,
                error_code=code,
                detail=detail,
                retry=False,
            )
            raise
    assert last_error is not None
    raise last_error


def _process_item(
    item: SourceItem,
    *,
    run_dir: Path,
    trace: TraceLogger,
    client: BailianClient,
    model: str,
    prompt_version: str,
) -> None:
    trace.emit(item=item, phase="item_start", status="START")
    request = _make_request(item)
    try:
        payload, usage = _model_call(
            client,
            request,
            model=model,
            max_attempts=2,
            trace=trace,
            item=item,
        )
        model_path = run_dir / "model" / f"{item.item_id}.json"
        _save_model_payload(
            item,
            run_dir,
            payload=payload,
            usage=usage,
            model=model,
            prompt_version=prompt_version,
        )
        trace.emit(
            item=item,
            phase="model_result_saved",
            status="PASS",
            model_path=item.model_payload_path,
            result_sha256=_sha256_bytes(model_path.read_bytes()),
        )
    except ModelNoResponseError:
        _fallback(
            item,
            run_dir,
            trace,
            reason_code="MODEL_NO_RESPONSE",
            reason_message="模型两次调用均未返回可用结果",
        )
        trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)
        return
    except Exception as exc:
        code, detail = _safe_error(exc)
        _fallback(
            item,
            run_dir,
            trace,
            reason_code=code,
            reason_message=detail,
        )
        trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)
        return

    try:
        rendered, model_audit = _render_model_payload(payload, request=request)
        trace.emit(
            item=item,
            phase="semantic_validation",
            status="PASS",
            schema_version=str(payload.get("schema_version") or "legacy_sections"),
        )
        item.audit["model_result"] = model_audit
    except ModelReviewRequired as exc:
        reason = str(exc) or "MODEL_REVIEW_SKIPPED"
        reason_code = "CONSULTATION_EXCLUDED" if reason == "CONSULTATION_EXCLUDED" else "MODEL_REVIEW_SKIPPED"
        trace.emit(
            item=item,
            phase="semantic_validation",
            status="PASS",
            disposition="exclude" if reason_code == "CONSULTATION_EXCLUDED" else "review",
        )
        if reason_code == "CONSULTATION_EXCLUDED":
            item.status = "excluded"
            item.selected = "none"
            item.error_code = reason_code
            item.error_message = "模型明确排除该案例"
            trace.emit(
                item=item,
                phase="fallback",
                status="SKIP",
                reason_code=reason_code,
                reason="模型明确 exclude，不执行源 Markdown 降级",
            )
        else:
            _fallback(
                item,
                run_dir,
                trace,
                reason_code=reason_code,
                reason_message="模型判定需要人工复核，按线上规则恢复源 Markdown",
            )
        trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)
        return
    except Exception as exc:
        code, detail = _safe_error(exc)
        trace.emit(
            item=item,
            phase="semantic_validation",
            status="FAIL",
            error_code=code,
            detail=detail,
        )
        _fallback(item, run_dir, trace, reason_code=code, reason_message=detail)
        trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)
        return

    trace.emit(
        item=item,
        phase="model_render",
        status="PASS",
        model_chars=len(rendered),
    )
    categories = _privacy_categories(rendered)
    if categories:
        trace.emit(
            item=item,
            phase="model_privacy_validation",
            status="FAIL",
            error_code="MODEL_PRIVACY_REVIEW_REQUIRED",
            categories=categories,
        )
        _fallback(
            item,
            run_dir,
            trace,
            reason_code="MODEL_PRIVACY_REVIEW_REQUIRED",
            reason_message="模型 Markdown 仍有隐私检测结果",
        )
        trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)
        return
    trace.emit(item=item, phase="model_privacy_validation", status="PASS", categories=[])

    try:
        final_model, table_issue_count = _normalize_markdown(rendered)
        if not final_model.strip():
            raise ValueError("模型 Markdown 为空")
        trace.emit(
            item=item,
            phase="final_markdown_validation",
            status="PASS",
            table_issues=table_issue_count,
            chars=len(final_model),
        )
    except Exception as exc:
        code, detail = _safe_error(exc)
        trace.emit(
            item=item,
            phase="final_markdown_validation",
            status="FAIL",
            error_code=code,
            detail=detail,
        )
        _fallback(item, run_dir, trace, reason_code=code, reason_message=detail)
        trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)
        return

    try:
        model_result = item.audit.get("model_result") or {}
        if model_result.get("schema_version") == "consulting_case_v2":
            boundary = verify_parent_child_blocks(
                final_model,
                expected_children=int(model_result["decision_unit_count"]),
            )
            trace.emit(
                item=item,
                phase="dify_boundary_validation",
                status="PASS",
                boundary_mode="consultation_v2_parent_child",
                child_count=boundary["child_count"],
            )
        else:
            _validate_boundaries(final_model)
            trace.emit(
                item=item,
                phase="dify_boundary_validation",
                status="PASS",
                boundary_mode="reserved_separator_rejection",
            )
    except Exception as exc:
        code, detail = _safe_error(exc)
        trace.emit(
            item=item,
            phase="dify_boundary_validation",
            status="FAIL",
            error_code=code,
            detail=detail,
        )
        _fallback(item, run_dir, trace, reason_code=code, reason_message=detail)
        trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)
        return

    validated_path = run_dir / "validated" / f"{item.item_id}.md"
    final_path = run_dir / "final" / f"{item.item_id}.md"
    _write_text_atomic(validated_path, final_model)
    _write_text_atomic(final_path, final_model)
    item.validated_path = str(validated_path.relative_to(run_dir))
    item.final_path = str(final_path.relative_to(run_dir))
    item.status = "succeeded"
    item.selected = "model"
    item.audit["model_output_sha256"] = _sha256_text(final_model)
    trace.emit(
        item=item,
        phase="final_output_saved",
        status="PASS",
        selected="model",
        validated_path=item.validated_path,
        final_path=item.final_path,
        final_sha256=item.audit["model_output_sha256"],
    )
    trace.emit(item=item, phase="item_complete", status="PASS", selected=item.selected)


def _new_run_id() -> str:
    seed = f"{time.time_ns()}|{os.getpid()}".encode("utf-8")
    return (
        "consultation-model-cleaning-"
        + datetime.now().strftime("%Y%m%d-%H%M%S")
        + "-"
        + hashlib.sha256(seed).hexdigest()[:8]
    )


def _validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise RunInputError("run_id 格式无效")
    return run_id


def _resolve_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _run_dir(output_root: Path, run_id: str) -> Path:
    root = _resolve_path(output_root)
    candidate = (root / "runs" / run_id).resolve(strict=False)
    if candidate.parent != (root / "runs").resolve(strict=False):
        raise RunInputError("运行目录越出 runs 根目录")
    return candidate


def _prepare_run_dir(output_root: Path, run_id: str) -> Path:
    run_dir = _run_dir(output_root, _validate_run_id(run_id))
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    _chmod_private_dir(run_dir.parent)
    if run_dir.exists():
        marker = run_dir / "RUN_MARKER.json"
        if not marker.is_file():
            raise RunInputError("已有运行目录缺少 RUN_MARKER.json，拒绝覆盖")
        try:
            marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RunInputError("已有运行目录的 RUN_MARKER.json 无法读取") from exc
        if (
            marker_payload.get("kind") != RUN_KIND
            or marker_payload.get("run_id") != run_id
        ):
            raise RunInputError("已有运行目录标记不匹配，拒绝覆盖")
        for child in run_dir.iterdir():
            if child.is_symlink():
                raise RunInputError("已有运行目录包含符号链接，拒绝覆盖")
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    run_dir.mkdir(parents=True, exist_ok=True)
    _chmod_private_dir(run_dir)
    for name in ("source", "model", "validated", "final"):
        child = run_dir / name
        child.mkdir(parents=True, exist_ok=True)
        _chmod_private_dir(child)
    return run_dir


def _discover_items(input_dir: Path) -> list[SourceItem]:
    directory = _resolve_path(input_dir)
    if not directory.is_dir() or directory.is_symlink():
        raise RunInputError("input-dir 必须是非符号链接目录")
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.suffix.lower() == ".md" and path.is_file() and not path.is_symlink()
    )
    if not paths:
        raise RunInputError("input-dir 下没有直接 Markdown 文件")
    items: list[SourceItem] = []
    for index, path in enumerate(paths, start=1):
        raw = path.read_bytes()
        source_sha = _sha256_bytes(raw)
        items.append(
            SourceItem(
                index=index,
                path=path,
                item_id=f"item-{index:03d}-{source_sha[:12]}",
                source_sha256=source_sha,
            )
        )
    return items


def _manifest_item(item: SourceItem) -> dict[str, Any]:
    return {
        "item": f"{item.index:03d}",
        "item_id": item.item_id,
        "source_sha256": item.source_sha256,
        "source_chars": item.source_chars,
        "source_block_count": len(item.source_blocks),
        "table_issues": item.table_issues,
        "status": item.status,
        "selected": item.selected,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "model_attempts": item.model_attempts,
        "usage": item.usage,
        "estimated_cost_yuan": round(item.estimated_cost_yuan, 8),
        "model_payload_path": item.model_payload_path,
        "validated_path": item.validated_path,
        "final_path": item.final_path,
        "audit": item.audit,
    }


def _write_manifest(
    run_dir: Path,
    *,
    run_id: str,
    input_dir: Path,
    model: str,
    prompt_version: str,
    max_cost_yuan: float,
    estimated_cost_yuan: float,
    items: Sequence[SourceItem],
    status: str,
) -> None:
    _write_json_atomic(
        run_dir / "manifest.json",
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "kind": RUN_KIND,
            "run_id": run_id,
            "status": status,
            "input_dir_sha256": _sha256_text(str(_resolve_path(input_dir))),
            "model": model,
            "prompt_version": prompt_version,
            "max_cost_yuan": max_cost_yuan,
            "estimated_cost_yuan": round(estimated_cost_yuan, 8),
            "items": [_manifest_item(item) for item in items],
        },
    )


def _write_input_map(run_dir: Path, items: Sequence[SourceItem], input_dir: Path) -> None:
    """Keep filename mapping private so safe logs/manifests stay redacted."""

    _write_json_atomic(
        run_dir / "input-map.json",
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "kind": f"{RUN_KIND}_private_input_map",
            "items": [
                {
                    "item": f"{item.index:03d}",
                    "item_id": item.item_id,
                    "source_sha256": item.source_sha256,
                    "input_name": str(item.path.relative_to(_resolve_path(input_dir))),
                }
                for item in items
            ],
        },
    )


def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    input_dir = _resolve_path(args.input_dir)
    output_root = _resolve_path(args.output_root)
    if output_root == input_dir or input_dir in output_root.parents:
        raise RunInputError("output-root 不能位于 input-dir 内，避免把运行产物再次当作输入")
    items = _discover_items(input_dir)
    run_id = _validate_run_id(args.run_id or _new_run_id())
    run_dir = _prepare_run_dir(output_root, run_id)
    trace = TraceLogger(run_dir / "run.log", run_id=run_id)
    model = settings.cleaning_model
    prompt_version = settings.cleaning_prompt_version
    max_cost = (
        float(args.max_cost_yuan)
        if args.max_cost_yuan is not None
        else float(settings.acceptance_cleaning_budget_max_yuan)
    )
    if max_cost < 0:
        raise RunInputError("max-cost-yuan 必须是非负数字")
    _write_json_atomic(
        run_dir / "RUN_MARKER.json",
        {
            "kind": RUN_KIND,
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cleanup_scope": "this_run_directory_only",
        },
    )
    _write_input_map(run_dir, items, input_dir)
    _write_manifest(
        run_dir,
        run_id=run_id,
        input_dir=input_dir,
        model=model,
        prompt_version=prompt_version,
        max_cost_yuan=max_cost,
        estimated_cost_yuan=0,
        items=items,
        status="running",
    )
    trace.emit(
        item=None,
        phase="run_start",
        status="PASS",
        input_count=len(items),
        model=model,
        prompt_version=prompt_version,
        max_cost_yuan=max_cost,
        run_dir=str(run_dir),
    )

    ready_items: list[SourceItem] = []
    estimated_total = 0.0
    for item in items:
        try:
            _validate_source(item, trace)
            _save_source_snapshot(item, run_dir)
            request = _make_request(item)
            item.estimated_cost_yuan = _estimated_cost_yuan(
                request,
                input_price_per_million=settings.cleaning_realtime_input_price_per_million,
                output_price_per_million=settings.cleaning_realtime_output_price_per_million,
            )
            if _estimated_tokens(request) > settings.cleaning_candidate_max_tokens:
                _fallback(
                    item,
                    run_dir,
                    trace,
                    reason_code="MODEL_INPUT_TOO_LARGE",
                    reason_message="单候选超过模型输入上限，未发起模型调用",
                )
                continue
            ready_items.append(item)
            estimated_total += item.estimated_cost_yuan
            trace.emit(
                item=item,
                phase="cost_preflight",
                status="PASS",
                estimated_input_tokens=_estimated_tokens(request),
                estimated_cost_yuan=round(item.estimated_cost_yuan, 8),
            )
        except SourceGateError as exc:
            item.status = "failed"
            item.selected = "none"
            item.error_code = "SOURCE_GATE_FAILED"
            item.error_message = str(exc)
            trace.emit(
                item=item,
                phase="cost_preflight",
                status="FAIL",
                error_code=item.error_code,
                reason="源 Markdown 未通过硬门禁",
            )

    _write_manifest(
        run_dir,
        run_id=run_id,
        input_dir=input_dir,
        model=model,
        prompt_version=prompt_version,
        max_cost_yuan=max_cost,
        estimated_cost_yuan=estimated_total,
        items=items,
        status="preflight",
    )
    trace.emit(
        item=None,
        phase="cost_preflight_total",
        status="PASS" if estimated_total <= max_cost else "FAIL",
        candidate_count=len(ready_items),
        estimated_cost_yuan=round(estimated_total, 8),
        max_cost_yuan=max_cost,
    )
    if estimated_total > max_cost:
        for item in ready_items:
            _fallback(
                item,
                run_dir,
                trace,
                reason_code="MODEL_RUN_BUDGET_EXCEEDED",
                reason_message="本次运行预估费用超过 max-cost-yuan，未发起模型调用",
            )
    else:
        try:
            client = BailianClient(
                api_key=settings.bailian_api_key,
                base_url=settings.bailian_base_url,
                timeout_seconds=180,
            )
        except Exception as exc:
            detail = type(exc).__name__
            trace.emit(
                item=None,
                phase="model_client_init",
                status="FAIL",
                error_code="MODEL_CLIENT_UNAVAILABLE",
                detail=detail,
            )
            for item in ready_items:
                _fallback(
                    item,
                    run_dir,
                    trace,
                    reason_code="MODEL_CLIENT_UNAVAILABLE",
                    reason_message="模型客户端初始化失败，按线上规则恢复源 Markdown",
                )
        else:
            try:
                for item in ready_items:
                    if item.status != "pending":
                        continue
                    _process_item(
                        item,
                        run_dir=run_dir,
                        trace=trace,
                        client=client,
                        model=model,
                        prompt_version=prompt_version,
                    )
                    _write_manifest(
                        run_dir,
                        run_id=run_id,
                        input_dir=input_dir,
                        model=model,
                        prompt_version=prompt_version,
                        max_cost_yuan=max_cost,
                        estimated_cost_yuan=estimated_total,
                        items=items,
                        status="running",
                    )
            finally:
                client.close()

    final_status = "completed" if all(item.status != "failed" for item in items) else "failed"
    _write_manifest(
        run_dir,
        run_id=run_id,
        input_dir=input_dir,
        model=model,
        prompt_version=prompt_version,
        max_cost_yuan=max_cost,
        estimated_cost_yuan=estimated_total,
        items=items,
        status=final_status,
    )
    trace.emit(
        item=None,
        phase="run_complete",
        status="PASS" if final_status == "completed" else "FAIL",
        run_status=final_status,
        succeeded=sum(item.status == "succeeded" for item in items),
        fallback=sum(item.status == "fallback" for item in items),
        excluded=sum(item.status == "excluded" for item in items),
        failed=sum(item.status == "failed" for item in items),
        manifest_path=str(run_dir / "manifest.json"),
        log_path=str(run_dir / "run.log"),
    )
    return 0 if final_status == "completed" else 1


def _load_marker(run_dir: Path, run_id: str) -> dict[str, Any]:
    marker = run_dir / "RUN_MARKER.json"
    if not marker.is_file():
        raise RunInputError("运行目录缺少 RUN_MARKER.json")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if payload.get("kind") != RUN_KIND or payload.get("run_id") != run_id:
        raise RunInputError("运行标记不匹配")
    return payload


def _list_runs(args: argparse.Namespace) -> int:
    root = _resolve_path(args.output_root) / "runs"
    entries: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if not path.is_dir() or not RUN_ID_RE.fullmatch(path.name):
                continue
            try:
                marker = _load_marker(path, path.name)
            except Exception:
                continue
            entries.append(
                {
                    "run_id": path.name,
                    "created_at": marker.get("created_at", ""),
                    "manifest": str(path / "manifest.json"),
                    "log": str(path / "run.log"),
                }
            )
    print(json.dumps(entries, ensure_ascii=False, indent=2))
    return 0


def _cleanup_run(args: argparse.Namespace) -> int:
    run_id = _validate_run_id(args.run_id)
    run_dir = _run_dir(args.output_root, run_id)
    _load_marker(run_dir, run_id)
    expected = f"DELETE_{run_id}"
    if args.confirm != expected:
        raise RunInputError(f"需要确认参数：{expected}")
    shutil.rmtree(run_dir)
    print(json.dumps({"status": "deleted", "run_id": run_id}, ensure_ascii=False))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="jx 个人商业咨询 Markdown 模型清洗与降级脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--input-dir", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--run-id", default="")
    run.add_argument("--max-cost-yuan", type=float, default=None)
    listed = subparsers.add_parser("list-runs")
    listed.add_argument("--output-root", type=Path, required=True)
    cleanup = subparsers.add_parser("cleanup-run")
    cleanup.add_argument("--output-root", type=Path, required=True)
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "list-runs":
            return _list_runs(args)
        return _cleanup_run(args)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error": _safe_exception_message(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
