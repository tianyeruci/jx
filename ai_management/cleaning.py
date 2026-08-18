from __future__ import annotations

import codecs
import hashlib
import json
import logging
import re
import shutil
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePath
from typing import Any, Iterable, Protocol

from docx import Document
from docx.oxml.ns import qn
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from knowledge_cleaner.chunking import (
    DIFY_CHILD_SEPARATOR,
    DIFY_PARENT_SEPARATOR,
)
from knowledge_cleaner.markdown_table_cleaner import clean_markdown_tables
from knowledge_cleaner.semantics import SemanticDecision
from knowledge_cleaner.sources import (
    parse_markdown_posts_text,
    parse_wechat_public_account,
)

from .activity_cleaning import ActivityCleaningStrategy
from .cache import cache
from .config import get_settings
from .db import SessionLocal, get_session
from .errors import ServiceError
from .models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    ModelCleaningTask,
    utcnow,
)
from .privacy import (
    consultation_aliases,
    discover_author_aliases,
    redact_article_identity,
)
from .security import require_admin_token, require_internal_token
from .services.observability import record_event

router = APIRouter(
    prefix="/internal/v1/knowledge",
    tags=["knowledge-cleaning"],
    dependencies=[Depends(require_internal_token), Depends(require_admin_token)],
)
logger = logging.getLogger(__name__)

CONTENT_MODEL_STRATEGIES = frozenset(
    {
        "personal_business_consultation",
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
    }
)


def _review_force_strategy_codes(value: str | None) -> frozenset[str]:
    return frozenset(
        item.strip()
        for item in str(value or "").split(",")
        if item.strip()
    )


def review_policy_snapshot(strategy_code: str) -> dict[str, Any]:
    """Return the server-side review policy captured by a new batch."""

    settings = get_settings()
    enabled = strategy_code in CONTENT_MODEL_STRATEGIES
    return {
        "enabled": enabled,
        "review_required_enabled": settings.cleaning_review_required_enabled,
        # New batches may publish any non-empty cleaned Markdown directly.
        # The catalogue remains explicitly excluded because its model output
        # changes authoritative course/route records rather than just text.
        "source_markdown_fallback_enabled": strategy_code != "course_route_catalog",
        "min_chars": settings.cleaning_review_min_chars if enabled else 0,
        "force_review": enabled
        and strategy_code in _review_force_strategy_codes(
            settings.cleaning_review_force_strategies
        ),
    }


