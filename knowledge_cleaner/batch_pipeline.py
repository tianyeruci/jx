from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .chunking import PreparedChunk, prepare_chunks, render_dify_text
from .markdown import safe_filename, write_text_atomic
from .semantics import SemanticDecision, classify_text
from .sources import ParsedSourceUnit, parse_source


class AmbiguousClassifier(Protocol):
    def classify(self, *, title: str, body: str, source_type: str) -> Any: ...


@dataclass(slots=True)
class BatchOptions:
    input_path: Path
    output_path: Path
    classifier: AmbiguousClassifier | None = None
    review_overrides: Path | None = None
    include_patterns: tuple[str, ...] = ()
    enable_ocr: bool = True


@dataclass(slots=True)
class BatchResult:
    source_files: int = 0
    parsed_documents: int = 0
    kept: int = 0
    no_index: int = 0
    review: int = 0
    duplicate: int = 0
    content_missing: int = 0
    chunks: int = 0
    parse_errors: int = 0
    ocr_required: int = 0
    privacy_blocked: int = 0


@dataclass(slots=True)
class BatchDocument:
    doc_id: str
    title: str
    body: str
    source_path: Path
    source_type: str
    source_name: str
    collection: str
    sequence: str
    author: str = ""
    published_at: str = ""
    content_type: str = "article"
    domain_class: str = "uncertain"
    knowledge_nature: str = "opinion"
    topics: tuple[str, ...] = ()
    time_sensitive: bool = False
    authority: str = "editorial"
    privacy_level: str = "public"
    disposition: str = "review"
    reason: str = ""
    confidence: float = 0.0
    raw_sha256: str = ""
    clean_sha256: str = ""
    duplicate_of: str = ""
    quality_flags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    chunks: list[PreparedChunk] = field(default_factory=list)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _clean_hash(body: str) -> str:
    normalized = re.sub(r"\s+", "", body).lower()
    return _sha256_bytes(normalized.encode("utf-8"))


def _stable_id(root: Path, unit: ParsedSourceUnit) -> str:
    try:
        relative = unit.source_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = unit.source_path.name
    identity = "\0".join((unit.source_type, relative, unit.sequence, unit.title.strip()))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _public_source_name(path: Path, source_type: str, raw_hash: str) -> str:
    if source_type == "consultation_pdf":
        return f"consultation_{raw_hash[:12]}.pdf"
    return path.name


def _decision_from_model(value: Any, fallback: SemanticDecision) -> SemanticDecision:
    if hasattr(value, "to_semantic_decision"):
        return value.to_semantic_decision()
    outcome_decision = str(getattr(value, "decision", ""))
    result = getattr(value, "result", None)
    if result is not None:
        data = {
            name: getattr(result, name)
            for name in (
                "domain_class",
                "content_type",
                "knowledge_nature",
                "topics",
                "time_sensitive",
                "quality_class",
                "evidence_spans",
                "confidence",
            )
        }
    else:
        data = value if isinstance(value, dict) else {}
    domain = str(data.get("domain_class") or "uncertain")
    quality = str(data.get("quality_class") or "uncertain")
    confidence = float(data.get("confidence") or 0)
    evidence = data.get("evidence_spans") or []
    has_evidence = bool(evidence)
    if outcome_decision in {"keep", "no_index", "review"}:
        disposition = outcome_decision
    elif (
        domain in {"business_core", "business_support"}
        and quality in {"useful", "core", "context", "evidence"}
        and confidence >= 0.85
        and has_evidence
    ):
        disposition = "keep"
    elif (
        domain in {"off_scope", "promotion_or_catalog"}
        and quality in {"off_scope", "promotion", "low_info"}
        and confidence >= 0.92
        and has_evidence
    ):
        disposition = "review" if len(str(data.get("body") or "")) < 150 else "no_index"
    else:
        disposition = "review"
    return SemanticDecision(
        disposition=disposition,
        domain_class=domain,
        content_type=str(data.get("content_type") or fallback.content_type),
        knowledge_nature=str(data.get("knowledge_nature") or fallback.knowledge_nature),
        topics=tuple(str(item) for item in (data.get("topics") or fallback.topics)[:5]),
        time_sensitive=bool(data.get("time_sensitive", fallback.time_sensitive)),
        reason=str(data.get("reason") or getattr(value, "error", "") or "模糊内容模型分类"),
        confidence=confidence,
    )


