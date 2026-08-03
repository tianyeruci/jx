"""Evidence-first consultation case v2 cleaning helpers.

This module is deliberately independent from the existing asynchronous cleaning
worker.  It provides the strict JSON contract, deterministic Markdown renderer,
DOCX/PDF source extraction and shadow-migration/RAG evaluation helpers used by
the consultation migration.  The public HTTP/SSE contract is not touched.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evaluation import EvaluationCase, RetrievalMetrics, evaluate_retrieval


class FactState(StrEnum):
    CUSTOMER_FACT = "customer_fact"
    CONSULTANT_JUDGMENT = "consultant_judgment"
    DECISION = "decision"
    PLANNED_ACTION = "planned_action"
    OBSERVED_RESULT = "observed_result"
    DERIVED_CONDITION = "derived_condition"


class EvidenceLevel(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"


class BackgroundSufficiency(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CaseDisposition(StrEnum):
    KEEP = "keep_as_case"
    REVIEW = "review_required"
    EXCLUDE = "exclude"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=3000)
    status: FactState
    source_block_ids: list[str] = Field(min_length=1, max_length=100)
    # ``priority`` is meaningful for planned actions, but remains optional so
    # the same evidence item contract can be used for facts and conditions.
    priority: Literal["primary", "secondary"] | None = None

    @field_validator("source_block_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("source_block_ids必须唯一且非空")
        return value


class DecisionUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    unit_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,80}$")
    topic: str = Field(min_length=1, max_length=160)
    local_context: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    problem: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    mechanism_or_diagnosis: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    actions: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    observed_outcomes: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    result_status: str = Field(pattern=r"^(unverified|partially_verified|verified|not_applicable)$")
    stated_conditions: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    derived_conditions: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    risks_or_limits: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    evidence_level: EvidenceLevel
    retrieval_summary: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_evidence(self) -> "DecisionUnit":
        if not self.problem and not self.mechanism_or_diagnosis:
            raise ValueError("决策单元至少需要problem或mechanism_or_diagnosis")
        if not self.actions and self.evidence_level != EvidenceLevel.E0:
            raise ValueError("非E0决策单元必须包含actions")
        if self.evidence_level in {EvidenceLevel.E2, EvidenceLevel.E3} and not self.observed_outcomes:
            raise ValueError("E2/E3必须包含observed_outcomes")
        if self.evidence_level == EvidenceLevel.E3 and self.result_status != "verified":
            raise ValueError("E3必须标记为verified")
        allowed_states = {
            "local_context": {FactState.CUSTOMER_FACT, FactState.OBSERVED_RESULT},
            "problem": {FactState.CUSTOMER_FACT, FactState.CONSULTANT_JUDGMENT},
            "mechanism_or_diagnosis": {FactState.CONSULTANT_JUDGMENT},
            "actions": {FactState.PLANNED_ACTION, FactState.DECISION},
            "observed_outcomes": {FactState.OBSERVED_RESULT},
            "stated_conditions": {FactState.CUSTOMER_FACT, FactState.CONSULTANT_JUDGMENT, FactState.DERIVED_CONDITION},
            "derived_conditions": {FactState.DERIVED_CONDITION},
            "risks_or_limits": {FactState.CONSULTANT_JUDGMENT, FactState.DERIVED_CONDITION},
        }
        for name, allowed in allowed_states.items():
            if any(item.status not in allowed for item in getattr(self, name)):
                raise ValueError(f"{name}包含不匹配的事实状态")
            if name != "actions" and any(item.priority is not None for item in getattr(self, name)):
                raise ValueError(f"{name}不允许priority字段")
        return self


class SourceCoverageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    block_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)


class SourceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kept: list[SourceCoverageEntry] = Field(default_factory=list)
    dropped: list[SourceCoverageEntry] = Field(default_factory=list)
    unresolved: list[SourceCoverageEntry] = Field(default_factory=list)


class ConsultationCaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = Field(pattern=r"^consulting_case_v2$")
    case_id: str = Field(pattern=r"^case_[A-Za-z0-9_-]{4,80}$")
    case_group_id: str | None = None
    disposition: CaseDisposition
    title: str = Field(min_length=1, max_length=200)
    source_date: str = ""
    shared_context: list[EvidenceItem] = Field(default_factory=list, max_length=40)
    objectives: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    decision_units: list[DecisionUnit] = Field(default_factory=list, max_length=5)
    overall_evidence_level: EvidenceLevel
    background_sufficiency: BackgroundSufficiency
    time_sensitive: bool = False
    time_sensitive_items: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    source_coverage: SourceCoverage
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def check_case(self) -> "ConsultationCaseV2":
        if self.disposition == CaseDisposition.KEEP and (not self.shared_context or not self.decision_units):
            raise ValueError("keep_as_case必须包含共享背景和决策单元")
        if not self.time_sensitive and self.time_sensitive_items:
            raise ValueError("time_sensitive为false时不能存在time_sensitive_items")
        if self.time_sensitive and not self.time_sensitive_items:
            raise ValueError("time_sensitive为true时必须说明时效项")
        if self.overall_evidence_level in {EvidenceLevel.E2, EvidenceLevel.E3} and not any(
            unit.observed_outcomes for unit in self.decision_units
        ):
            raise ValueError("整体E2/E3必须至少包含一个已观察结果")
        if self.background_sufficiency == BackgroundSufficiency.LOW and self.disposition == CaseDisposition.KEEP:
            raise ValueError("背景充分度low不得直接keep_as_case")
        return self


@dataclass(frozen=True, slots=True)
class SourceBlock:
    block_id: str
    text: str
    page: int | None = None


@dataclass(frozen=True, slots=True)
class SourceExtraction:
    source_path: Path
    source_sha256: str
    blocks: tuple[SourceBlock, ...]
    disposition: str
    reason: str = ""
    source_date: str = ""
    privacy_findings: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


_PII_RE = (
    ("phone", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")),
    ("email", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("wechat", re.compile(r"(?i)(?:微信(?:号|ID)?|wechat)\s*[:：]?\s*[A-Za-z][-_A-Za-z0-9]{5,19}")),
    ("id_card", re.compile(r"(?<![0-9Xx])\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9Xx])")),
    ("url", re.compile(r"(?i)(?:https?://|www\.)\S+")),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_privacy(text: str) -> tuple[str, ...]:
    findings: list[str] = []
    for name, pattern in _PII_RE:
        if pattern.search(text):
            findings.append(name)
    # Never report the actual matching text.
    return tuple(findings)


def _blocks_from_text(text: str, *, page: int | None = None) -> tuple[SourceBlock, ...]:
    blocks: list[SourceBlock] = []
    current_page = page
    for raw in text.replace("\r", "").split("\n"):
        marker = re.fullmatch(r"\s*<!--\s*page:(\d+)\s*-->\s*", raw)
        if marker:
            current_page = int(marker.group(1))
            continue
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            blocks.append(SourceBlock(f"b{len(blocks) + 1:03d}", line, current_page))
    return tuple(blocks)


def extract_docx(path: str | Path) -> SourceExtraction:
    """Extract anonymised DOCX paragraphs and tables as stable source blocks."""
    source = Path(path)
    from docx import Document

    from .pdf_convert import anonymize_consultation, scan_residual_pii

    doc = Document(str(source))
    raw_parts: list[str] = []
    for paragraph in doc.paragraphs:
        value = paragraph.text.strip()
        if value:
            raw_parts.append(value)
    for table in doc.tables:
        for row in table.rows:
            values = [cell.text.strip() for cell in row.cells]
            if any(values):
                raw_parts.append(" | ".join(values))
    raw = "\n".join(raw_parts)
    anonymized, _ = anonymize_consultation(raw, source_path=source)
    residual = scan_residual_pii(anonymized)
    findings = tuple(sorted({item.category for item in residual}))
    disposition = "review_required" if findings or not anonymized.strip() else "ready"
    reason = "脱敏后仍检测到隐私信息" if findings else ("正文为空" if not anonymized.strip() else "")
    return SourceExtraction(source, _sha256(source), _blocks_from_text(anonymized), disposition, reason, privacy_findings=findings)


def extract_pdf(path: str | Path, *, enable_ocr: bool = True) -> SourceExtraction:
    """Use the existing PDF converter, while exposing an explicit review gate."""
    source = Path(path)
    from .pdf_convert import convert_consultation_pdf

    converted = convert_consultation_pdf(source, enable_ocr=enable_ocr)
    blocks = _blocks_from_text(converted.markdown)
    findings = tuple(sorted({item.category for item in converted.residual_pii}))
    warnings = tuple(str(item) for item in converted.review_reasons)
    blocking = tuple(item for item in warnings if not item.startswith("table_review:"))
    disposition = "review_required" if blocking or findings or not converted.markdown.strip() else "ready"
    reason = "；".join(blocking) if blocking else ("正文为空" if not blocks else "")
    return SourceExtraction(source, _sha256(source), blocks, disposition, reason, privacy_findings=findings, warnings=warnings)


def extract_consultation(path: str | Path, *, enable_ocr: bool = True) -> SourceExtraction:
    source = Path(path)
    if source.suffix.lower() == ".docx":
        return extract_docx(source)
    if source.suffix.lower() == ".pdf":
        return extract_pdf(source, enable_ocr=enable_ocr)
    raise ValueError("咨询案例仅支持DOCX和PDF")


def validate_case_payload(
    payload: Mapping[str, Any],
    *,
    source_blocks: Mapping[str, str] | None = None,
) -> ConsultationCaseV2:
    """Validate strict v2 JSON and enforce source coverage conservation."""
    case = ConsultationCaseV2.model_validate(dict(payload))
    def canonical_block_id(value: str) -> str:
        match = re.fullmatch(r"b(\d+)", value.strip(), re.I)
        return f"b{int(match.group(1)):03d}" if match else value.strip()

    # Existing worker payloads use ``b0001`` while the v2 optimization pack
    # uses ``b001``.  They are the same ordinal source block; normalize only
    # this mechanical spelling difference and keep unknown IDs strict.
    all_ids: set[str] = {canonical_block_id(item) for item in (source_blocks or {})}
    referenced: list[str] = []
    for item in case.shared_context + case.objectives + case.time_sensitive_items:
        referenced.extend(item.source_block_ids)
    for unit in case.decision_units:
        for collection in (
            unit.local_context, unit.problem, unit.mechanism_or_diagnosis,
            unit.actions, unit.observed_outcomes, unit.stated_conditions,
            unit.derived_conditions, unit.risks_or_limits,
        ):
            for item in collection:
                referenced.extend(item.source_block_ids)
    coverage = case.source_coverage
    covered = [canonical_block_id(item.block_id) for item in (*coverage.kept, *coverage.dropped, *coverage.unresolved)]
    if len(covered) != len(set(covered)):
        raise ValueError("source_coverage中block_id不能重复")
    if all_ids:
        if set(covered) != all_ids:
            raise ValueError("source_coverage必须完整覆盖输入来源块")
        unknown = {canonical_block_id(item) for item in referenced} - all_ids
        if unknown:
            raise ValueError(f"结构化结果引用未知来源块：{sorted(unknown)}")
    if any(item.status == FactState.DERIVED_CONDITION for item in (*case.shared_context, *case.objectives)):
        raise ValueError("shared_context/objectives不能直接使用derived_condition")
    if case.disposition == CaseDisposition.KEEP and not coverage.kept:
        raise ValueError("keep_as_case必须存在kept来源块")
    if case.disposition != CaseDisposition.KEEP and case.decision_units:
        # A review case may still retain units for human inspection, but an excluded
        # case must not accidentally enter a publish manifest.
        if case.disposition == CaseDisposition.EXCLUDE:
            raise ValueError("exclude不应携带可发布decision_units")
    return case


def _render_item(item: EvidenceItem) -> str:
    return item.text.strip()


def render_case_markdown(case: ConsultationCaseV2) -> str:
    """Render deterministic, non-empty parent/child case Markdown."""
    if case.disposition != CaseDisposition.KEEP:
        raise ValueError("只有keep_as_case可渲染知识库正文")
    lines = [
        f"# {case.title}",
        "",
        "## 共享背景与证据状态",
        "",
        "；".join(_render_item(item) for item in case.shared_context),
    ]
    if case.objectives:
        lines.extend(["", "## 咨询目标", "", "；".join(_render_item(item) for item in case.objectives)])
    lines.extend(["", f"> 证据等级：{case.overall_evidence_level.value}；背景充分度：{case.background_sufficiency.value}"])
    if case.time_sensitive_items:
        lines.extend(["", "> 时效提示：" + "；".join(_render_item(item) for item in case.time_sensitive_items)])
    for unit in case.decision_units:
        lines.extend(["", "<<<DIFY_CHILD>>>", "", f"## 决策单元：{unit.topic}", ""])
        # ``retrieval_summary`` is produced from the structured fields and is
        # intentionally the compact, deterministic representation sent to the
        # vector store.  Keep one local condition/risk when present so a child
        # chunk remains safe when returned without its parent.
        parts: list[str] = [unit.retrieval_summary.strip()]
        if unit.local_context and len(unit.retrieval_summary) < 110:
            parts.append("局部前提：" + _render_item(unit.local_context[0]))
        if unit.risks_or_limits and len(unit.retrieval_summary) < 150:
            parts.append("限制：" + _render_item(unit.risks_or_limits[0]))
        parts.append(f"证据等级：{unit.evidence_level.value}；状态：{unit.result_status}")
        lines.append("\n\n".join(parts))
    result = "\n".join(lines).strip() + "\n"
    if "\n<<<DIFY_CHILD>>>\n" not in result:
        raise ValueError("案例至少需要一个非空子块")
    if len(result) > 1200:
        raise ValueError("案例正文超过1200字硬上限，应先压缩或按case_group_id拆分")
    if re.search(r"^#{1,6}\s*$", result, re.M):
        raise ValueError("渲染结果包含空标题")
    return result


def prepare_consultation_v2(
    source_path: str | Path,
    payload: Mapping[str, Any],
    *,
    enable_ocr: bool = True,
) -> dict[str, Any]:
    """Adapter entry point for the existing cleaning worker.

    The worker can call this after its model task returns JSON.  It keeps the
    current ``CleanedDocument``/HTTP contract out of this module while
    returning the fields needed by that adapter.  A privacy or extraction gate
    always returns ``review_required`` and never a publishable placeholder.
    """
    extraction = extract_consultation(source_path, enable_ocr=enable_ocr)
    source_text = {item.block_id: item.text for item in extraction.blocks}
    if extraction.disposition != "ready":
        return {
            "disposition": "review_required",
            "error_code": "PRIVACY_REVIEW_REQUIRED" if extraction.privacy_findings else "CONSULTATION_SOURCE_REVIEW_REQUIRED",
            "reason": extraction.reason or "咨询源文件需要人工复核",
            "source_sha256": extraction.source_sha256,
            "source_blocks": sorted(source_text),
            "privacy_findings": list(extraction.privacy_findings),
        }
    try:
        case = validate_case_payload(payload, source_blocks=source_text)
        markdown = render_case_markdown(case) if case.disposition == CaseDisposition.KEEP else ""
    except Exception as exc:
        return {
            "disposition": "review_required",
            "error_code": "CONSULTATION_SCHEMA_REVIEW_REQUIRED",
            "reason": str(exc),
            "source_sha256": extraction.source_sha256,
            "source_blocks": sorted(source_text),
        }
    if case.disposition != CaseDisposition.KEEP:
        return {
            "disposition": "review_required" if case.disposition == CaseDisposition.REVIEW else "excluded",
            "reason": "模型结果标记为人工复核" if case.disposition == CaseDisposition.REVIEW else "模型结果明确排除",
            "source_sha256": extraction.source_sha256,
        }
    case_checksum = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return {
        "disposition": "awaiting_model",
        "document_id": case.case_id,
        "title": case.title,
        "markdown": markdown,
        "metadata": {
            "record_type": "consulting_case_v2",
            "case_id": case.case_id,
            "case_group_id": case.case_group_id or "",
            "topic": "；".join(unit.topic for unit in case.decision_units),
            "evidence_level": case.overall_evidence_level.value,
            "background_sufficiency": case.background_sufficiency.value,
            "time_sensitive": case.time_sensitive,
            "privacy_level": "private_anonymized",
            "source_checksum": extraction.source_sha256,
            "clean_checksum": case_checksum,
            "review_status": "awaiting_model",
        },
        "audit": {
            "schema_version": case.schema_version,
            "source_coverage": case.source_coverage.model_dump(mode="json"),
            "source_block_count": len(source_text),
            "parent_child": verify_parent_child_blocks(markdown, expected_children=len(case.decision_units)),
        },
    }


def verify_parent_child_blocks(markdown: str, *, expected_children: int | None = None) -> dict[str, Any]:
    """Verify Dify parent/child separator integrity without remote mutation."""
    text = markdown.strip()
    if not text:
        raise ValueError("正文不能为空")
    children = text.split("<<<DIFY_CHILD>>>")
    if any(not part.strip() for part in children):
        raise ValueError("父子块分隔符产生空块")
    child_count = len(children) - 1
    if child_count < 1:
        raise ValueError("缺少DIFY_CHILD子块")
    if expected_children is not None and child_count != expected_children:
        raise ValueError(f"子块数量不一致：期望{expected_children}，实际{child_count}")
    return {"parent_count": 1, "child_count": child_count, "char_count": len(text), "valid": True}


def build_shadow_manifest(
    cases: Iterable[ConsultationCaseV2],
    *,
    authorized_sources: Iterable[str | Path],
    source_paths: Mapping[str, str | Path] | None = None,
    dataset_code: str = "advisor_knowledge_shadow",
) -> dict[str, Any]:
    """Build a non-destructive shadow migration manifest.

    The manifest contains only exact source hashes/IDs.  It intentionally does
    not perform Dify writes or infer deletion candidates.
    """
    allowed = {str(Path(item).resolve()) for item in authorized_sources}
    documents: list[dict[str, Any]] = []
    for case in cases:
        source_path = source_paths.get(case.case_id) if source_paths else None
        if source_paths is not None and source_path is None:
            raise ValueError(f"案例{case.case_id}缺少精确授权源文件")
        if source_path is not None and str(Path(source_path).resolve()) not in allowed:
            raise ValueError(f"案例{case.case_id}的源文件不在授权清单")
        rendered = render_case_markdown(case)
        check = verify_parent_child_blocks(rendered, expected_children=len(case.decision_units))
        documents.append({
            "case_id": case.case_id,
            "case_group_id": case.case_group_id,
            "dataset_code": dataset_code,
            "source_authorization_hash": (
                hashlib.sha256(str(Path(source_path).resolve()).encode("utf-8")).hexdigest()[:16]
                if source_path is not None else ""
            ),
            "source_ids": sorted({entry.block_id for entry in case.source_coverage.kept}),
            "markdown_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "parent_count": check["parent_count"],
            "child_count": check["child_count"],
        })
    return {"dataset_code": dataset_code, "authorized_source_count": len(allowed), "documents": documents}


def evaluate_rerank_threshold_matrix(
    client: Any,
    cases: Iterable[EvaluationCase],
    *,
    thresholds: Sequence[float] = (0.0, 0.1, 0.2, 0.3),
    rerank_enabled: Sequence[bool] = (False, True),
    k: int = 5,
) -> dict[str, Any]:
    """Run the fixed A/B matrix and return every result, including failures."""
    case_list = list(cases)
    if not case_list:
        raise ValueError("评测集不能为空")
    matrix: list[dict[str, Any]] = []
    for enabled in rerank_enabled:
        for threshold in thresholds:
            metrics: RetrievalMetrics = evaluate_retrieval(
                client,
                case_list,
                k=k,
                search_method="hybrid_search",
                score_threshold=threshold,
                reranking={"reranking_enable": True} if enabled else None,
            )
            matrix.append({
                "reranking_enabled": bool(enabled),
                "score_threshold": float(threshold),
                "recall_at_k": metrics.recall_at_k,
                "hit_rate_at_k": metrics.hit_rate_at_k,
                "mrr": metrics.mrr,
                "failed": metrics.failed,
                "total": metrics.total,
                "cases": [
                    {"query": item.query, "recall": item.recall, "error": item.error}
                    for item in metrics.cases
                ],
            })
    return {"k": k, "thresholds": list(thresholds), "rerank_enabled": list(rerank_enabled), "matrix": matrix}


__all__ = [
    "BackgroundSufficiency", "CaseDisposition", "ConsultationCaseV2", "DecisionUnit",
    "EvidenceItem", "EvidenceLevel", "EvaluationCase", "FactState", "SourceBlock", "SourceCoverage",
    "SourceExtraction", "build_shadow_manifest", "evaluate_rerank_threshold_matrix",
    "extract_consultation", "extract_docx", "extract_pdf", "render_case_markdown",
    "prepare_consultation_v2",
    "scan_privacy", "validate_case_payload", "verify_parent_child_blocks",
]