def content_review_bypass_enabled(
    strategy_code: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Whether a content-model batch may continue past a review gate."""

    if strategy_code not in CONTENT_MODEL_STRATEGIES:
        return False
    policy = (metadata or {}).get("_cleaning_review_policy") or review_policy_snapshot(
        strategy_code
    )
    # Missing policy fields are fail-closed for batches created before this
    # switch existed.
    return policy.get("review_required_enabled", True) is False


def source_markdown_fallback_enabled(
    strategy_code: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Whether a new batch may publish its non-empty cleaned Markdown source."""

    if strategy_code == "course_route_catalog":
        return False
    policy = (metadata or {}).get("_cleaning_review_policy")
    if not isinstance(policy, dict):
        # Historical rows predate this policy and must not be silently
        # reinterpreted by a newer worker.
        return False
    return policy.get("source_markdown_fallback_enabled") is True


def preserve_review_reason(
    audit: dict[str, Any] | None,
    error_code: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    """Keep the original gate reason in internal audit only."""

    result = dict(audit or {})
    if error_code or error_message:
        result["review_original_error"] = {
            "code": error_code or "REVIEW_REQUIRED",
            "message": str(error_message or "需要人工复核")[:500],
        }
    return result


def content_char_count(markdown: str) -> int:
    """Count source characters without whitespace or Markdown decoration."""

    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", markdown or "")
    value = re.sub(r"[*_~`]", "", value)
    return len(re.sub(r"\s+", "", value))


def content_review_reason(
    markdown: str,
    strategy_code: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Return a configured human-review reason for a content-model item."""

    if strategy_code not in CONTENT_MODEL_STRATEGIES:
        return None
    policy = (metadata or {}).get("_cleaning_review_policy") or review_policy_snapshot(
        strategy_code
    )
    if not isinstance(policy, dict) or not policy.get("enabled", True):
        return None
    if policy.get("review_required_enabled", True) is False or policy.get(
        "source_markdown_fallback_enabled"
    ) is True:
        return None
    if bool(policy.get("force_review")):
        return "CONFIG_REVIEW_REQUIRED", "服务端清洗策略配置要求人工复核"
    minimum = max(0, int(policy.get("min_chars") or 0))
    if minimum and content_char_count(markdown) < minimum:
        return "CONTENT_TOO_SHORT", f"最终正文少于 {minimum} 字"
    return None


def _cleaning_queue_for_filename(filename: str) -> str:
    """Keep PDF conversion isolated from the general cleaning worker."""

    return "ai_ocr" if Path(filename).suffix.lower() == ".pdf" else "ai_cleaning"


def _enqueue_cleaning_file(file_id: str, filename: str) -> None:
    queue = _cleaning_queue_for_filename(filename)
    if queue == "ai_ocr":
        from .workers.tasks.ocr import process_ocr_cleaning_file

        process_ocr_cleaning_file.apply_async(args=[file_id], queue=queue)
        return
    from .tasks import process_cleaning_file

    process_cleaning_file.apply_async(args=[file_id], queue=queue)


def _enqueue_cleaning_document(item_id: str, filename: str) -> None:
    from .tasks import process_cleaning_document

    process_cleaning_document.apply_async(
        args=[item_id], queue=_cleaning_queue_for_filename(filename)
    )


@dataclass(slots=True)
class ChunkingConfig:
    doc_form: str = "hierarchical_model"
    process_mode: str = "hierarchical"
    parent_mode: str = "paragraph"
    parent_separator: str | None = DIFY_PARENT_SEPARATOR
    parent_max_tokens: int = 1000
    child_separator: str | None = DIFY_CHILD_SEPARATOR
    child_max_tokens: int = 350
    parent_chunk_overlap: int = 0
    child_chunk_overlap: int = 0


@dataclass(slots=True)
class CleanedDocument:
    markdown: str
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)
    chunking: ChunkingConfig | None = None
    disposition: str = "pending"
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)

    @property
    def parent_separator(self) -> str:
        return (self.chunking or ChunkingConfig()).parent_separator or "\n\n"

    @property
    def parent_max_tokens(self) -> int:
        return (self.chunking or ChunkingConfig()).parent_max_tokens

    @property
    def child_separator(self) -> str:
        return (self.chunking or ChunkingConfig()).child_separator or "\n\n"

    @property
    def child_max_tokens(self) -> int:
        return (self.chunking or ChunkingConfig()).child_max_tokens


class CleaningStrategy(Protocol):
    code: str
    display_name: str
    supported_extensions: set[str]
    sensitive: bool

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]: ...


def _docx_markdown(path: Path) -> str:
    document = Document(path)
    paragraphs = {id(item._p): item for item in document.paragraphs}
    tables = {id(item._tbl): item for item in document.tables}
    parts: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = paragraphs.get(id(child))
            if paragraph is None:
                continue
            text = paragraph.text.strip()
            if not text:
                continue
            style = paragraph.style.name.lower() if paragraph.style else ""
            prefix = ""
            if "heading" in style:
                match = re.search(r"(\d+)", style)
                level = min(int(match.group(1)), 6) if match else 2
                parts.append(f"{'#' * level} {text}")
                continue
            num_pr = paragraph._p.find(qn("w:pPr"))
            num_pr = num_pr.find(qn("w:numPr")) if num_pr is not None else None
            if num_pr is not None:
                ilvl = num_pr.find(qn("w:ilvl"))
                level = int(ilvl.get(qn("w:val"), "0")) if ilvl is not None else 0
                prefix = "  " * max(0, min(level, 8)) + "- "
            else:
                indentation = paragraph.paragraph_format.left_indent
                if indentation is not None and indentation.pt:
                    prefix = "  " * max(0, min(round(indentation.pt / 18), 8))
            parts.append(f"{prefix}{text}")
        elif child.tag == qn("w:tbl"):
            table = tables.get(id(child))
            if table is None:
                continue
            rows = [[cell.text.strip().replace("|", "\\|") for cell in row.cells] for row in table.rows]
            if not rows:
                continue
            width = max(len(row) for row in rows)
            rows = [row + [""] * (width - len(row)) for row in rows]
            parts.append("| " + " | ".join(rows[0]) + " |")
            parts.append("| " + " | ".join(["---"] * width) + " |")
            parts.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    # Some malformed DOCX files expose a body child that python-docx cannot
    # map.  Preserve the old fallback rather than silently dropping text.
    if not parts:
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def _apply_markdown_table_cleaning(
    markdown: str,
    *,
    audit: dict[str, Any] | None = None,
) -> str:
    result = clean_markdown_tables(markdown)
    if result.tables_seen:
        if audit is not None:
            audit["markdown_table_cleaning"] = result.as_dict()
        source_id = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]
        for issue in result.issues:
            logger.warning(
                "Markdown表格清洗异常 source_id=%s table=%s action=%s reason=%s rows_skipped=%s",
                source_id,
                issue.table_index,
                issue.action,
                issue.reason,
                issue.rows_skipped,
            )
    return result.markdown


def user_visible_warnings(values: Iterable[Any]) -> list[str]:
    """Filter conversion/table audit details out of user-facing warnings."""

    return [str(value) for value in values if not _is_pdf_table_warning(value)]


def finalize_knowledge_markdown(
    markdown: str,
    *,
    audit: dict[str, Any] | None = None,
) -> str:
    """Apply the final no-table invariant before local or remote publication."""

    table_audit: dict[str, Any] = {}
    cleaned = _apply_markdown_table_cleaning(markdown, audit=table_audit)
    from .sanitization import sanitize_for_knowledge

    cleaned = sanitize_for_knowledge(cleaned)
    if audit is not None and table_audit:
        audit["final_markdown_table_cleaning"] = table_audit.get(
            "markdown_table_cleaning", table_audit
        )
    return cleaned


def validate_chunking_boundaries(markdown: str, chunking: ChunkingConfig) -> None:
    """Reject source text that could forge Dify parent/child boundaries."""

    for label, separator in (
        ("parent", chunking.parent_separator),
        ("child", chunking.child_separator),
    ):
        if separator and separator in markdown:
            raise ValueError(f"正文包含保留的 Dify {label} 分隔符")


def _read_text_markdown(
    path: Path,
    suffix: str,
    *,
    table_audit: dict[str, Any] | None = None,
) -> str:
    if suffix == ".docx":
        markdown = _docx_markdown(path)
    else:
        encoding = "gb18030" if suffix == ".txt" else "utf-8-sig"
        markdown = path.read_text(encoding=encoding)
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    if table_audit is not None:
        markdown = _apply_markdown_table_cleaning(markdown, audit=table_audit)
    return markdown


def _conversion_audit(result: Any) -> dict[str, Any]:
    if callable(getattr(result, "to_dict", None)):
        return dict(result.to_dict(include_page_markdown=False))
    return {
        key: getattr(result, key)
        for key in (
            "document_id",
            "source_checksum",
            "backend",
            "status",
            "needs_review",
            "review_reasons",
            "metadata",
            "route",
        )
        if hasattr(result, key)
    }


_PDF_HARD_BLOCK_PREFIXES = (
    "PDF读取失败",
    "PDF_TYPE_REVIEW_REQUIRED",
    "PDF_CONSULTATION_IMAGE_PRIVACY_REVIEW",
    "OCR_PAGE_LIMIT_REVIEW",
    "PDF缺少可用文字层",
    "OCR执行失败",
    "OCR_KEY_FACT_CONFLICT",
    "未生成可入库正文",
    "脱敏后仍检测到",
    "OCR离线模型",
    "不支持的OCR后端",
)


def _pdf_blocking_reasons(reasons: Iterable[Any], markdown: str) -> list[str]:
    values = [str(reason) for reason in reasons]
    blocking = [
        reason
        for reason in values
        if reason.startswith(_PDF_HARD_BLOCK_PREFIXES)
        and not any(
            marker in reason.lower()
            for marker in ("table", "表格", "版面", "格式", "低置信", "confidence")
        )
    ]
    if not markdown.strip() and "未生成可入库正文" not in blocking:
        blocking.append("未生成可入库正文")
    return list(dict.fromkeys(blocking))


def _is_pdf_table_warning(value: Any) -> bool:
    text = str(value)
    return (
        text.startswith("table_review:")
        or text.startswith("table_")
        or "表格" in text
    )


def _read_pdf_markdown(path: Path, *, diagnostics: dict[str, Any] | None = None) -> str:
    from knowledge_cleaner.pdf_convert import convert_pdf

    settings = get_settings()
    result = convert_pdf(
        path,
        enable_ocr=settings.pdf_ocr_enabled,
        ocr_backend_name=settings.pdf_ocr_backend,
        ocr_max_pages=settings.pdf_ocr_max_pages,
        ocr_render_dpi=settings.pdf_ocr_render_dpi,
        ocr_retry_dpi=settings.pdf_ocr_retry_dpi,
        ocr_auto_confidence=settings.pdf_ocr_auto_confidence,
        ocr_min_confidence=settings.pdf_ocr_min_confidence,
        ocr_max_low_conf_ratio=settings.pdf_ocr_max_low_conf_ratio,
        ocr_cpu_threads=settings.pdf_ocr_cpu_threads,
        ocr_model_root=settings.pdf_ocr_model_root,
        ocr_model_manifest=settings.pdf_ocr_model_manifest,
    )
    if diagnostics is not None:
        diagnostics.update(_conversion_audit(result))
    blocking_reasons = _pdf_blocking_reasons(result.review_reasons, result.markdown)
    if diagnostics is not None:
        diagnostics["blocking_reasons"] = blocking_reasons
    source_id = str(getattr(result, "source_checksum", "") or "")[:16]
    if not source_id:
        source_id = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    for warning in result.review_reasons:
        if _is_pdf_table_warning(warning):
            logger.warning(
                "PDF表格结构不可靠，已线性化并继续处理 source_id=%s warning=%s",
                source_id,
                warning,
            )
    if blocking_reasons or not result.markdown.strip():
        reason = "；".join(blocking_reasons[:3]) or "没有可提取正文"
        raise ValueError(f"PDF不支持清洗入库：{reason}")
    return result.markdown.strip()


def _conversion_artifact_component(value: str, *, max_bytes: int) -> str:
    cleaned = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff._ -]",
        "_",
        normalized_original_filename(value),
    )
    result: list[str] = []
    size = 0
    for char in cleaned:
        encoded_size = len(char.encode("utf-8"))
        if size + encoded_size > max_bytes:
            break
        result.append(char)
        size += encoded_size
    return "".join(result).strip(" .") or "未命名文件"


def _conversion_artifact_root() -> Path:
    settings = get_settings()
    root = settings.conversion_markdown_root.resolve()
    if root.name != "conversion-markdown":
        raise ValueError("CONVERSION_MARKDOWN_ROOT 必须是名为 conversion-markdown 的专用目录")
    return root


def _save_conversion_markdown(
    path: Path,
    original_filename: str,
    markdown: str,
) -> Path | None:
    settings = get_settings()
    if (
        not settings.save_conversion_markdown
        or path.suffix.lower() not in {".pdf", ".docx"}
    ):
        return None
    root = _conversion_artifact_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    batch_id = _conversion_artifact_component(path.parent.name, max_bytes=64)
    file_id = _conversion_artifact_component(path.stem, max_bytes=64)
    safe_name = _conversion_artifact_component(original_filename, max_bytes=96)
    target = root / f"{batch_id}__{file_id}__{safe_name}.md"
    temporary = root / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(markdown, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    logger.info("saved conversion markdown artifact source=%s artifact=%s", path.name, target.name)
    return target


def maintain_conversion_markdown_artifacts(
    *,
    delete_all: bool | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete disabled or expired conversion artifacts from the dedicated root."""

    settings = get_settings()
    root = _conversion_artifact_root()
    if not root.exists():
        return {"removed": 0, "retained": 0, "skipped_directories": 0}
    if not root.is_dir():
        raise ValueError("CONVERSION_MARKDOWN_ROOT 不是目录")
    remove_everything = (
        not settings.save_conversion_markdown if delete_all is None else delete_all
    )
    current = now or datetime.now(timezone.utc)
    cutoff = current.timestamp() - (
        settings.conversion_markdown_retention_days * 24 * 60 * 60
    )
    removed = 0
    retained = 0
    skipped_directories = 0
    for child in root.iterdir():
        if child.is_symlink():
            child.unlink(missing_ok=True)
            removed += 1
            continue
        if child.is_dir():
            skipped_directories += 1
            continue
        expired = child.stat().st_mtime <= cutoff
        managed = child.suffix == ".md" or child.name.endswith(".tmp")
        if remove_everything or (managed and expired):
            child.unlink(missing_ok=True)
            removed += 1
        else:
            retained += 1
    return {
        "removed": removed,
        "retained": retained,
        "skipped_directories": skipped_directories,
    }


def _content_title(markdown: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()[:300]
    for line in markdown.splitlines():
        candidate = line.strip().strip("|").strip()
        if candidate:
            return candidate[:80]
    return "未命名文档"


def _load_markdown_like(
    path: Path,
    original_filename: str,
    *,
    diagnostics: dict[str, Any] | None = None,
    clean_tables: bool = True,
) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        markdown = _read_pdf_markdown(path, diagnostics=diagnostics)
    else:
        markdown = _read_text_markdown(path, suffix)
    if clean_tables:
        table_audit: dict[str, Any] = {}
        markdown = _apply_markdown_table_cleaning(markdown, audit=table_audit)
        if diagnostics is not None and table_audit:
            diagnostics.update(table_audit)
    _save_conversion_markdown(path, original_filename, markdown)
    # The original filename is administrative provenance only. It must not
    # change the semantic title, model prompt or content identity.
    title = _content_title(markdown)
    if not markdown.lstrip().startswith("#"):
        markdown = f"# {title}\n\n{markdown}"
    return title, markdown


class TestDocumentStrategy:
    code = "test_markdown"
    display_name = "通用测试文档"
    supported_extensions = {".md", ".markdown", ".txt", ".docx"}
    sensitive = False
    dify_metadata_fields = frozenset({"document_type", "review_status"})

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        conversion: dict[str, Any] = {}
        title, markdown = _load_markdown_like(
            file_path, original_filename, diagnostics=conversion
        )
        if not markdown.strip():
            raise ValueError("文档没有可入库正文")
        return [
            CleanedDocument(
                markdown=markdown,
                title=title,
                metadata={"document_type": "test_document", "review_status": "approved"},
                audit={"pdf_conversion": conversion} if conversion else {},
            )
        ]


class StandardDocumentStrategy:
    """Safe general-purpose cleaner for the two fixed knowledge bases."""

    code = "standard_document"
    display_name = "标准文档清洗"
    supported_extensions = {".md", ".markdown", ".txt", ".docx", ".pdf"}
    sensitive = False
    dify_metadata_fields = frozenset({"document_type", "review_status"})

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        conversion: dict[str, Any] = {}
        title, markdown = _load_markdown_like(
            file_path, original_filename, diagnostics=conversion
        )
        if not markdown.strip():
            raise ValueError("文档没有可入库正文")
        return [
            CleanedDocument(
                markdown=markdown,
                title=title,
                metadata={"document_type": "standard_document", "review_status": "approved"},
                audit={"pdf_conversion": conversion} if conversion else {},
            )
        ]


class PersonalBusinessConsultationStrategy:
    code = "personal_business_consultation"
    display_name = "个人商业咨询案例总结"
    # Markdown is the canonical source produced by the PDF conversion/sample
    # workflow; DOCX/PDF remain supported for existing uploads.
    supported_extensions = {".md", ".markdown", ".docx", ".pdf"}
    sensitive = True
    knowledge_base_code = "general"
    dify_metadata_fields = frozenset({"record_type", "topic", "source_title", "case_id"})

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from knowledge_cleaner.pdf_convert import (
            anonymize_consultation,
            convert_consultation_pdf,
            scan_residual_pii,
        )

        suffix = file_path.suffix.lower()
        aliases: dict[str, str]
        conversion_audit: dict[str, Any] = {}
        if suffix == ".docx":
            raw_markdown = _docx_markdown(file_path)
            table_audit: dict[str, Any] = {}
            cleaned_markdown = _apply_markdown_table_cleaning(
                raw_markdown,
                audit=table_audit,
            )
            _save_conversion_markdown(file_path, original_filename, cleaned_markdown)
            conversion_audit.update(table_audit)
            aliases = consultation_aliases(raw_markdown, original_filename)
            markdown, redactions = anonymize_consultation(
                cleaned_markdown,
                extra_entities=aliases,
            )
            residual_pii = scan_residual_pii(markdown)
            source_checksum = hashlib.sha256(file_path.read_bytes()).hexdigest()
            document_id = f"case_{source_checksum[:16]}"
            review_reasons = []
            if not markdown.strip():
                review_reasons.append("未生成可入库正文")
            if residual_pii:
                review_reasons.append(
                    f"脱敏后仍检测到{len(residual_pii)}处潜在PII，禁止直接发布"
                )
            needs_review = bool(review_reasons)
        elif suffix == ".pdf":
            aliases = consultation_aliases("", original_filename)
            settings = get_settings()
            result = convert_consultation_pdf(
                file_path,
                enable_ocr=settings.pdf_ocr_enabled,
                ocr_backend_name=settings.pdf_ocr_backend,
                ocr_max_pages=settings.pdf_ocr_max_pages,
                ocr_render_dpi=settings.pdf_ocr_render_dpi,
                ocr_retry_dpi=settings.pdf_ocr_retry_dpi,
                ocr_auto_confidence=settings.pdf_ocr_auto_confidence,
                ocr_min_confidence=settings.pdf_ocr_min_confidence,
                ocr_max_low_conf_ratio=settings.pdf_ocr_max_low_conf_ratio,
                ocr_cpu_threads=settings.pdf_ocr_cpu_threads,
                ocr_model_root=settings.pdf_ocr_model_root,
                ocr_model_manifest=settings.pdf_ocr_model_manifest,
                extra_entities=aliases,
            )
            conversion_audit = _conversion_audit(result)
            markdown = str(result.markdown or "")
            table_audit = {}
            markdown = _apply_markdown_table_cleaning(markdown, audit=table_audit)
            conversion_audit.update(table_audit)
            _save_conversion_markdown(file_path, original_filename, markdown)
            redactions = list(getattr(result, "redactions", []) or [])
            residual_pii = list(getattr(result, "residual_pii", []) or [])
            residual_pii.extend(scan_residual_pii(markdown))
            source_checksum = str(
                getattr(result, "source_checksum", "")
                or hashlib.sha256(file_path.read_bytes()).hexdigest()
            )
            document_id = str(
                getattr(result, "document_id", "")
                or f"case_{source_checksum[:16]}"
            )
            review_reasons = list(getattr(result, "review_reasons", []) or [])
            for warning in review_reasons:
                if _is_pdf_table_warning(warning):
                    logger.warning(
                        "PDF表格结构不可靠，已线性化并继续处理 source_id=%s warning=%s",
                        source_checksum[:16],
                        warning,
                    )
            blocking_reasons = _pdf_blocking_reasons(review_reasons, markdown)
            conversion_audit["blocking_reasons"] = blocking_reasons
            needs_review = bool(
                blocking_reasons or residual_pii or not markdown.strip()
            )
        elif suffix in {".md", ".markdown"}:
            conversion_audit = {}
            table_audit: dict[str, Any] = {}
            raw_markdown = _read_text_markdown(
                file_path,
                suffix,
                table_audit=table_audit,
            )
            cleaned_markdown = _apply_markdown_table_cleaning(
                raw_markdown,
                audit=table_audit,
            )
            aliases = consultation_aliases(raw_markdown, original_filename)
            markdown, redactions = anonymize_consultation(
                cleaned_markdown,
                extra_entities=aliases,
            )
            residual_pii = scan_residual_pii(markdown)
            source_checksum = hashlib.sha256(file_path.read_bytes()).hexdigest()
            document_id = f"case_{source_checksum[:16]}"
            review_reasons = []
            if not markdown.strip():
                review_reasons.append("未生成可入库正文")
            if residual_pii:
                review_reasons.append(
                    f"脱敏后仍检测到{len(residual_pii)}处潜在PII，禁止直接发布"
                )
            needs_review = bool(review_reasons)
        else:
            raise ValueError(f"个人商业咨询不支持 {suffix} 文件")

        disposition = "review_required" if needs_review else "awaiting_model"
        safe_markdown = markdown.strip()
        if not safe_markdown:
            safe_markdown = "# 匿名商业咨询案例\n\n内容需人工复核后发布。"
        redaction_summary = {
            str(getattr(item, "category", "unknown")): int(
                getattr(item, "count", 0)
            )
            for item in redactions
        }
        return [
            CleanedDocument(
                markdown=safe_markdown,
                title=f"匿名商业咨询案例 {document_id[:12]}",
                metadata={
                    "record_type": "consulting_case",
                    "source_title": f"匿名商业咨询案例 {document_id[:12]}",
                    "case_id": document_id[:16],
                },
                chunking=ChunkingConfig(
                    doc_form="hierarchical_model",
                    process_mode="hierarchical",
                    parent_mode="full-doc",
                    parent_separator=DIFY_PARENT_SEPARATOR,
                    parent_max_tokens=1000,
                    child_separator=DIFY_CHILD_SEPARATOR,
                    child_max_tokens=800,
                    parent_chunk_overlap=0,
                    child_chunk_overlap=0,
                ),
                disposition=disposition,
                error_code=(
                    "PRIVACY_REVIEW_REQUIRED" if needs_review else None
                ),
                error_message=(
                    "；".join(review_reasons[:3]) or "隐私或版面检查需要人工确认"
                    if needs_review
                    else None
                ),
                warnings=user_visible_warnings(review_reasons),
                audit={
                    "privacy_level": "private_anonymized",
                    "source_markdown_empty": not bool(markdown.strip()),
                    **({"pdf_conversion": conversion_audit} if conversion_audit else {}),
                    "identity_aliases": aliases,
                    "forbidden_public_names": sorted(aliases),
                    "redaction_summary": redaction_summary,
                    "residual_pii_count": len(residual_pii),
                    "model_blocks": (
                        {} if needs_review else _source_blocks(safe_markdown)
                    ),
                },
            )
        ]


_ADVISOR_TOPIC_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "战略与商业模式",
        frozenset({"创业", "一人公司", "商业模式", "定位", "品牌", "职业发展", "选择"}),
    ),
    (
        "产品与客户",
        frozenset({"产品", "定价", "交付", "客户", "用户需求", "咨询"}),
    ),
    (
        "市场销售与增长",
        frozenset(
            {
                "营销",
                "销售",
                "成交",
                "获客",
                "私域",
                "流量",
                "自媒体",
                "公众号",
                "小红书",
                "朋友圈",
                "内容创作",
                "变现",
            }
        ),
    ),
    ("组织与管理", frozenset({"团队管理", "目标管理", "时间管理"})),
    ("财务与风险", frozenset({"收入", "盈利"})),
    (
        "学习与成长",
        frozenset({"学习方法", "读书", "写作", "能力", "成长", "习惯", "心理韧性"}),
    ),
    (
        "决策与执行",
        frozenset({"决策", "效率", "执行力", "认知", "行动", "复盘"}),
    ),
    ("AI与智能体", frozenset({"ai商业", "智能体", "提示词"})),
)


def _advisor_topic(decision: SemanticDecision) -> str:
    topic_terms = set(decision.topics)
    for topic, terms in _ADVISOR_TOPIC_GROUPS:
        if topic_terms & terms:
            return topic
    return ""


def _source_blocks(text: str) -> dict[str, str]:
    paragraphs = [
        value.strip()
        for value in re.split(r"\n\s*\n+", str(text or ""))
        if value.strip()
    ]
    return {
        f"b{index:04d}": value
        for index, value in enumerate(paragraphs, start=1)
    }


class WechatPublicAccountCognitionStrategy:
    code = "wechat_public_account_cognition"
    display_name = "微信公众号认知分享"
    supported_extensions = {".txt"}
    sensitive = False
    knowledge_base_code = "general"
    # The full internal record keeps source lineage.  Only the business fields
    # defined by the advisor RAG plan are written to Dify for this strategy.
    dify_metadata_fields = frozenset(
        {"record_type", "topic", "source_title", "industry", "business_stage"}
    )

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        units = parse_wechat_public_account(file_path)
        documents: list[CleanedDocument] = []
        seen_bodies: set[str] = set()
        for unit in units:
            title, body, identity_audit = redact_article_identity(
                unit.title,
                unit.body.strip(),
                provided_identities=(unit.author,),
            )
            if not body or "body_candidate_conflict" in unit.warnings:
                continue
            if DIFY_PARENT_SEPARATOR in body or DIFY_CHILD_SEPARATOR in body:
                raise ValueError(f"文章 {unit.sequence} 正文与Dify分隔符碰撞")

            fingerprint = hashlib.sha256(
                re.sub(r"\s+", "", body).lower().encode("utf-8")
            ).hexdigest()
            if fingerprint in seen_bodies:
                continue
            seen_bodies.add(fingerprint)

            source_body = body
            documents.append(
                CleanedDocument(
                    markdown=source_body,
                    title=title,
                    metadata={
                        "record_type": "article",
                        "source_title": title,
                    },
                    chunking=_article_chunking(),
                    disposition="awaiting_model",
                    audit={
                        **identity_audit,
                        "model_blocks": _source_blocks(source_body),
                        "published_at": unit.published_at,
                        "parser_warnings": list(unit.warnings),
                    },
                )
            )
        if not documents:
            raise ValueError(f"{original_filename} 没有通过自动门禁的可入库商业知识")
        return documents


_ARTICLE_METADATA_FIELDS = frozenset(
    {"record_type", "topic", "source_title", "industry", "business_stage"}
)


def _article_chunking(*, parent_max_tokens: int = 1000, child_max_tokens: int = 350) -> ChunkingConfig:
    return ChunkingConfig(
        doc_form="hierarchical_model",
        process_mode="hierarchical",
        parent_mode="paragraph",
        parent_separator=DIFY_PARENT_SEPARATOR,
        parent_max_tokens=parent_max_tokens,
        child_separator=DIFY_CHILD_SEPARATOR,
        child_max_tokens=child_max_tokens,
        parent_chunk_overlap=0,
        child_chunk_overlap=0,
    )


def _build_article_document(
    *,
    title: str,
    body: str,
    parent_max_tokens: int = 1000,
    child_max_tokens: int = 350,
    record_type: str = "article",
    identity_candidates: Iterable[str] = (),
    requires_model: bool = True,
) -> CleanedDocument:
    title, body, identity_audit = redact_article_identity(
        title,
        body,
        provided_identities=identity_candidates,
    )
    model_blocks = _source_blocks(body)
    if not model_blocks:
        return CleanedDocument(
            markdown="",
            title=title,
            metadata={"record_type": record_type, "source_title": title},
            chunking=_article_chunking(
                parent_max_tokens=parent_max_tokens,
                child_max_tokens=child_max_tokens,
            ),
            disposition="excluded",
            error_code="CONTENT_MISSING",
            error_message="没有可供商业知识判断的正文",
            audit=identity_audit,
        )
    if requires_model:
        topic = ""
        markdown = body.strip()
        disposition = "awaiting_model"
    else:
        # Book and lead-magnet scope is selected by the uploader. They do not
        # spend model tokens and do not require a keyword-derived topic.
        topic = ""
        # Keep the local source Markdown free of Dify control separators.
        # The publication worker passes the configured separators to Dify,
        # which owns the hierarchical split. Rendering them here made the
        # subsequent boundary validator reject every lead-magnet document.
        markdown = body.strip()
        disposition = "pending"
    metadata = {
        "record_type": record_type,
        "source_title": title,
    }
    if topic:
        metadata["topic"] = topic
    return CleanedDocument(
        markdown=markdown,
        title=title,
        metadata=metadata,
        chunking=_article_chunking(
            parent_max_tokens=parent_max_tokens,
            child_max_tokens=child_max_tokens,
        ),
        disposition=disposition,
        error_code=None,
        error_message=None,
        audit={
            **identity_audit,
            "model_blocks": model_blocks if requires_model else {},
            "semantic_mode": "model" if requires_model else "trusted_upload_scope",
        },
    )


class ZsxqPostStrategy:
    code = "zsxq_post"
    display_name = "知识星球帖子"
    supported_extensions = {".md", ".markdown"}
    sensitive = False
    knowledge_base_code = "general"
    dify_metadata_fields = _ARTICLE_METADATA_FIELDS

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        table_audit: dict[str, Any] = {}
        source_text = _read_text_markdown(
            file_path,
            file_path.suffix.lower(),
            table_audit=table_audit,
        )
        units = parse_markdown_posts_text(source_text, file_path)
        source_aliases = discover_author_aliases("", source_text)
        documents = [
            _build_article_document(
                title=unit.title,
                body=unit.body,
                child_max_tokens=800,
                identity_candidates=source_aliases,
            )
            for unit in units
            if unit.body.strip()
        ]
        for document in documents:
            if table_audit:
                document.audit.update(table_audit)
        if not documents:
            raise ValueError(f"{original_filename} 没有可清洗的知识星球正文")
        return documents


class XiaobotPostStrategy:
    code = "xiaobot_post"
    display_name = "小报童帖子"
    supported_extensions = {".md", ".markdown"}
    sensitive = False
    knowledge_base_code = "general"
    dify_metadata_fields = _ARTICLE_METADATA_FIELDS

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        table_audit: dict[str, Any] = {}
        source_text = _read_text_markdown(
            file_path,
            file_path.suffix.lower(),
            table_audit=table_audit,
        )
        units = parse_markdown_posts_text(source_text, file_path)
        source_aliases = discover_author_aliases("", source_text)
        documents = [
            _build_article_document(
                title=unit.title,
                body=unit.body,
                identity_candidates=source_aliases,
            )
            for unit in units
            if unit.body.strip()
        ]
        for document in documents:
            if table_audit:
                document.audit.update(table_audit)
        if not documents:
            raise ValueError(f"{original_filename} 没有可清洗的小报童正文")
        return documents


class LeadMagnetStrategy:
    code = "lead_magnet"
    display_name = "资料钩子"
    supported_extensions = {".md", ".markdown", ".txt", ".docx", ".pdf"}
    sensitive = False
    knowledge_base_code = "general"
    dify_metadata_fields = _ARTICLE_METADATA_FIELDS

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from .document_structure import remove_reliable_toc

        conversion: dict[str, Any] = {}
        title, markdown = _load_markdown_like(
            file_path, original_filename, diagnostics=conversion
        )
        toc = remove_reliable_toc(markdown)
        document = _build_article_document(
            title=title,
            body=toc.markdown,
            parent_max_tokens=1200,
            child_max_tokens=350,
            requires_model=False,
        )
        if toc.needs_review:
            document.disposition = "review_required"
            document.error_code = "TOC_REVIEW_REQUIRED"
            document.error_message = "疑似目录无法与正文标题可靠对应"
        document.warnings.extend(toc.warnings)
        document.audit["toc_detected"] = toc.detected
        document.audit["toc_headings"] = toc.headings
        if conversion:
            document.audit["pdf_conversion"] = conversion
        if not document.markdown.strip():
            raise ValueError(f"{original_filename} 没有可清洗正文")
        return [document]


def _split_book_sections(markdown: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("# ") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


def _split_book_markdown(markdown: str, limit_bytes: int) -> list[str]:
    if len(markdown.encode("utf-8")) <= limit_bytes:
        return [markdown]
    sections = _split_book_sections(markdown)
    if len(sections) <= 1:
        sections = [part.strip() for part in re.split(r"\n\s*\n", markdown) if part.strip()]
    parts: list[str] = []
    current: list[str] = []
    for section in sections:
        candidate = "\n\n".join([*current, section]).strip()
        if current and len(candidate.encode("utf-8")) > limit_bytes:
            parts.append("\n\n".join(current).strip())
            current = [section]
        else:
            current.append(section)
    if current:
        parts.append("\n\n".join(current).strip())
    return [part for part in parts if part]


class BookStrategy:
    code = "book"
    display_name = "书籍"
    supported_extensions = {".md", ".markdown", ".txt", ".docx", ".pdf"}
    sensitive = False
    knowledge_base_code = "general"
    dify_metadata_fields = frozenset({"record_type", "topic", "source_title", "book_id", "part_index", "part_count"})

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from .document_structure import remove_reliable_toc

        conversion: dict[str, Any] = {}
        title, markdown = _load_markdown_like(
            file_path, original_filename, diagnostics=conversion
        )
        toc = remove_reliable_toc(markdown)
        markdown = toc.markdown
        parts = _split_book_markdown(markdown, get_settings().cleaning_markdown_split_bytes)
        if not parts:
            raise ValueError(f"{original_filename} 没有可清洗正文")
        metadata_topic = ""
        book_id = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]
        documents: list[CleanedDocument] = []
        total = len(parts)
        for index, part in enumerate(parts, start=1):
            metadata: dict[str, Any] = {
                "record_type": "book",
                "source_title": title,
                "book_id": book_id,
            }
            if metadata_topic:
                metadata["topic"] = metadata_topic
            if total > 1:
                metadata["part_index"] = index
                metadata["part_count"] = total
            documents.append(
                CleanedDocument(
                    markdown=part,
                    title=title if total == 1 else f"{title}（第{index}/{total}卷）",
                    metadata=metadata,
                    chunking=ChunkingConfig(
                        doc_form="hierarchical_model",
                        process_mode="hierarchical",
                        parent_mode="paragraph",
                        parent_separator=DIFY_PARENT_SEPARATOR,
                        parent_max_tokens=1500,
                        child_separator=DIFY_CHILD_SEPARATOR,
                        child_max_tokens=400,
                        parent_chunk_overlap=0,
                        child_chunk_overlap=0,
                    ),
                    disposition=(
                        "review_required"
                        if toc.needs_review
                        else "pending"
                    ),
                    error_code=(
                        "TOC_REVIEW_REQUIRED"
                        if toc.needs_review
                        else None
                    ),
                    error_message=(
                        "疑似目录无法与正文标题可靠对应"
                        if toc.needs_review
                        else (
                            None
                            if metadata_topic
                            else None
                        )
                    ),
                    warnings=list(toc.warnings),
                    audit={
                        "split_parts": total,
                        "privacy_level": "public_source",
                        "toc_detected": toc.detected,
                        "toc_headings": toc.headings,
                        **({"pdf_conversion": conversion} if conversion else {}),
                    },
                )
            )
        return documents


class CourseInformationStrategy:
    """Strict catalogue cleaner for one or more structured course records."""

    code = "course_information"
    display_name = "课程信息"
    supported_extensions = {".md", ".markdown", ".txt", ".docx"}
    sensitive = False
    knowledge_base_code = "course"
    dify_metadata_fields = frozenset(
        {"document_type", "course_id", "direction", "stage", "source_platform", "series_name", "status"}
    )

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from .course_import import _course_text, load_course_records_text
        from .models import Course

        table_audit: dict[str, Any] = {}
        text = _read_text_markdown(
            file_path,
            file_path.suffix.lower(),
            table_audit=table_audit,
        )
        # An initial course upload has no dependency on a live catalogue.  Only
        # touch the DB when the supplied template explicitly names prerequisite
        # IDs that need resolving against already published courses.
        courses = load_course_records_text(
            text, validate_prerequisite_references=False
        )
        if any(item.prerequisite_course_ids for item in courses):
            with SessionLocal() as session:
                known_course_ids = set(session.scalars(select(Course.course_id)))
            courses = load_course_records_text(
                text, known_course_ids=known_course_ids
            )
        return [
            CleanedDocument(
                markdown=_course_text(item),
                title=item.name,
                metadata={
                    "document_type": "course",
                    "course_id": item.course_id,
                    "direction": item.direction,
                    "stage": item.stage,
                    "source_platform": item.source_platform or "小鹅通",
                    "series_name": item.series_name or "",
                    "status": "active" if item.enabled and item.review_status == "approved" else "inactive",
                },
                audit={
                    "catalog_record": item.model_dump(mode="json"),
                    **table_audit,
                },
            )
            for item in courses
        ]


class LearningRouteStrategy:
    """Strict route cleaner; stage courses resolve against existing catalogue IDs."""

    code = "learning_route"
    display_name = "学习路线"
    supported_extensions = {".md", ".markdown", ".txt", ".docx"}
    sensitive = False
    knowledge_base_code = "course"
    dify_metadata_fields = frozenset({"document_type", "route_id", "direction", "status"})

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from .course_import import _route_text, load_route_records_text
        from .models import Course

        table_audit: dict[str, Any] = {}
        text = _read_text_markdown(
            file_path,
            file_path.suffix.lower(),
            table_audit=table_audit,
        )
        with SessionLocal() as session:
            known_courses = {
                item.course_id: item.name
                for item in session.scalars(
                    select(Course).where(
                        Course.enabled.is_(True), Course.review_status == "approved"
                    )
                )
            }
        routes = load_route_records_text(text, known_courses=known_courses)
        return [
            CleanedDocument(
                markdown=_route_text(item),
                title=item.name,
                metadata={
                    "document_type": "learning_route",
                    "route_id": item.route_id,
                    "direction": item.direction,
                    "status": "active" if item.enabled and item.review_status == "approved" else "inactive",
                },
                audit={
                    "catalog_record": item.model_dump(mode="json"),
                    **table_audit,
                },
            )
            for item in routes
        ]


class CourseRouteCatalogStrategy:
    """Parse one XLSX into deterministic course and route review drafts."""

    code = "course_route_catalog"
    display_name = "课程信息与学习路线（合并）"
    supported_extensions = {".xlsx"}
    sensitive = False
    knowledge_base_code = "course"
    dify_metadata_fields = frozenset(
        {
            "document_type",
            "course_id",
            "route_id",
            "direction",
            "stage",
            "source_platform",
            "series_name",
            "status",
        }
    )

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from .course_route_catalog import (
            normalize_course_url,
            parse_course_route_catalog,
        )
        from .models import Course

        with SessionLocal() as session:
            existing_ids: dict[str, str] = {}
            for row in session.scalars(select(Course)):
                try:
                    existing_ids[normalize_course_url(row.official_url)] = row.course_id
                except ValueError:
                    continue
        catalog = parse_course_route_catalog(
            file_path,
            existing_course_ids_by_url=existing_ids,
        )
        audit_summary = {
            "sheet_name": catalog.audit.sheet_name,
            "source_sha256": catalog.audit.source_sha256,
            "source_rows": catalog.audit.source_rows,
            "unknown_columns": catalog.audit.unknown_columns,
            "duplicate_link_groups": catalog.audit.duplicate_link_groups,
            "group_headers": catalog.audit.group_headers,
            "effective_groups": catalog.audit.effective_groups,
            "collections": [
                {
                    "collection_id": item.collection_id,
                    "name": item.name,
                    "raw_title": item.raw_title,
                    "declared_lessons": item.declared_lessons,
                    "total_minutes": item.total_minutes,
                    "entry_course_id": item.entry_course_id,
                    "member_course_ids": item.member_course_ids,
                    "member_orders": item.member_orders,
                    "warnings": item.warnings,
                }
                for item in catalog.collections
            ],
            "invalid_rows": catalog.audit.invalid_rows,
            "warnings": catalog.audit.warnings,
        }
        structure_blocked = any(
            item.structure_conflicts for item in catalog.courses
        ) or any(
            warning.get("code") in {
                "duplicate_course_order",
                "route_course_stage_conflict",
            }
            for warning in catalog.audit.warnings
        )
        documents: list[CleanedDocument] = []
        for item in catalog.courses:
            blocks = {
                "b001": f"课程名称：{item.name}",
                "b002": f"阶段：{item.stage}",
                "b003": f"学习路线：{item.route_name}",
                "b004": f"系列名称：{item.series_name}",
                "b005": f"课程方向：{item.direction_candidate}",
                "b006": f"课程描述：{item.description_candidate}",
                "b007": f"适用人群：{item.audience_candidate}",
                "b008": f"课程亮点：{item.highlights_candidate}",
                "b009": f"学习目标：{item.learning_outcomes_candidate}",
            }
            blocks = {
                key: value
                for key, value in blocks.items()
                if value.split("：", 1)[-1].strip()
            }
            documents.append(
                CleanedDocument(
                    markdown=f"# {item.name}\n\n课程资料等待审核后发布。",
                    title=item.name,
                    metadata={
                        "document_type": "course",
                        "course_id": item.course_id,
                        "stage": item.stage,
                        "source_platform": "小鹅通",
                        "series_name": item.collection_clean_name or item.series_name,
                        "status": "inactive",
                        "review_status": "pending_review",
                    },
                    disposition="review_required" if structure_blocked else "awaiting_model",
                    error_code=(
                        "CATALOG_STRUCTURE_REVIEW_REQUIRED"
                        if item.structure_conflicts
                        else ("CATALOG_STRUCTURE_REVIEW_BLOCKED" if structure_blocked else None)
                    ),
                    error_message=(
                        "同一课程链接存在结构字段冲突，需先人工确定最终名称/合集"
                        if item.structure_conflicts
                        else ("同一文件存在结构冲突，需先完成结构审核" if structure_blocked else None)
                    ),
                    audit={
                        "catalog_kind": "course",
                        "catalog_draft": {
                            "course_id": item.course_id,
                            "name": item.name,
                            "name_candidates": item.name_candidates,
                            "series_candidates": item.series_candidates,
                            "official_url": item.normalized_url,
                            "stage": item.stage,
                            "source_platform": "小鹅通",
                            "series_name": item.collection_clean_name or item.series_name,
                            "collection_id": item.collection_id,
                            "collection_name": item.collection_clean_name,
                            "collection_order": item.collection_order,
                            "collection_entry": item.collection_entry,
                            "collection_declared_lessons": next(
                                (
                                    collection.declared_lessons
                                    for collection in catalog.collections
                                    if collection.collection_id == item.collection_id
                                ),
                                None,
                            ),
                            "collection_total_minutes": next(
                                (
                                    collection.total_minutes
                                    for collection in catalog.collections
                                    if collection.collection_id == item.collection_id
                                ),
                                None,
                            ),
                            "collection_raw_title": next(
                                (
                                    collection.raw_title
                                    for collection in catalog.collections
                                    if collection.collection_id == item.collection_id
                                ),
                                "",
                            ),
                            "collection_entry_course_id": next(
                                (
                                    collection.entry_course_id
                                    for collection in catalog.collections
                                    if collection.collection_id == item.collection_id
                                ),
                                "",
                            ),
                            "collection_warnings": next(
                                (
                                    collection.warnings
                                    for collection in catalog.collections
                                    if collection.collection_id == item.collection_id
                                ),
                                [],
                            ),
                            "direction": item.direction_candidate,
                            "description": item.description_candidate,
                            "audience": item.audience_candidate,
                            "highlights": [
                                value.strip()
                                for value in re.split(
                                    r"[；;\n]+", item.highlights_candidate
                                )
                                if value.strip()
                            ],
                            "learning_outcomes": [
                                value.strip()
                                for value in re.split(
                                    r"[；;\n]+", item.learning_outcomes_candidate
                                )
                                if value.strip()
                            ],
                            "enabled": False,
                            "review_status": "pending_review",
                        },
                        "structure_conflicts": item.structure_conflicts,
                        "model_blocks": blocks,
                        "field_sources": {
                            "name": "source",
                            "official_url": "source",
                            "stage": "source",
                            "course_id": "deterministic",
                        },
                        "source_rows": item.source_rows,
                        "catalog_audit": audit_summary,
                    },
                )
            )
        course_names_by_id = {item.course_id: item.name for item in catalog.courses}
        for item in catalog.routes:
            stage_context: list[str] = []
            for stage in item.stages:
                names = [
                    course_names_by_id.get(str(course_id), str(course_id))
                    for course_id in stage.get("course_ids", [])
                ]
                if names:
                    stage_context.append(
                        f"{stage.get('name', '')}：" + "、".join(names)
                    )
            route_blocks = {
                "b001": f"路线名称：{item.name}",
                "b002": f"路线目标：{item.target_candidate}",
                "b003": "路线阶段与课程：" + "；".join(stage_context),
            }
            route_blocks = {
                key: value
                for key, value in route_blocks.items()
                if value.split("：", 1)[-1].strip()
            }
            documents.append(
                CleanedDocument(
                    markdown=f"# {item.name}\n\n学习路线等待审核和排序后发布。",
                    title=item.name,
                    metadata={
                        "document_type": "learning_route",
                        "route_id": item.route_id,
                        "status": "inactive",
                        "review_status": "pending_review",
                    },
                    disposition="review_required" if structure_blocked else "awaiting_model",
                    error_code=(
                        "CATALOG_STRUCTURE_REVIEW_BLOCKED" if structure_blocked else None
                    ),
                    error_message=(
                        "同一文件存在结构冲突，需先完成结构审核" if structure_blocked else None
                    ),
                    audit={
                        "catalog_kind": "route",
                        "catalog_draft": {
                            "route_id": item.route_id,
                            "name": item.name,
                            "direction": item.direction_candidate,
                            "target": item.target_candidate,
                            "stages": item.stages,
                            "enabled": False,
                            "review_status": "pending_review",
                        },
                        "model_blocks": route_blocks,
                        "field_sources": {
                            "name": "source",
                            "route_id": "deterministic",
                            "stages": "deterministic_pending_review",
                        },
                        "source_rows": item.source_rows,
                        "catalog_audit": audit_summary,
                    },
                )
            )
        if not documents:
            raise ValueError("工作簿没有生成任何课程或学习路线草稿")
        return documents


STRATEGY_REGISTRY: dict[str, CleaningStrategy] = {
    strategy.code: strategy
    for strategy in (
        StandardDocumentStrategy(),
        TestDocumentStrategy(),
        PersonalBusinessConsultationStrategy(),
        WechatPublicAccountCognitionStrategy(),
        ZsxqPostStrategy(),
        XiaobotPostStrategy(),
        LeadMagnetStrategy(),
        BookStrategy(),
        CourseInformationStrategy(),
        LearningRouteStrategy(),
        CourseRouteCatalogStrategy(),
    )
}

# Keep the activity strategy feature-owned while exposing it through the
# existing registry and upload/test-page discovery contract.
STRATEGY_REGISTRY[ActivityCleaningStrategy.code] = ActivityCleaningStrategy()

SYSTEM_METADATA_FIELDS = {
    "source_file_id",
    "source_filename",
    "source_extension",
    "source_sha256",
    "upload_batch_id",
    "cleaning_strategy",
    "cleaning_spec_revision",
    "source_document_index",
    "build_version",
    "document_type",
    "review_status",
    "course_id",
    "route_id",
    "direction",
    "stage",
    "source_platform",
    "series_name",
    "status",
    "record_type",
    "topic",
    "source_title",
    "industry",
    "business_stage",
    "event_id",
    "event_status",
    "source_status",
    "event_type",
    "delivery_mode",
    "city",
    "event_start_ts",
    "event_end_ts",
    "registration_deadline_ts",
    "registration_url",
}
ALLOWED_COMMON_METADATA = {
    "document_type",
    "review_status",
    "course_id",
    "route_id",
    "direction",
    "stage",
    "source_platform",
    "series_name",
    "status",
    # Only the dedicated Streamlit test page writes this.  It remains local
    # provenance and is deliberately absent from every strategy's Dify
    # metadata allow-list.
    "_test_ui_cleanup_run_id",
}
ITEM_TERMINAL_STATUSES = {"succeeded", "excluded", "review_required", "failed"}
ITEM_SUCCESS_STATUSES = {"succeeded"}
ITEM_PROCESSED_WITHOUT_ERROR = {"succeeded", "excluded"}
ITEM_RETRYABLE_STATUSES = {"failed"}
FILE_TERMINAL_STATUSES = {"succeeded", "excluded", "review_required", "failed", "mixed"}

_ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    },
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}