def _read_overrides(path: Path | None) -> dict[str, tuple[str, str]]:
    if not path or not path.exists():
        return {}
    result: dict[str, tuple[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            doc_id = str(row.get("doc_id") or "").strip()
            disposition = str(row.get("disposition") or "").strip()
            if doc_id and disposition in {"keep", "no_index", "review"}:
                result[doc_id] = (disposition, str(row.get("review_note") or "人工复核"))
    return result


def _render_audit(doc: BatchDocument) -> str:
    metadata = {
        "doc_id": doc.doc_id,
        "source_type": doc.source_type,
        "source_name": doc.source_name,
        "collection": doc.collection,
        "sequence": doc.sequence,
        "author": doc.author,
        "published_at": doc.published_at,
        "content_type": doc.content_type,
        "domain_class": doc.domain_class,
        "knowledge_nature": doc.knowledge_nature,
        "topics": list(doc.topics),
        "time_sensitive": doc.time_sensitive,
        "authority": doc.authority,
        "privacy_level": doc.privacy_level,
        "review_status": doc.disposition,
        "reason": doc.reason,
    }
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items()
    )
    return f"---\n{frontmatter}\n---\n\n# {doc.title}\n\n{doc.body.strip()}\n"


def _manifest_record(doc: BatchDocument, audit_path: str, dify_path: str) -> dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "source_path": str(doc.source_path),
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "collection": doc.collection,
        "sequence": doc.sequence,
        "author": doc.author,
        "published_at": doc.published_at,
        "content_type": doc.content_type,
        "domain_class": doc.domain_class,
        "knowledge_nature": doc.knowledge_nature,
        "topics": list(doc.topics),
        "time_sensitive": doc.time_sensitive,
        "authority": doc.authority,
        "privacy_level": doc.privacy_level,
        "course_id": "",
        "route_id": "",
        "review_status": doc.disposition,
        "disposition": doc.disposition,
        "reason": doc.reason,
        "confidence": doc.confidence,
        "raw_sha256": doc.raw_sha256,
        "clean_sha256": doc.clean_sha256,
        "duplicate_of": doc.duplicate_of,
        "quality_flags": doc.quality_flags,
        "audit_path": audit_path,
        "markdown_path": dify_path,
        "chunk_count": len(doc.chunks),
        "extra": doc.extra,
    }


