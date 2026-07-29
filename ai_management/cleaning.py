from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path, PurePath
from typing import Any, Protocol

from docx import Document
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from knowledge_cleaner.chunking import (
    DIFY_CHILD_SEPARATOR,
    DIFY_PARENT_SEPARATOR,
    prepare_chunks,
    render_hierarchical_dify_text,
)
from knowledge_cleaner.dify import DifyKnowledgeClient
from knowledge_cleaner.semantics import SemanticDecision, classify_text
from knowledge_cleaner.sources import parse_markdown_posts, parse_wechat_public_account

from .cache import cache
from .config import get_settings
from .db import SessionLocal, get_session
from .errors import ServiceError
from .models import CleaningBatch, CleaningDocument, CleaningFile, utcnow
from .security import require_admin_token, require_internal_token
from .services.observability import record_event

router = APIRouter(
    prefix="/internal/v1/knowledge",
    tags=["knowledge-cleaning"],
    dependencies=[Depends(require_internal_token), Depends(require_admin_token)],
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
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name.lower() if paragraph.style else ""
        if "heading" in style:
            match = re.search(r"(\d+)", style)
            level = min(int(match.group(1)), 6) if match else 2
            parts.append(f"{'#' * level} {text}")
        else:
            parts.append(text)
    for table in document.tables:
        rows = [[cell.text.strip().replace("|", "\\|") for cell in row.cells] for row in table.rows]
        if not rows:
            continue
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        parts.append("| " + " | ".join(rows[0]) + " |")
        parts.append("| " + " | ".join(["---"] * width) + " |")
        parts.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n\n".join(parts).strip()


def _read_text_markdown(path: Path, suffix: str) -> str:
    if suffix == ".docx":
        markdown = _docx_markdown(path)
    else:
        encoding = "gb18030" if suffix == ".txt" else "utf-8-sig"
        markdown = path.read_text(encoding=encoding)
    return markdown.replace("\r\n", "\n").replace("\r", "\n").strip()


def _read_pdf_markdown(path: Path) -> str:
    from knowledge_cleaner.pdf_convert import convert_pdf

    result = convert_pdf(path, enable_ocr=False)
    if result.needs_review or not result.markdown.strip():
        reason = "；".join(result.review_reasons[:3]) or "没有可提取正文"
        raise ValueError(f"PDF不支持清洗入库：{reason}")
    return result.markdown.strip()


def _load_markdown_like(path: Path, original_filename: str) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        markdown = _read_pdf_markdown(path)
    else:
        markdown = _read_text_markdown(path, suffix)
    title = PurePath(original_filename).stem.strip() or "未命名文档"
    if not markdown.lstrip().startswith("#"):
        markdown = f"# {title}\n\n{markdown}"
    return title, markdown


class TestDocumentStrategy:
    code = "test_markdown"
    display_name = "通用测试文档"
    supported_extensions = {".md", ".markdown", ".txt", ".docx"}
    sensitive = False

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        title, markdown = _load_markdown_like(file_path, original_filename)
        if not markdown.strip():
            raise ValueError("文档没有可入库正文")
        return [
            CleanedDocument(
                markdown=markdown,
                title=title,
                metadata={"document_type": "test_document", "review_status": "approved"},
            )
        ]


class StandardDocumentStrategy:
    """Safe general-purpose cleaner for the two fixed knowledge bases."""

    code = "standard_document"
    display_name = "标准文档清洗"
    supported_extensions = {".md", ".markdown", ".txt", ".docx", ".pdf"}
    sensitive = False

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        title, markdown = _load_markdown_like(file_path, original_filename)
        if not markdown.strip():
            raise ValueError("文档没有可入库正文")
        return [
            CleanedDocument(
                markdown=markdown,
                title=title,
                metadata={"document_type": "standard_document", "review_status": "approved"},
            )
        ]


class PersonalBusinessConsultationStrategy:
    code = "personal_business_consultation"
    display_name = "个人商业咨询案例总结"
    supported_extensions = {".pdf"}
    sensitive = True
    knowledge_base_code = "general"
    dify_metadata_fields = frozenset({"record_type", "topic", "source_title", "case_id"})

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from knowledge_cleaner.pdf_convert import convert_consultation_pdf

        # OCR and image content recognition are explicitly out of scope.  A
        # scanned PDF must fail visibly instead of silently producing guesses.
        result = convert_consultation_pdf(file_path, enable_ocr=False)
        if result.needs_review or result.residual_pii:
            raise ValueError("隐私或版面检查未通过，需要人工处理后重试")
        if not result.markdown.strip():
            raise ValueError("PDF没有提取到可入库正文")
        return [
            CleanedDocument(
                markdown=result.markdown,
                title=f"匿名商业咨询案例 {result.document_id[:12]}",
                metadata={
                    "record_type": "consulting_case",
                    "topic": "产品与客户",
                    "source_title": f"匿名商业咨询案例 {result.document_id[:12]}",
                    "case_id": result.document_id[:16],
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
                audit={"privacy_level": "private_anonymized"},
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
            body = unit.body.strip()
            if not body or "body_candidate_conflict" in unit.warnings:
                continue
            if DIFY_PARENT_SEPARATOR in body or DIFY_CHILD_SEPARATOR in body:
                raise ValueError(f"文章 {unit.sequence} 正文与Dify分隔符碰撞")

            decision = classify_text(unit.title, body)
            if decision.disposition != "keep":
                continue
            fingerprint = hashlib.sha256(
                re.sub(r"\s+", "", body).lower().encode("utf-8")
            ).hexdigest()
            if fingerprint in seen_bodies:
                continue
            seen_bodies.add(fingerprint)

            topic = _advisor_topic(decision)
            if not topic:
                documents.append(
                    CleanedDocument(
                        markdown=body,
                        title=unit.title,
                        metadata={
                            "record_type": "article",
                            "source_title": unit.title,
                        },
                        chunking=_article_chunking(),
                        disposition="review_required",
                        error_code="TOPIC_REVIEW_REQUIRED",
                        error_message="主题无法稳定归入受控词表，需要人工确认",
                        audit={"warnings": list(unit.warnings)},
                    )
                )
                continue
            source_body = body
            if decision.time_sensitive and unit.published_at:
                attribution = unit.author or "该公众号"
                source_body = (
                    f"> 来源语境：{attribution}于{unit.published_at}发布。"
                    "以下为当时观点，不代表当前状态。\n\n"
                    f"{body}"
                )
            chunks = prepare_chunks(
                source_body,
                title=unit.title,
                content_type="article",
                topics=(topic,),
                overlap=0,
            )
            markdown = render_hierarchical_dify_text(chunks)
            if not markdown:
                continue
            documents.append(
                CleanedDocument(
                    markdown=markdown,
                    title=unit.title,
                    metadata={
                        "record_type": "article",
                        "topic": topic,
                        "source_title": unit.title,
                    },
                    chunking=_article_chunking(),
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
) -> CleanedDocument:
    decision = classify_text(title, body)
    topic = _advisor_topic(decision) if decision.disposition == "keep" else ""
    chunks = prepare_chunks(
        body,
        title=title,
        content_type=record_type,
        topics=(topic,) if topic else (),
        overlap=0,
    )
    markdown = render_hierarchical_dify_text(chunks) if chunks else body.strip()
    disposition = {
        "keep": "pending",
        "review": "review_required",
        "content_missing": "excluded",
    }.get(decision.disposition, "excluded")
    if disposition == "pending" and not topic:
        disposition = "review_required"
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
        error_code=None if disposition == "pending" else ("TOPIC_REVIEW_REQUIRED" if not topic else decision.disposition.upper()),
        error_message=None if disposition == "pending" else ("主题无法稳定归入受控词表，需要人工确认" if not topic else decision.reason),
        audit={
            "semantic_disposition": decision.disposition,
            "semantic_reason": decision.reason,
            "semantic_topics": list(decision.topics),
            "confidence": decision.confidence,
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
        units = parse_markdown_posts(file_path)
        documents = [
            _build_article_document(title=unit.title, body=unit.body, child_max_tokens=800)
            for unit in units
            if unit.body.strip()
        ]
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
        units = parse_markdown_posts(file_path)
        documents = [
            _build_article_document(title=unit.title, body=unit.body)
            for unit in units
            if unit.body.strip()
        ]
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
        title, markdown = _load_markdown_like(file_path, original_filename)
        document = _build_article_document(
            title=title,
            body=markdown,
            parent_max_tokens=1200,
            child_max_tokens=350,
        )
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
        title, markdown = _load_markdown_like(file_path, original_filename)
        parts = _split_book_markdown(markdown, get_settings().cleaning_markdown_split_bytes)
        if not parts:
            raise ValueError(f"{original_filename} 没有可清洗正文")
        semantic = classify_text(title, markdown, hinted_type="book")
        metadata_topic = _advisor_topic(semantic) if semantic.disposition == "keep" else ""
        book_id = hashlib.sha256(f"{original_filename}:{title}".encode("utf-8")).hexdigest()[:16]
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
                    disposition="pending" if metadata_topic else "review_required",
                    error_code=None if metadata_topic else "TOPIC_REVIEW_REQUIRED",
                    error_message=None if metadata_topic else "书籍主题无法稳定归入受控词表，需要人工确认",
                    audit={"split_parts": total},
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

        text = _read_text_markdown(file_path, file_path.suffix.lower())
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
                audit={"catalog_record": item.model_dump(mode="json")},
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

        text = _read_text_markdown(file_path, file_path.suffix.lower())
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
                audit={"catalog_record": item.model_dump(mode="json")},
            )
            for item in routes
        ]


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
    )
}

SYSTEM_METADATA_FIELDS = {
    "source_file_id",
    "source_filename",
    "source_extension",
    "source_sha256",
    "upload_batch_id",
    "cleaning_strategy",
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
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise ValueError("DOCX内部结构无效")
        except zipfile.BadZipFile as exc:
            raise ValueError("DOCX不是有效ZIP文档") from exc
        return
    if suffix in {".md", ".markdown", ".txt"}:
        with path.open("rb") as handle:
            sample = handle.read(64 * 1024)
        if b"\x00" in sample:
            raise ValueError("文本文件包含二进制空字节")
        encodings = ("utf-8-sig", "gb18030") if suffix == ".txt" else ("utf-8-sig",)
        for encoding in encodings:
            try:
                sample.decode(encoding, errors="strict")
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


def dify_document_name(original_filename: str, sha256: str, sequence: int) -> str:
    stem = PurePath(original_filename).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._") or "未命名"
    if len(stem) > 60:
        stem = f"{stem[:40]}…{stem[-19:]}"
    return f"{stem}__{sha256[:8]}__{sequence}.md"


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
        "warnings": item.warnings_json or [],
        "metadata": item.metadata_json or {},
        "chunking": item.chunking_json or {},
        "dify_document_id": item.dify_document_id,
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
        "succeeded": sum(item.status == "succeeded" for item in items),
        "excluded": sum(item.status == "excluded" for item in items),
        "review_required": sum(item.status == "review_required" for item in items),
        "failed": sum(item.status == "failed" for item in items),
    }
    terminal = status_counts["succeeded"] + status_counts["excluded"] + status_counts["review_required"] + status_counts["failed"]
    if item_total == 0:
        outcome = file_row.outcome if file_row.outcome and file_row.outcome != "pending" else file_row.status
    elif terminal < item_total or status_counts["processing"] or status_counts["pending"]:
        outcome = "processing"
    elif status_counts["succeeded"] == item_total:
        outcome = "succeeded"
    elif status_counts["excluded"] == item_total:
        outcome = "excluded"
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
        "succeeded": sum(item["item_counts"]["succeeded"] for item in files),
        "excluded": sum(item["item_counts"]["excluded"] for item in files),
        "review_required": sum(item["item_counts"]["review_required"] for item in files),
        "failed": sum(item["item_counts"]["failed"] for item in files),
    }
    terminal_items = item_counts["succeeded"] + item_counts["excluded"] + item_counts["review_required"] + item_counts["failed"]
    status = "completed" if file_total and non_terminal_files == 0 and terminal_items == total_items else "processing"
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
        "files": files,
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


def _remote_source_name(document: dict[str, Any]) -> str:
    metadata = _remote_metadata(document)
    details = document.get("data_source_info") or document.get("data_source_detail_dict") or {}
    return str(
        metadata.get("source_filename")
        or document.get("name")
        or details.get("upload_file")
        or details.get("file_name")
        or ""
    )


def _preflight_remote_duplicates(
    *,
    knowledge_base_code: str,
    rows: list[CleaningFile],
) -> None:
    """Apply the documented duplicate rules before any Worker task is queued."""
    settings = get_settings()
    dataset_id = (
        settings.dify_general_dataset_id
        if knowledge_base_code == "general"
        else settings.dify_course_dataset_id
    )
    if not settings.dify_knowledge_api_key or not dataset_id:
        if settings.app_env == "test":
            return
        raise ServiceError("DIFY_UNAVAILABLE", "知识库服务尚未配置", 503, True)
    client = DifyKnowledgeClient(
        settings.knowledge_base_url,
        settings.dify_knowledge_api_key,
        dataset_id,
        timeout=60,
    )
    try:
        remote_by_name: dict[str, list[dict[str, Any]]] = {}
        for document in client.iter_documents():
            remote_by_name.setdefault(_remote_source_name(document), []).append(document)
        for row in rows:
            same_name = remote_by_name.get(row.original_filename, [])
            if len(same_name) > 1:
                raise ServiceError(
                    "CONFLICT",
                    f"Dify 中存在多个同名文档：{row.original_filename}，请先由管理员处理",
                    409,
                    False,
                )
            if not same_name:
                continue
            existing = same_name[0]
            existing_hash = _remote_metadata(existing).get("source_sha256", "")
            if existing_hash and hmac.compare_digest(existing_hash, row.source_sha256):
                row.status = "succeeded"
                row.outcome = "excluded"
                row.stage = "duplicate_noop"
                row.error_code = None
                row.error_message = "同名且内容未变化，未重复索引"
            else:
                remote_id = str(existing.get("id") or "")
                if not remote_id:
                    raise ServiceError("DIFY_UNAVAILABLE", "Dify 文档缺少 ID，无法安全替换", 502, True)
                row.replace_document_id = remote_id
                row.stage = "replace_pending"
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("DIFY_UNAVAILABLE", "知识库重复校验失败，已停止上传", 502, True) from exc
    finally:
        client.close()


@router.get("/cleaning-strategies")
def cleaning_strategies() -> list[dict[str, Any]]:
    return [
        {
            "code": item.code,
            "display_name": item.display_name,
            "supported_extensions": sorted(item.supported_extensions),
            "knowledge_base_code": getattr(item, "knowledge_base_code", None),
        }
        for item in STRATEGY_REGISTRY.values()
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
        common_metadata=parsed_metadata,
        build_version=settings.build_version,
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
        _preflight_remote_duplicates(
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
            from .tasks import process_cleaning_file

            process_cleaning_file.apply_async(args=[file_id], queue="ai_cleaning")
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
            selectinload(CleaningBatch.files).selectinload(CleaningFile.documents)
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
        from .tasks import process_cleaning_document

        for item in retryable_documents:
            process_cleaning_document.apply_async(args=[item.id], queue="ai_cleaning")
    else:
        from .tasks import process_cleaning_file

        process_cleaning_file.apply_async(args=[file_id], queue="ai_cleaning")
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
    from .tasks import process_cleaning_document

    process_cleaning_document.apply_async(args=[item_id], queue="ai_cleaning")
    return {"item_id": item_id, "status": "pending"}


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
            row.cleaned_markdown = markdown_text.strip()
            row.metadata_json = {**(row.metadata_json or {}), **metadata_patch}
        elif metadata_patch:
            row.metadata_json = {**(row.metadata_json or {}), **metadata_patch}
        row.status = "pending"
        row.stage = "pending"
        row.retryable = True
        row.error_code = None
        row.error_message = None
    from .tasks import _finish_batch

    _finish_batch(session, row.file.batch_id)
    session.commit()
    if action != "exclude":
        from .tasks import process_cleaning_document

        process_cleaning_document.apply_async(args=[item_id], queue="ai_cleaning")
    return {"item_id": item_id, "status": row.status, "review_revision": row.review_revision}