def validate_uploaded_file(path: Path, suffix: str, content_type: str | None) -> None:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    allowed_types = _ALLOWED_CONTENT_TYPES.get(suffix, set())
    if normalized_type and allowed_types and normalized_type not in allowed_types:
        raise ValueError(f"声明的MIME类型 {normalized_type} 与扩展名 {suffix} 不匹配")
    if suffix == ".pdf":
        with path.open("rb") as handle:
            header = handle.read(5)
        if header != b"%PDF-":
            raise ValueError("PDF文件头无效")
        return
    if suffix in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                required = (
                    "word/document.xml"
                    if suffix == ".docx"
                    else "xl/workbook.xml"
                )
                if "[Content_Types].xml" not in names or required not in names:
                    raise ValueError(f"{suffix.upper()[1:]}内部结构无效")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{suffix.upper()[1:]}不是有效ZIP文档") from exc
        return
    if suffix in {".md", ".markdown", ".txt"}:
        with path.open("rb") as handle:
            sample = handle.read(64 * 1024)
        if b"\x00" in sample:
            raise ValueError("文本文件包含二进制空字节")
        encodings = ("utf-8-sig", "gb18030") if suffix == ".txt" else ("utf-8-sig",)
        for encoding in encodings:
            try:
                decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
                decoder.decode(sample, final=False)
                break
            except UnicodeDecodeError:
                continue
        else:
            names = "UTF-8或GB18030" if suffix == ".txt" else "UTF-8"
            raise ValueError(f"文本文件不能按{names}无损解码")


