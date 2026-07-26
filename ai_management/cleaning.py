from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Protocol

from docx import Document
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .cache import cache
from .config import get_settings
from .db import get_session
from .errors import ServiceError
from .models import CleaningBatch, CleaningFile
from .security import require_internal_token

router = APIRouter(
    prefix="/internal/v1/knowledge",
    tags=["knowledge-cleaning"],
    dependencies=[Depends(require_internal_token)],
)


@dataclass(slots=True)
class CleanedDocument:
    markdown: str
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_separator: str = "\n## "
    parent_max_tokens: int = 1000
    child_separator: str = "\n\n"
    child_max_tokens: int = 200


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


class TestDocumentStrategy:
    code = "test_markdown"
    display_name = "通用测试文档"
    supported_extensions = {".md", ".markdown", ".txt", ".docx"}
    sensitive = False

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        suffix = file_path.suffix.lower()
        if suffix == ".docx":
            markdown = _docx_markdown(file_path)
        else:
            markdown = file_path.read_text(encoding="utf-8-sig")
        markdown = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not markdown:
            raise ValueError("文档没有可入库正文")
        title = PurePath(original_filename).stem.strip() or "未命名文档"
        if not markdown.lstrip().startswith("#"):
            markdown = f"# {title}\n\n{markdown}"
        return [
            CleanedDocument(
                markdown=markdown,
                title=title,
                metadata={"document_type": "test_document", "review_status": "approved"},
            )
        ]


class PersonalBusinessConsultationStrategy:
    code = "personal_business_consultation"
    display_name = "个人商业咨询案例总结"
    supported_extensions = {".pdf"}
    sensitive = True

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from knowledge_cleaner.pdf_convert import convert_consultation_pdf

        result = convert_consultation_pdf(file_path, enable_ocr=True)
        if result.needs_review or result.residual_pii:
            raise ValueError("隐私或版面检查未通过，需要人工处理后重试")
        if not result.markdown.strip():
            raise ValueError("PDF没有提取到可入库正文")
        return [
            CleanedDocument(
                markdown=result.markdown,
                title=f"匿名商业咨询案例 {result.document_id[:12]}",
                metadata={
                    "document_type": "business_consultation",
                    "review_status": "approved",
                    "privacy_level": "private_anonymized",
                },
            )
        ]


STRATEGY_REGISTRY: dict[str, CleaningStrategy] = {
    strategy.code: strategy
    for strategy in (
        TestDocumentStrategy(),
        PersonalBusinessConsultationStrategy(),
    )
}

SYSTEM_METADATA_FIELDS = {
    "source_file_id",
    "source_filename",
    "source_extension",
    "source_sha256",
    "upload_batch_id",
    "cleaning_strategy",
    "document_type",
    "review_status",
    "course_id",
    "route_id",
    "direction",
}
ALLOWED_COMMON_METADATA = {"document_type", "review_status", "course_id", "route_id", "direction"}


def cleaning_batch_cache_key(batch_id: str) -> str:
    return f"ai:cleaning:batch:{batch_id}"


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


def _serialize_batch(batch: CleaningBatch) -> dict[str, Any]:
    files = list(batch.files)
    succeeded = sum(item.status == "succeeded" for item in files)
    failed = sum(item.status == "failed" for item in files)
    terminal = succeeded + failed
    status = "completed" if terminal == len(files) else "processing"
    return {
        "batch_id": batch.id,
        "strategy_code": batch.strategy_code,
        "knowledge_base_code": batch.knowledge_base_code,
        "status": status,
        "all_succeeded": bool(files) and succeeded == len(files),
        "total": len(files),
        "succeeded": succeeded,
        "failed": failed,
        "files": [
            {
                "file_id": item.id,
                "original_filename": item.original_filename,
                "status": item.status,
                "stage": item.stage,
                "error_code": item.error_code,
                "error_message": item.error_message,
                "updated_at": item.updated_at,
            }
            for item in files
        ],
    }


@router.get("/cleaning-strategies")
def cleaning_strategies() -> list[dict[str, Any]]:
    return [
        {
            "code": item.code,
            "display_name": item.display_name,
            "supported_extensions": sorted(item.supported_extensions),
        }
        for item in STRATEGY_REGISTRY.values()
    ]


@router.post("/cleaning-batches")
async def create_cleaning_batch(
    strategy_code: str = Form(...),
    knowledge_base_code: str = Form(...),
    common_metadata: str = Form(default="{}"),
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    settings = get_settings()
    strategy = STRATEGY_REGISTRY.get(strategy_code)
    if strategy is None:
        raise ServiceError("VALIDATION_ERROR", "未注册的清洗策略", 422, False)
    if knowledge_base_code not in {"general", "course"}:
        raise ServiceError("VALIDATION_ERROR", "目标知识库代码无效", 422, False)
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
    parsed_metadata = {
        key: value for key, value in parsed_metadata.items() if key in ALLOWED_COMMON_METADATA
    }

    batch = CleaningBatch(
        strategy_code=strategy_code,
        knowledge_base_code=knowledge_base_code,
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
        row = CleaningFile(
            batch_id=batch.id,
            original_filename=original,
            storage_filename="",
            extension=suffix,
            source_sha256="",
            size_bytes=0,
            status="pending",
            stage="upload",
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
        elif suffix not in strategy.supported_extensions:
            row.status = "failed"
            row.error_code = "UNSUPPORTED_FILE_TYPE"
            row.error_message = f"策略 {strategy_code} 不支持 {suffix or '无扩展名'}"
            target.unlink(missing_ok=True)
        else:
            pending_ids.append(row.id)

    if not pending_ids:
        batch.status = "completed"
    session.commit()
    for file_id in pending_ids:
        try:
            from .tasks import process_cleaning_file

            process_cleaning_file.apply_async(args=[file_id], queue="cleaning")
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
    return _serialize_batch(loaded)


@router.get("/cleaning-batches/{batch_id}")
def get_cleaning_batch(batch_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    cached = cache.get_json(cleaning_batch_cache_key(batch_id))
    if isinstance(cached, dict):
        return cached
    batch = session.scalar(
        select(CleaningBatch)
        .where(CleaningBatch.id == batch_id)
        .options(selectinload(CleaningBatch.files))
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
    if row.status != "failed":
        raise ServiceError("VALIDATION_ERROR", "只允许重试失败文件", 409, False)
    target = get_settings().upload_root.resolve() / batch_id / row.storage_filename
    if not target.exists():
        raise ServiceError("CLEANING_FAILED", "失败文件已经过期，无法重试", 410, False)
    row.status = "pending"
    row.stage = "cleaning"
    row.error_code = None
    row.error_message = None
    session.commit()
    cache.delete(cleaning_batch_cache_key(batch_id))
    from .tasks import process_cleaning_file

    process_cleaning_file.apply_async(args=[file_id], queue="cleaning")
    return {"file_id": file_id, "status": "pending"}