class BatchCleaningPipeline:
    """Create an auditable, complete snapshot for all supported fixed sources."""

    def __init__(self, options: BatchOptions):
        self.options = options

    def _files(self) -> list[Path]:
        root = self.options.input_path
        candidates = (
            [root]
            if root.is_file()
            else sorted(
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in {".txt", ".md", ".markdown", ".pdf"}
            )
        )
        if not self.options.include_patterns:
            return candidates
        return [
            path
            for path in candidates
            if any(
                pattern.lower() in path.name.lower() for pattern in self.options.include_patterns
            )
        ]

    def _from_parsed(self, unit: ParsedSourceUnit, root: Path) -> BatchDocument:
        raw_hash = _sha256_bytes(unit.source_path.read_bytes())
        decision = classify_text(
            unit.title,
            unit.body,
            hinted_type="lesson" if unit.source_type == "markdown_course" else "article",
        )
        if decision.disposition == "review" and self.options.classifier is not None:
            try:
                classified = self.options.classifier.classify(
                    title=unit.title, body=unit.body, source_type=unit.source_type
                )
                decision = _decision_from_model(classified, decision)
            except Exception as exc:
                unit.warnings.append(f"classifier_failed:{type(exc).__name__}")
        return BatchDocument(
            doc_id=_stable_id(root, unit),
            title=unit.title,
            body=unit.body,
            source_path=unit.source_path,
            source_type=unit.source_type,
            source_name=_public_source_name(unit.source_path, unit.source_type, raw_hash),
            collection=unit.collection,
            sequence=unit.sequence,
            author=unit.author,
            published_at=unit.published_at,
            content_type=decision.content_type,
            domain_class=decision.domain_class,
            knowledge_nature=decision.knowledge_nature,
            topics=decision.topics,
            time_sensitive=decision.time_sensitive,
            disposition=decision.disposition,
            reason=decision.reason,
            confidence=decision.confidence,
            raw_sha256=raw_hash,
            clean_sha256=_clean_hash(unit.body),
            quality_flags=list(unit.warnings),
            extra=dict(unit.metadata),
        )

    def _parse_pdf(self, path: Path, root: Path) -> BatchDocument:
        from .pdf_convert import convert_consultation_pdf

        converted = convert_consultation_pdf(path, enable_ocr=self.options.enable_ocr)
        raw_hash = _sha256_bytes(path.read_bytes())
        unit = ParsedSourceUnit(
            title=f"匿名商业咨询案例 {raw_hash[:12]}",
            body=converted.markdown,
            source_path=path,
            source_type="markdown_post",
            sequence=raw_hash[:12],
            collection="匿名商业咨询案例",
        )
        doc = self._from_parsed(unit, root)
        doc.doc_id = converted.document_id
        doc.source_type = "consultation_pdf"
        doc.source_name = _public_source_name(path, doc.source_type, raw_hash)
        doc.content_type = "case"
        doc.domain_class = "business_core"
        doc.knowledge_nature = "experience"
        doc.authority = "consultation_case"
        doc.privacy_level = "private_anonymized"
        privacy_findings = [asdict(item) for item in converted.residual_pii]
        table_count = sum(page.table_count for page in converted.pages)
        doc.extra.update(
            {
                "conversion_backend": converted.backend,
                "ocr_used": converted.backend.startswith(("rapidocr", "paddleocr")),
                "page_count": converted.page_count,
                "table_count": table_count,
                "privacy_findings": privacy_findings,
                "redactions": [asdict(item) for item in converted.redactions],
            }
        )
        doc.quality_flags.extend(converted.review_reasons)
        if not converted.markdown and "ocr" in converted.backend:
            doc.quality_flags.append("ocr_required")
        if converted.needs_review or converted.residual_pii:
            doc.disposition = "review"
            doc.reason = "咨询材料需要完成OCR/表格或隐私人工确认"
        elif not converted.markdown.strip():
            doc.disposition = "content_missing"
            doc.reason = "PDF没有可用文字且OCR未生成正文"
        else:
            # All consultation documents require an explicit review override before publishing.
            doc.disposition = "review"
            doc.reason = "咨询材料已自动脱敏，等待人工隐私确认"
        doc.clean_sha256 = _clean_hash(doc.body)
        return doc

    @staticmethod
    def _deduplicate(documents: list[BatchDocument]) -> None:
        groups: dict[str, list[BatchDocument]] = defaultdict(list)
        for doc in documents:
            if doc.body.strip():
                groups[doc.clean_sha256].append(doc)
        for group in groups.values():
            if len(group) < 2:
                continue
            canonical = max(
                group,
                key=lambda item: (
                    not bool(re.search(r"\s*\(\d+\)$", item.source_path.stem)),
                    len(item.body),
                    -len(str(item.source_path)),
                ),
            )
            for doc in group:
                if doc is canonical:
                    continue
                doc.disposition = "duplicate"
                doc.duplicate_of = canonical.doc_id
                doc.reason = "清洗后正文与canonical文档完全相同"

    def run(self) -> BatchResult:
        source_root = (
            self.options.input_path
            if self.options.input_path.is_dir()
            else self.options.input_path.parent
        )
        files = self._files()
        result = BatchResult(source_files=len(files))
        documents: list[BatchDocument] = []
        parse_errors: list[dict[str, str]] = []
        for path in files:
            try:
                if path.suffix.lower() == ".pdf":
                    documents.append(self._parse_pdf(path, source_root))
                else:
                    documents.extend(
                        self._from_parsed(unit, source_root) for unit in parse_source(path)
                    )
            except Exception as exc:
                result.parse_errors += 1
                parse_errors.append(
                    {"source_path": str(path), "error": f"{type(exc).__name__}: {exc}"}
                )
        result.parsed_documents = len(documents)
        self._deduplicate(documents)
        overrides = _read_overrides(self.options.review_overrides)
        for doc in documents:
            if doc.doc_id in overrides:
                doc.disposition, note = overrides[doc.doc_id]
                doc.reason = note
                doc.extra["manual_override"] = True
            if doc.disposition == "keep":
                doc.chunks = prepare_chunks(
                    doc.body,
                    title=doc.title,
                    collection=doc.collection,
                    content_type=doc.content_type,
                    topics=doc.topics,
                )
                if not doc.chunks:
                    doc.disposition = "content_missing"
                    doc.reason = "未生成可用检索块"

        stage = self.options.output_path.with_name(
            f"{self.options.output_path.name}.staging-{uuid.uuid4().hex[:8]}"
        )
        stage.mkdir(parents=True, exist_ok=False)
        records: list[dict[str, Any]] = []
        chunk_records: list[dict[str, Any]] = []
        try:
            for doc in documents:
                folder = {
                    "keep": "cleaned",
                    "review": "review",
                    "no_index": "no_index",
                    "duplicate": "no_index",
                    "content_missing": "no_index",
                }.get(doc.disposition, "review")
                filename = safe_filename(f"{doc.doc_id}__{doc.title}") + ".md"
                audit_target = stage / folder / filename
                write_text_atomic(audit_target, _render_audit(doc))
                dify_relative = ""
                if doc.disposition == "keep":
                    dify_target = stage / "dify_ready" / filename
                    write_text_atomic(dify_target, render_dify_text(doc.chunks))
                    dify_relative = str(dify_target.relative_to(stage))
                    for chunk in doc.chunks:
                        chunk_records.append(
                            {
                                "doc_id": doc.doc_id,
                                "chunk_index": chunk.index,
                                "char_count": chunk.char_count,
                                "child_count": len(chunk.children),
                                "content_sha256": _sha256_bytes(chunk.content.encode("utf-8")),
                                "content_preview": chunk.content[:300],
                            }
                        )
                records.append(
                    _manifest_record(doc, str(audit_target.relative_to(stage)), dify_relative)
                )

            write_text_atomic(
                stage / "manifest.jsonl",
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
            )
            write_text_atomic(
                stage / "chunks.jsonl",
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in chunk_records),
            )
            write_text_atomic(
                stage / "parse_errors.jsonl",
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in parse_errors),
            )
            with (stage / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "doc_id",
                        "title",
                        "source_type",
                        "disposition",
                        "reason",
                        "review_note",
                    ],
                )
                writer.writeheader()
                for doc in documents:
                    if doc.disposition == "review":
                        writer.writerow(
                            {
                                "doc_id": doc.doc_id,
                                "title": doc.title,
                                "source_type": doc.source_type,
                                "disposition": "review",
                                "reason": doc.reason,
                                "review_note": "",
                            }
                        )

            counts = Counter(doc.disposition for doc in documents)
            result.kept = counts["keep"]
            result.no_index = counts["no_index"]
            result.review = counts["review"]
            result.duplicate = counts["duplicate"]
            result.content_missing = counts["content_missing"]
            result.chunks = len(chunk_records)
            result.ocr_required = sum("ocr_required" in doc.quality_flags for doc in documents)
            result.privacy_blocked = sum(
                bool(doc.extra.get("privacy_findings")) for doc in documents
            )
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "result": asdict(result),
                "by_source_type": dict(Counter(doc.source_type for doc in documents)),
                "by_disposition": dict(counts),
                "quality_flags": dict(
                    Counter(flag for doc in documents for flag in doc.quality_flags)
                ),
            }
            write_text_atomic(
                stage / "quality_report.json",
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            )

            target = self.options.output_path
            if target.exists():
                backup = target.with_name(
                    f"{target.name}.previous-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                )
                target.replace(backup)
            stage.replace(target)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        return result