def cleaning_batch_cache_key(batch_id: str) -> str:
    return f"ai:cleaning:batch:{batch_id}"


def cleaning_batch_files_cache_key(batch_id: str) -> str:
    return f"ai:cleaning:batch:{batch_id}:files"


def normalized_original_filename(value: str) -> str:
    name = PurePath(value.replace("\\", "/")).name
    name = "".join(char for char in unicodedata.normalize("NFC", name) if unicodedata.category(char) != "Cc")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:500] or "未命名文件"


def normalized_filename_key(value: str) -> str:
    """Case/Unicode/whitespace-insensitive local duplicate key."""
    return re.sub(r"\s+", " ", normalized_original_filename(value)).strip().casefold()


def dify_document_name(
    original_filename: str,
    sha256: str,
    sequence: int,
    *,
    split: bool = False,
) -> str:
    del sha256
    if sequence < 1:
        raise ValueError("Dify文档序号必须为正整数")
    original = normalized_original_filename(original_filename)
    path = PurePath(original)
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    # Keep the user's filename stem as the stable visible identity while
    # making the normalized knowledge document type explicit.
    stem = re.sub(r"[\\/:*?\"<>|]", "_", stem).strip(" .") or "未命名文件"
    suffix = ".md"
    if not split:
        max_stem = max(1, 300 - len(suffix))
        return f"{stem[:max_stem]}{suffix}"
    max_stem = max(1, 300 - len(suffix) - 5)
    stem = stem[:max_stem]
    return f"{stem}__{sequence:03d}{suffix}"


def dify_activity_document_name(
    activity_name: str,
    event_id: str,
    version: str,
) -> str:
    """Build a readable, collision-resistant name for an activity revision."""

    if not event_id or not version:
        raise ValueError("活动 Dify 文档名缺少 event_id 或版本摘要")
    base = dify_document_name(f"{activity_name}.md", "", 1).removesuffix(".md")
    safe_event_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(event_id))
    safe_version = re.sub(r"[^A-Za-z0-9_-]", "_", str(version))
    suffix = f"__{safe_event_id}__{safe_version[:16]}.md"
    max_base = max(1, 300 - len(suffix))
    return f"{base[:max_base]}{suffix}"


def _serialize_item(item: CleaningDocument) -> dict[str, Any]:
    return {
        "item_id": item.id,
        "sequence": item.sequence,
        "title": item.title,
        "document_key": item.document_key,
        "status": item.status,
        "stage": item.stage,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "retryable": item.retryable,
        "review_revision": item.review_revision,
        "warnings": user_visible_warnings(item.warnings_json or []),
        "metadata": item.metadata_json or {},
        "chunking": item.chunking_json or {},
        "dify_document_id": item.dify_document_id,
        "shard_id": item.shard_id,
        "shard_parent_index": item.shard_parent_index,
        "remote_parent_count": item.remote_parent_count,
        "remote_child_count": item.remote_child_count,
        "updated_at": item.updated_at,
    }


def _summarize_file(file_row: CleaningFile) -> dict[str, Any]:
    items = sorted(file_row.documents, key=lambda item: item.sequence)
    item_total = len(items)
    status_counts = {
        "pending": sum(item.status == "pending" for item in items),
        "processing": sum(item.status == "processing" for item in items),
        "awaiting_model": sum(item.status == "awaiting_model" for item in items),
        "model_processing": sum(item.status == "model_processing" for item in items),
        "shard_pending": sum(item.status == "shard_pending" for item in items),
        "shard_indexing": sum(item.status == "shard_indexing" for item in items),
        "succeeded": sum(item.status == "succeeded" for item in items),
        "excluded": sum(item.status == "excluded" for item in items),
        "review_required": sum(item.status == "review_required" for item in items),
        "failed": sum(item.status == "failed" for item in items),
    }
    terminal = status_counts["succeeded"] + status_counts["excluded"] + status_counts["review_required"] + status_counts["failed"]
    if item_total == 0:
        outcome = file_row.outcome if file_row.outcome and file_row.outcome != "pending" else file_row.status
    elif terminal < item_total or any(
        status_counts[name]
        for name in (
            "processing",
            "pending",
            "awaiting_model",
            "model_processing",
            "shard_pending",
            "shard_indexing",
        )
    ):
        outcome = "processing"
    elif status_counts["excluded"] == item_total:
        outcome = "excluded"
    elif (
        status_counts["succeeded"] + status_counts["excluded"]
        == item_total
    ):
        outcome = "succeeded"
    elif status_counts["review_required"] == item_total:
        outcome = "review_required"
    elif status_counts["failed"] == item_total:
        outcome = "failed"
    else:
        outcome = "mixed"
    progress = 100 if item_total == 0 and outcome in FILE_TERMINAL_STATUSES else int((terminal / item_total) * 100) if item_total else 0
    return {
        "file_id": file_row.id,
        "original_filename": file_row.original_filename,
        "status": file_row.status,
        "outcome": outcome,
        "stage": file_row.stage,
        "error_code": file_row.error_code,
        "error_message": file_row.error_message,
        "updated_at": file_row.updated_at,
        "source_expires_at": file_row.source_expires_at,
        "source_deleted_at": file_row.source_deleted_at,
        "item_counts": status_counts,
        "item_total": item_total,
        "item_terminal": terminal,
        "progress": progress,
        "items": [_serialize_item(item) for item in items],
        "publication_shards": [
            {
                "shard_id": shard.id,
                "shard_index": shard.shard_index,
                "status": shard.status,
                "item_count": shard.item_count,
                "content_bytes": shard.content_bytes,
                "dify_document_id": shard.dify_document_id,
                "remote_parent_count": shard.remote_parent_count,
                "remote_child_count": shard.remote_child_count,
                "last_error": shard.last_error,
            }
            for shard in sorted(
                file_row.publication_shards,
                key=lambda value: value.shard_index,
            )
        ],
    }


def _serialize_model_task(task: ModelCleaningTask) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "mode": task.mode,
        "model": task.model,
        "prompt_version": task.prompt_version,
        "status": task.status,
        "candidate_fingerprint": task.candidate_fingerprint,
        "candidate_count": task.candidate_count,
        "estimated_input_tokens": task.estimated_input_tokens,
        "estimated_output_tokens": task.estimated_output_tokens,
        "estimated_cost_low": task.estimated_cost_low,
        "estimated_cost_high": task.estimated_cost_high,
        "price_version": task.price_version,
        "price_effective_date": task.price_effective_date,
        "price_valid_until": task.price_valid_until,
        "approved_at": task.approved_at,
        "provider_batch_ids": task.provider_batch_ids or [],
        "result_counts": task.result_counts or {},
        "actual_usage": task.actual_usage or {},
        "cleanup_status": task.cleanup_status,
        "last_error": task.last_error,
    }


def _serialize_batch(batch: CleaningBatch) -> dict[str, Any]:
    files = [_summarize_file(item) for item in batch.files]
    file_total = len(files)
    file_succeeded = sum(item["outcome"] == "succeeded" for item in files)
    file_failed = sum(item["outcome"] == "failed" for item in files)
    file_review = sum(item["outcome"] == "review_required" for item in files)
    file_excluded = sum(item["outcome"] == "excluded" for item in files)
    file_mixed = sum(item["outcome"] == "mixed" for item in files)
    total_items = sum(item["item_total"] for item in files)
    non_terminal_files = sum(item["outcome"] not in FILE_TERMINAL_STATUSES for item in files)
    item_counts = {
        "pending": sum(item["item_counts"]["pending"] for item in files),
        "processing": sum(item["item_counts"]["processing"] for item in files),
        "awaiting_model": sum(item["item_counts"]["awaiting_model"] for item in files),
        "model_processing": sum(item["item_counts"]["model_processing"] for item in files),
        "shard_pending": sum(item["item_counts"]["shard_pending"] for item in files),
        "shard_indexing": sum(item["item_counts"]["shard_indexing"] for item in files),
        "succeeded": sum(item["item_counts"]["succeeded"] for item in files),
        "excluded": sum(item["item_counts"]["excluded"] for item in files),
        "review_required": sum(item["item_counts"]["review_required"] for item in files),
        "failed": sum(item["item_counts"]["failed"] for item in files),
    }
    terminal_items = item_counts["succeeded"] + item_counts["excluded"] + item_counts["review_required"] + item_counts["failed"]
    status = (
        "completed"
        if file_total and non_terminal_files == 0 and terminal_items == total_items
        else batch.status
    )
    if file_total == 0:
        progress = 0
    elif total_items == 0:
        progress = 100 if all(item["outcome"] in FILE_TERMINAL_STATUSES for item in files) else 0
    else:
        progress = int((terminal_items / total_items) * 100)
    all_files_terminal = file_total > 0 and non_terminal_files == 0
    all_files_succeeded = all_files_terminal and file_succeeded == file_total
    all_files_processed_without_error = (
        all_files_terminal
        and file_failed == 0
        and file_review == 0
        and file_mixed == 0
    )
    return {
        "batch_id": batch.id,
        "strategy_code": batch.strategy_code,
        "knowledge_base_code": batch.knowledge_base_code,
        "upload_mode": batch.upload_mode,
        "status": status,
        "progress": progress,
        "next_poll_after_seconds": 2 if status != "completed" else 10,
        "all_succeeded": all_files_succeeded,
        "all_processed_without_error": all_files_processed_without_error,
        "total": file_total,
        "succeeded": file_succeeded,
        "failed": file_failed,
        "excluded": file_excluded,
        "review_required": file_review,
        "mixed": file_mixed,
        "file_counts": {
            "succeeded": file_succeeded,
            "failed": file_failed,
            "excluded": file_excluded,
            "review_required": file_review,
            "mixed": file_mixed,
        },
        "item_counts": item_counts,
        "model_tasks": [
            _serialize_model_task(item)
            for item in sorted(batch.model_tasks, key=lambda value: value.created_at)
        ],
        "files": files,
    }


def _mark_preflight_duplicate(row: CleaningFile, *, message: str) -> None:
    """Leave an auditable terminal item when upload-time deduplication wins.

    A duplicate is intentionally not re-indexed, but it is still a user
    submitted file.  Previously we only changed the file state; that made a
    completed upload appear to have no items at all and hid the reason from
    the cleaning-status API and UI.
    """

    row.status = "excluded"
    row.outcome = "excluded"
    row.stage = "duplicate_noop"
    row.error_code = None
    row.error_message = message
    if row.documents:
        return
    row.documents.append(
        CleaningDocument(
            sequence=1,
            title="重复内容（未重复入库）",
            dify_document_name=dify_document_name("", row.source_sha256, 1),
            document_key=f"{row.id}:duplicate:{row.source_sha256[:12]}",
            status="excluded",
            stage="duplicate_noop",
            error_code="EXACT_CONTENT_DUPLICATE",
            error_message=message,
            content_sha256=hashlib.sha256(
                f"duplicate:{row.source_sha256}".encode("utf-8")
            ).hexdigest(),
            metadata_json={
                "source_file_id": row.id,
                "source_filename": row.original_filename,
                "source_extension": row.extension,
                "source_sha256": row.source_sha256,
                "upload_batch_id": row.batch_id,
                "cleaning_strategy": row.batch.strategy_code,
                "cleaning_spec_revision": row.batch.spec_revision,
                "source_document_index": 1,
                "build_version": row.batch.build_version,
            },
            chunking_json={},
            audit_json={"duplicate_preflight": True},
            warnings_json=[],
            cleaned_markdown="",
            retryable=False,
        )
    )


def _preflight_local_duplicates(
    *,
    session: Session,
    knowledge_base_code: str,
    rows: list[CleaningFile],
) -> None:
    """Deduplicate against protected local provenance before Worker queuing.

    Source hashes must not be projected into Dify.  The local cleaning file
    record is authoritative for both successful remote publication and the
    cleaning revision, so no Dify listing is required here.
    """

    rows = [row for row in rows if row.source_sha256]
    if not rows:
        return
    # PostgreSQL advisory locks close the race between two simultaneous
    # uploads with the same content/name.  SQLite serializes the write
    # transaction itself, while other dialects retain the row-lock query.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        for lock_key in sorted({
            f"{knowledge_base_code}:sha:{row.source_sha256}"
            for row in rows
        } | {
            f"{knowledge_base_code}:name:{row.source_name_key or normalized_filename_key(row.original_filename)}"
            for row in rows
        }):
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})
    submitted_ids = {row.id for row in rows}
    activity_submission = any(
        getattr(getattr(row, "batch", None), "strategy_code", "") == "recent_activity"
        for row in rows
    )
    source_hashes = {row.source_sha256 for row in rows}
    source_names = {
        row.source_name_key or normalized_filename_key(row.original_filename)
        for row in rows
    }
    existing = session.execute(
        select(CleaningFile, CleaningBatch)
        .join(CleaningBatch, CleaningFile.batch_id == CleaningBatch.id)
        .where(
            (
                CleaningFile.source_sha256.in_(source_hashes)
                | CleaningFile.source_name_key.in_(source_names)
                | CleaningFile.source_name_key.is_(None)
            ),
            CleaningFile.status.in_(
                ["pending", "processing", "awaiting_model", "model_processing", "succeeded", "review_required", "mixed", "excluded"]
            ),
            CleaningFile.dedupe_released_at.is_(None),
            CleaningBatch.knowledge_base_code == knowledge_base_code,
        )
        .with_for_update()
    ).all()
    existing_by_hash = {
        file_row.source_sha256 for file_row, _batch in existing
        if file_row.id not in submitted_ids and file_row.source_sha256
    }
    existing_by_name = {
        file_row.source_name_key or normalized_filename_key(file_row.original_filename)
        for file_row, _batch in existing
        if file_row.id not in submitted_ids
        and not (activity_submission and _batch.strategy_code == "recent_activity")
    }
    seen_batch_hashes: set[str] = set()
    seen_batch_names: set[str] = set()
    for row in rows:
        name_key = row.source_name_key or normalized_filename_key(row.original_filename)
        if row.source_sha256 in seen_batch_hashes or name_key in seen_batch_names:
            _mark_preflight_duplicate(
                row,
                message="同批次内容或文件名重复，未重复索引",
            )
            continue
        seen_batch_hashes.add(row.source_sha256)
        seen_batch_names.add(name_key)
        if row.source_sha256 in existing_by_hash:
            _mark_preflight_duplicate(
                row,
                message="相同内容已存在；请先删除原文档后再上传",
            )
        elif name_key in existing_by_name:
            _mark_preflight_duplicate(
                row,
                message="同名文件已存在；请先删除原文档后再上传",
            )


@router.get("/cleaning-strategies")
def cleaning_strategies() -> list[dict[str, Any]]:
    hidden_strategy_codes = {
        "course_information",
        "learning_route",
        "course_route_catalog",
    }
    return [
        {
            "code": item.code,
            "display_name": item.display_name,
            "supported_extensions": sorted(item.supported_extensions),
            "knowledge_base_code": getattr(item, "knowledge_base_code", None),
        }
        for item in STRATEGY_REGISTRY.values()
        if item.code not in hidden_strategy_codes
    ]


@router.post("/cleaning-batches")
async def create_cleaning_batch(
    strategy_code: str = Form(...),
    knowledge_base_code: str = Form(...),
    upload_mode: str = Form(default="cleaned"),
    common_metadata: str = Form(default="{}"),
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    settings = get_settings()
    max_batch_bytes = settings.max_upload_batch_mb * 1024 * 1024
    disk_free = shutil.disk_usage(settings.upload_root.resolve()).free
    reserve = settings.upload_disk_reserve_mb * 1024 * 1024
    if disk_free < max_batch_bytes + reserve:
        raise ServiceError(
            "RESOURCE_EXHAUSTED",
            "上传磁盘空间不足，已拒绝创建清洗批次",
            507,
            True,
        )
    upload_mode = upload_mode.strip().lower()
    if upload_mode not in {"cleaned", "raw"}:
        raise ServiceError("VALIDATION_ERROR", "上传方式只能是 cleaned 或 raw", 422, False)
    strategy = STRATEGY_REGISTRY.get(strategy_code)
    if upload_mode == "cleaned" and strategy is None:
        raise ServiceError("VALIDATION_ERROR", "未注册的清洗策略", 422, False)
    if knowledge_base_code not in {"general", "course"}:
        raise ServiceError("VALIDATION_ERROR", "目标知识库代码无效", 422, False)
    required_knowledge_base = getattr(strategy, "knowledge_base_code", None) if strategy else None
    if required_knowledge_base and knowledge_base_code != required_knowledge_base:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"策略 {strategy_code} 只能写入 {required_knowledge_base} 知识库",
            422,
            False,
        )
    if not files or len(files) > settings.max_upload_files:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"一个批次最多上传 {settings.max_upload_files} 个文件",
            422,
            False,
        )
    # Admission control is deliberately at batch level.  Uploads are streamed
    # to disk, while Celery workers consume at a bounded queue concurrency.
    # Lock active rows on PostgreSQL so concurrent API requests cannot both
    # pass the same capacity check.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": "ai-cleaning-admission"},
        )
    active_batch_ids = session.scalars(
        select(CleaningBatch.id)
        .where(CleaningBatch.status.in_(["accepted", "processing", "awaiting_model", "model_processing", "publishing"]))
        .with_for_update()
    ).all()
    if len(active_batch_ids) >= settings.max_active_cleaning_batches:
        raise ServiceError(
            "RESOURCE_EXHAUSTED",
            f"当前清洗批次已达到并发上限 {settings.max_active_cleaning_batches}，请等待已有批次完成",
            429,
            True,
        )
    try:
        parsed_metadata = json.loads(common_metadata or "{}")
        if not isinstance(parsed_metadata, dict):
            raise ValueError
    except ValueError as exc:
        raise ServiceError("VALIDATION_ERROR", "公共 Metadata 必须是 JSON 对象", 422, False) from exc
    unexpected_fields = sorted(set(parsed_metadata) - ALLOWED_COMMON_METADATA)
    if unexpected_fields:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"公共 Metadata 包含未允许字段：{', '.join(unexpected_fields)}",
            422,
            False,
        )

    batch = CleaningBatch(
        strategy_code=strategy_code if upload_mode == "cleaned" else "raw_upload",
        knowledge_base_code=knowledge_base_code,
        upload_mode=upload_mode,
        common_metadata={
            **parsed_metadata,
            # This is server-owned admission state. It is added after the
            # client allow-list check and is never projected to Dify metadata.
            "_cleaning_review_policy": (
                review_policy_snapshot(strategy_code)
                if upload_mode == "cleaned"
                else {"enabled": False, "min_chars": 0, "force_review": False}
            ),
        },
        build_version=settings.build_version,
        spec_revision=settings.cleaning_spec_revision,
    )
    session.add(batch)
    session.flush()
    batch_dir = settings.upload_root.resolve() / batch.id
    batch_dir.mkdir(parents=True, exist_ok=False)
    total_bytes = 0
    pending_ids: list[str] = []
    max_file_bytes = settings.max_upload_file_mb * 1024 * 1024
    max_batch_bytes = settings.max_upload_batch_mb * 1024 * 1024

    for upload in files:
        original = normalized_original_filename(upload.filename or "未命名文件")
        suffix = Path(original).suffix.lower()
        declared_content_type = upload.content_type
        row = CleaningFile(
            batch_id=batch.id,
            original_filename=original,
            storage_filename="",
            extension=suffix,
            source_sha256="",
            source_name_key=normalized_filename_key(original),
            size_bytes=0,
            status="pending",
            stage="upload",
            source_expires_at=utcnow() + timedelta(days=settings.cleaning_source_error_retention_days),
        )
        session.add(row)
        session.flush()
        storage_name = f"{row.id}{suffix or '.bin'}"
        row.storage_filename = storage_name
        target = batch_dir / storage_name
        digest = hashlib.sha256()
        size = 0
        with target.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                total_bytes += len(chunk)
                if size > max_file_bytes:
                    row.status = "failed"
                    row.error_code = "FILE_TOO_LARGE"
                    row.error_message = f"单文件超过 {settings.max_upload_file_mb}MB"
                    break
                if total_bytes > max_batch_bytes:
                    row.status = "failed"
                    row.error_code = "FILE_TOO_LARGE"
                    row.error_message = f"批次总大小超过 {settings.max_upload_batch_mb}MB"
                    break
                digest.update(chunk)
                handle.write(chunk)
        row.size_bytes = size
        row.source_sha256 = digest.hexdigest()
        if row.status == "failed":
            target.unlink(missing_ok=True)
        elif suffix not in (_ALLOWED_CONTENT_TYPES if upload_mode == "raw" else strategy.supported_extensions):
            row.status = "failed"
            row.error_code = "UNSUPPORTED_FILE_TYPE"
            row.error_message = f"{'原样上传' if upload_mode == 'raw' else f'策略 {strategy_code}'} 不支持 {suffix or '无扩展名'}"
            target.unlink(missing_ok=True)
        else:
            try:
                validate_uploaded_file(target, suffix, declared_content_type)
            except ValueError as exc:
                row.status = "failed"
                row.error_code = "FILE_SIGNATURE_INVALID"
                row.error_message = str(exc)
                target.unlink(missing_ok=True)
            else:
                pending_ids.append(row.id)

    try:
        pending_rows = [session.get(CleaningFile, file_id) for file_id in pending_ids]
        _preflight_local_duplicates(
            session=session,
            knowledge_base_code=knowledge_base_code,
            rows=[item for item in pending_rows if item is not None],
        )
    except ServiceError:
        session.rollback()
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise
    pending_ids = [
        file_id
        for file_id in pending_ids
        if (row := session.get(CleaningFile, file_id)) is not None and row.status == "pending"
    ]
    for row in pending_rows:
        if row is None or row.status not in {"succeeded", "excluded"}:
            continue
        target = batch_dir / row.storage_filename
        target.unlink(missing_ok=True)
        deleted_at = utcnow()
        row.source_deleted_at = deleted_at
        # A preflight duplicate never created a remote document.  Its source
        # is removed immediately during batch creation, so it must not remain
        # as a permanent duplicate-protection tombstone and block the same
        # workbook from being uploaded again after the original is deleted.
        if row.status == "excluded" and not any(
            document.dify_document_id for document in row.documents
        ) and not any(
            shard.dify_document_id for shard in row.publication_shards
        ):
            row.dedupe_released_at = deleted_at
    if not pending_ids:
        batch.status = "completed"
    session.commit()
    manifest_batch = session.scalar(
        select(CleaningBatch)
        .where(CleaningBatch.id == batch.id)
        .options(selectinload(CleaningBatch.files))
    )
    cache.set_json(
        cleaning_batch_files_cache_key(batch.id),
        {
            "batch_id": batch.id,
            "files": [
                {"file_id": item.id, "original_filename": item.original_filename}
                for item in manifest_batch.files
            ],
        },
        ttl=settings.cleaning_status_retention_days * 24 * 60 * 60,
    )
    for file_id in pending_ids:
        try:
            queued_row = session.get(CleaningFile, file_id)
            if queued_row is None:
                raise ValueError("清洗文件在入队前不存在")
            _enqueue_cleaning_file(file_id, queued_row.original_filename)
        except Exception as exc:
            row = session.get(CleaningFile, file_id)
            if row:
                row.status = "failed"
                row.error_code = "CLEANING_FAILED"
                row.error_message = f"任务提交失败：{exc}"
    remaining = session.scalar(
        select(func.count(CleaningFile.id)).where(
            CleaningFile.batch_id == batch.id,
            CleaningFile.status.in_(["pending", "processing"]),
        )
    )
    batch.status = "processing" if remaining else "completed"
    session.commit()
    loaded = session.scalar(
        select(CleaningBatch)
        .where(CleaningBatch.id == batch.id)
        .options(selectinload(CleaningBatch.files))
    )
    record_event(
        level="INFO",
        category="operation",
        component="cleaning",
        event_code="CLEANING_BATCH_CREATED",
        status="accepted",
        message="清洗批次已创建",
        resource_type="cleaning_batch",
        resource_id=batch.id,
        details={
            "strategy_code": batch.strategy_code,
            "knowledge_base_code": knowledge_base_code,
            "upload_mode": upload_mode,
            "total_files": len(files),
            "queued_files": len(pending_ids),
        },
    )
    return _serialize_batch(loaded)


@router.get("/cleaning-batches/{batch_id}")
def get_cleaning_batch(batch_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    cached = cache.get_json(cleaning_batch_cache_key(batch_id))
    if isinstance(cached, dict):
        return cached
    batch = session.scalar(
        select(CleaningBatch)
        .where(CleaningBatch.id == batch_id)
        .options(
            selectinload(CleaningBatch.files).selectinload(CleaningFile.documents),
            selectinload(CleaningBatch.files).selectinload(
                CleaningFile.publication_shards
            ),
            selectinload(CleaningBatch.model_tasks),
        )
    )
    if batch is None:
        raise ServiceError("VALIDATION_ERROR", "清洗批次不存在", 404, False)
    payload = _serialize_batch(batch)
    cache.set_json(
        cleaning_batch_cache_key(batch_id),
        payload,
        ttl=10,
    )
    return payload


def get_cleaning_items(
    batch_id: str,
    file_id: str,
    *,
    status: str | None,
    cursor: str | None,
    limit: int,
    session: Session,
) -> dict[str, Any]:
    file_row = session.scalar(
        select(CleaningFile)
        .where(CleaningFile.id == file_id, CleaningFile.batch_id == batch_id)
        .options(selectinload(CleaningFile.documents))
    )
    if file_row is None:
        raise ServiceError("VALIDATION_ERROR", "清洗文件不存在", 404, False)
    items = sorted(file_row.documents, key=lambda item: (item.sequence, item.id))
    if status:
        items = [item for item in items if item.status == status]
    start = 0
    if cursor:
        for index, item in enumerate(items):
            if item.id == cursor:
                start = index + 1
                break
    page = items[start : start + limit]
    next_cursor = page[-1].id if start + limit < len(items) and page else None
    return {
        "file_id": file_id,
        "items": [_serialize_item(item) for item in page],
        "next_cursor": next_cursor,
        "total": len(items),
    }


def get_cleaning_item(batch_id: str, file_id: str, item_id: str, session: Session) -> dict[str, Any]:
    row = session.scalar(
        select(CleaningDocument)
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningDocument.id == item_id,
            CleaningDocument.file_id == file_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    if row is None:
        raise ServiceError("VALIDATION_ERROR", "清洗条目不存在", 404, False)
    payload = _serialize_item(row)
    payload["cleaned_markdown"] = row.cleaned_markdown
    payload["audit"] = row.audit_json or {}
    return payload


@router.post("/cleaning-batches/{batch_id}/files/{file_id}/retry")
def retry_cleaning_file(
    batch_id: str,
    file_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = session.scalar(
        select(CleaningFile).where(
            CleaningFile.id == file_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    if row is None:
        raise ServiceError("VALIDATION_ERROR", "清洗文件不存在", 404, False)
    review_required_documents = [item for item in row.documents if item.status == "review_required"]
    retryable_documents = [item for item in row.documents if item.status in ITEM_RETRYABLE_STATUSES]
    if review_required_documents and not retryable_documents and row.status in {"review_required", "mixed"}:
        raise ServiceError("VALIDATION_ERROR", "待复核条目不能直接重试，请先执行人工复核", 409, False)
    if row.status != "failed" and not retryable_documents:
        raise ServiceError("VALIDATION_ERROR", "当前文件没有失败条目可重试", 409, False)
    target = get_settings().upload_root.resolve() / batch_id / row.storage_filename
    if not target.exists() and not retryable_documents:
        raise ServiceError("CLEANING_FAILED", "失败文件已经过期，无法重试", 410, False)
    row.status = "pending"
    row.outcome = "pending"
    row.stage = "cleaning" if not retryable_documents else "retrying_items"
    row.error_code = None
    row.error_message = None
    for item in retryable_documents:
        item.status = "pending"
        item.stage = "pending"
        item.error_code = None
        item.error_message = None
        item.retryable = True
    session.commit()
    cache.delete(cleaning_batch_cache_key(batch_id))
    if retryable_documents:
        for item in retryable_documents:
            _enqueue_cleaning_document(item.id, row.original_filename)
    else:
        _enqueue_cleaning_file(file_id, row.original_filename)
    record_event(
        level="INFO",
        category="operation",
        component="cleaning",
        event_code="CLEANING_FILE_RETRIED",
        status="accepted",
        message="失败清洗文件已重新提交",
        resource_type="cleaning_file",
        resource_id=file_id,
        details={"batch_id": batch_id},
    )
    return {"file_id": file_id, "status": "pending"}


def retry_cleaning_item(batch_id: str, file_id: str, item_id: str, session: Session) -> dict[str, Any]:
    row = session.scalar(
        select(CleaningDocument)
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningDocument.id == item_id,
            CleaningDocument.file_id == file_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    if row is None:
        raise ServiceError("VALIDATION_ERROR", "清洗条目不存在", 404, False)
    if row.status not in ITEM_RETRYABLE_STATUSES:
        raise ServiceError("VALIDATION_ERROR", "只有失败条目可以重试；待复核条目请先执行人工复核", 409, False)
    row.status = "pending"
    row.stage = "pending"
    row.error_code = None
    row.error_message = None
    row.retryable = True
    session.commit()
    cache.delete(cleaning_batch_cache_key(batch_id))
    _enqueue_cleaning_document(item_id, row.file.original_filename)
    return {"item_id": item_id, "status": "pending"}


def retry_model_contract_item(
    batch_id: str,
    task_id: str,
    item_id: str,
    session: Session,
) -> dict[str, Any]:
    """Requeue one terminal realtime model-contract failure.

    A model-contract failure is not a cleaning-file failure: rerunning the
    publication task would skip the model and could publish the pre-model
    Markdown.  Keep this recovery path deliberately narrow so it cannot
    reset arbitrary human-review results or turn the production Batch switch
    on.  The original consultation batch has one candidate, which is the
    only scope accepted here.
    """

    task = session.scalar(
        select(ModelCleaningTask).where(
            ModelCleaningTask.id == task_id,
            ModelCleaningTask.batch_id == batch_id,
        )
    )
    document = session.scalar(
        select(CleaningDocument)
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningDocument.id == item_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    batch = session.get(CleaningBatch, batch_id)
    if task is None or document is None or batch is None:
        raise ValueError("模型重试目标不存在")
    if task.mode != "realtime":
        raise ValueError("仅允许重试 realtime 模型任务，避免意外产生 Batch 调用")
    if task.status != "completed":
        raise ValueError("模型任务尚未进入可重试的终态")
    if task.candidate_count != 1:
        raise ValueError("仅允许对单候选模型任务执行精确重试")
    if document.status not in {"failed", "review_required"} or document.error_code not in {
        "MODEL_CONTRACT_INVALID",
        "MODEL_NO_RESPONSE",
    }:
        raise ValueError("只有模型契约或模型无响应失败条目可以精确重试")

    previous_document_state = (
        document.status,
        document.stage,
        document.error_code,
        document.error_message,
        document.retryable,
    )
    previous_task_status = task.status
    previous_task_counts = dict(task.result_counts or {})
    previous_task_usage = dict(task.actual_usage or {})
    previous_batch_status = batch.status

    document.status = "awaiting_model"
    document.stage = "model_retry"
    document.error_code = None
    document.error_message = None
    document.retryable = False
    task.status = "queued"
    task.result_counts = {}
    task.actual_usage = {}
    task.last_error = ""
    batch.status = "model_validating"
    session.commit()
    cache.delete(cleaning_batch_cache_key(batch_id))

    from .workers.tasks.model_cleaning import process_realtime_model_task

    try:
        process_realtime_model_task.apply_async(
            args=[task.id], queue="ai_model_realtime"
        )
    except Exception as exc:
        document.status, document.stage, document.error_code, document.error_message, document.retryable = previous_document_state
        task.status = previous_task_status
        task.result_counts = previous_task_counts
        task.actual_usage = previous_task_usage
        batch.status = previous_batch_status
        session.commit()
        cache.delete(cleaning_batch_cache_key(batch_id))
        raise ServiceError(
            "TASK_QUEUE_UNAVAILABLE",
            "模型精确重试提交失败，状态已恢复",
            503,
            True,
        ) from exc
    return {"task_id": task.id, "item_id": document.id, "status": task.status}


def review_cleaning_item(
    batch_id: str,
    file_id: str,
    item_id: str,
    *,
    action: str,
    expected_revision: int,
    reason: str,
    metadata_patch: dict[str, Any],
    markdown_text: str | None,
    session: Session,
) -> dict[str, Any]:
    row = session.scalar(
        select(CleaningDocument)
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningDocument.id == item_id,
            CleaningDocument.file_id == file_id,
            CleaningFile.batch_id == batch_id,
        )
    )
    if row is None:
        raise ServiceError("VALIDATION_ERROR", "清洗条目不存在", 404, False)
    if row.review_revision != expected_revision:
        raise ServiceError("CONFLICT", "条目版本已变化，请刷新后重试", 409, False)
    if set(metadata_patch) - _ARTICLE_METADATA_FIELDS - {"record_type", "book_id", "part_index", "part_count", "case_id"}:
        raise ServiceError("VALIDATION_ERROR", "metadata_patch 包含未允许字段", 422, False)
    row.review_revision += 1
    audit = dict(row.audit_json or {})
    review_log = list(audit.get("review_log") or [])
    review_log.append({"action": action, "reason": reason, "at": utcnow().isoformat()})
    audit["review_log"] = review_log
    row.audit_json = audit
    if action == "exclude":
        row.status = "excluded"
        row.stage = "reviewed"
        row.retryable = False
        row.error_code = None
        row.error_message = None
    else:
        if action == "replace_and_approve":
            if not markdown_text or not markdown_text.strip():
                raise ServiceError("VALIDATION_ERROR", "替换批准必须提交非空 Markdown", 422, False)
            row.cleaned_markdown = finalize_knowledge_markdown(
                markdown_text.strip(),
                audit=audit,
            )
            row.metadata_json = {**(row.metadata_json or {}), **metadata_patch}
        elif metadata_patch:
            row.metadata_json = {**(row.metadata_json or {}), **metadata_patch}
        if row.cleaned_markdown.strip():
            row.cleaned_markdown = finalize_knowledge_markdown(
                row.cleaned_markdown,
                audit=audit,
            )
            from .workers.tasks.cleaning import _content_sha

            row.content_sha256 = _content_sha(
                row.cleaned_markdown,
                row.metadata_json or {},
                row.chunking_json or {},
            )
        is_post_strategy = row.file.batch.strategy_code in {
            "wechat_public_account_cognition",
            "zsxq_post",
            "xiaobot_post",
        }
        row.status = "shard_pending" if is_post_strategy else "pending"
        row.stage = "shard_pending" if is_post_strategy else "pending"
        row.retryable = True
        row.error_code = None
        row.error_message = None
    from .tasks import _finish_batch, _refresh_file_state

    _refresh_file_state(session, row.file_id, commit=False)
    _finish_batch(session, row.file.batch_id)
    session.commit()
    if action != "exclude":
        if row.file.batch.strategy_code in {
            "wechat_public_account_cognition",
            "zsxq_post",
            "xiaobot_post",
        }:
            # Article strategies publish one merged Dify shard per source
            # file.  Manual approval must re-enter that same planner instead
            # of sending the reviewed item through regular one-document
            # publication.
            from .workers.tasks.model_cleaning import (
                _plan_post_publication,
                publish_dify_shard,
            )

            shard_ids = _plan_post_publication(session, row.file.batch_id)
            for shard_id in shard_ids:
                publish_dify_shard.apply_async(
                    args=[shard_id], queue="ai_publication"
                )
        else:
            _enqueue_cleaning_document(item_id, row.file.original_filename)
    return {"item_id": item_id, "status": row.status, "review_revision": row.review_revision}
