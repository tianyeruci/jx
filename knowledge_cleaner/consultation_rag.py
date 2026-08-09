"""Evidence-bound consultation case extraction for the business-advisor RAG.

The standalone sample cleaner and the production ``personal_business_consultation``
strategy share the same semantic contract here.  This module deliberately does
not perform provider calls or Dify writes; callers supply the already anonymised
source blocks and decide how to publish/fallback.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "consultation-rag-v1"

PROBLEM_CATEGORIES = (
    "流量与引流",
    "私域与转化",
    "产品设计与产品梯度",
    "定位与方向",
    "定价与销售",
    "交付与用户筛选",
    "内容与表达",
    "社群与留存",
    "执行与增长节奏",
    "其他商业问题",
)
CASE_STAGES = (
    "从0到1探索",
    "已有基础未闭环",
    "初步验证待优化",
    "已验证待放大",
    "放大后的交付或效率优化",
    "不明确",
)


class ConsultationRagEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["background", "problem", "advice"]
    quote: str = Field(min_length=4, max_length=1200)
    # Qwen may carry the historical v2 evidence binding into the v1 shape.
    # Accept it as an optional strengthening signal, but never require it for
    # v1 and never trust unknown block IDs.
    source_block_ids: list[str] = Field(default_factory=list, max_length=50)


class ConsultationRagCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary_problem: str = Field(min_length=10, max_length=500)
    problem_category: str = Field(pattern="|".join(re.escape(v) for v in PROBLEM_CATEGORIES))
    case_stage: str = Field(pattern="|".join(re.escape(v) for v in CASE_STAGES))
    relevant_background: list[str] = Field(min_length=1, max_length=10)
    problem_signals: list[str] = Field(default_factory=list, max_length=10)
    retrieval_queries: list[str] = Field(min_length=3, max_length=6)
    reference_advice: list[str] = Field(default_factory=list, max_length=10)
    evidence: list[ConsultationRagEvidence] = Field(min_length=2, max_length=20)


class ConsultationRagResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = SCHEMA_VERSION
    cases: list[ConsultationRagCase] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_schema_version(self) -> "ConsultationRagResponse":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"咨询案例schema_version必须为{SCHEMA_VERSION}")
        return self


_NON_FACTUAL_RE = re.compile(
    r"(?:建议|应该|可以|必须|不要|计划|准备|打算|拟|预计|未来)"
)
_NON_FACTUAL_PROBLEM_RE = re.compile(
    r"(?:建议|应该|可以|必须|不要|计划|准备|打算|希望|想要|拟|预计|未来)"
)


def _normalized(text: str) -> str:
    text = re.sub(r"(?m)^\s*[-+*>]\s+", "", text or "")
    text = re.sub(r"[`*_#]", "", text)
    return re.sub(r"\s+", "", text)


def evidence_quote_matches(quote: str, source: str) -> bool:
    source_norm = _normalized(source)
    quote_norm = _normalized(quote)
    if not quote_norm:
        return False
    if quote_norm in source_norm:
        return True
    parts = [part for part in re.split(r"(?:\.{3,}|…+)", quote_norm) if part]
    if len(parts) < 2 or any(len(part) < 4 for part in parts):
        return False
    position = 0
    for part in parts:
        position = source_norm.find(part, position)
        if position < 0:
            return False
        position += len(part)
    return True


def _is_factual(value: str, *, problem_signal: bool = False) -> bool:
    expression = _NON_FACTUAL_PROBLEM_RE if problem_signal else _NON_FACTUAL_RE
    return bool(value.strip() and not expression.search(value.strip()))


def _contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _normalize_model_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Apply safe lossy cleanup before strict validation.

    Qwen frequently copies advice-like sentences into the factual background
    arrays and emits ``reference_advice`` without an advice evidence item.  A
    whole-source fallback for those presentation mistakes discards otherwise
    traceable cases.  We remove only those unsafe, unsupported fields; all
    remaining structure, evidence quotes, IDs, enums and source coverage stay
    strict in ``model_validate`` below.
    """

    normalized = dict(payload)
    raw_cases = normalized.get("cases")
    if not isinstance(raw_cases, list):
        return normalized
    cases: list[Any] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            cases.append(raw_case)
            continue
        case = dict(raw_case)
        background = case.get("relevant_background")
        if isinstance(background, list):
            case["relevant_background"] = [
                value
                for value in background
                if not isinstance(value, str) or _is_factual(value)
            ]
        signals = case.get("problem_signals")
        if isinstance(signals, list):
            case["problem_signals"] = [
                value
                for value in signals
                if not isinstance(value, str)
                or _is_factual(value, problem_signal=True)
            ]
        evidence = case.get("evidence")
        advice_quotes = {
            str(item.get("quote")).strip()
            for item in evidence
            if isinstance(item, dict) and item.get("kind") == "advice"
            and str(item.get("quote") or "").strip()
        } if isinstance(evidence, list) else set()
        reference_advice = case.get("reference_advice")
        if isinstance(reference_advice, list):
            case["reference_advice"] = [
                value
                for value in reference_advice
                if isinstance(value, str) and value.strip() in advice_quotes
            ]
        cases.append(case)
    normalized["cases"] = cases
    return normalized


def validate_consultation_rag_response(
    payload: Mapping[str, Any],
    *,
    source_blocks: Mapping[str, str],
    sensitive_tokens: tuple[str, ...] = (),
) -> ConsultationRagResponse:
    """Validate structure, evidence coverage and semantic safety.

    Validation is intentionally all-or-none: one unsupported case invalidates
    the whole model result, allowing the caller to use the deterministic source
    fallback rather than publishing a partial meeting.
    """

    response = ConsultationRagResponse.model_validate(_normalize_model_payload(payload))
    source = "\n\n".join(str(value) for value in source_blocks.values())
    for index, case in enumerate(response.cases, start=1):
        if not all(_contains_chinese(query) for query in case.retrieval_queries):
            raise ValueError(f"咨询案例{index}的retrieval_queries必须为中文自然问法")
        for value in case.relevant_background:
            if not _is_factual(value):
                raise ValueError(f"咨询案例{index}的背景/问题表现包含建议或未来动作")
        for value in case.problem_signals:
            if not _is_factual(value, problem_signal=True):
                raise ValueError(f"咨询案例{index}的背景/问题表现包含建议或未来动作")
        evidence_by_kind = {item.kind for item in case.evidence}
        if not {"background", "problem"}.issubset(evidence_by_kind):
            raise ValueError(f"咨询案例{index}缺少background或problem证据")
        for evidence in case.evidence:
            if evidence.source_block_ids and not set(evidence.source_block_ids) <= set(source_blocks):
                raise ValueError(f"咨询案例{index}存在未知来源块引用")
            if not evidence_quote_matches(evidence.quote, source):
                raise ValueError(f"咨询案例{index}存在无法回溯的证据引用")
        advice_quotes = {
            item.quote.strip() for item in case.evidence if item.kind == "advice"
        }
        if case.reference_advice and not advice_quotes:
            raise ValueError(f"咨询案例{index}的reference_advice缺少advice证据")
        if any(item.strip() not in advice_quotes for item in case.reference_advice):
            raise ValueError(f"咨询案例{index}的reference_advice不是可回溯原文")
        serialized = response.model_dump_json()
        for token in sensitive_tokens:
            if token and token.casefold() in serialized.casefold():
                raise ValueError("咨询案例模型结果仍包含受保护身份")
    for left in range(len(response.cases)):
        for right in range(left + 1, len(response.cases)):
            first = set(response.cases[left].primary_problem)
            second = set(response.cases[right].primary_problem)
            if first and second and len(first & second) / max(1, len(first | second)) >= 0.80:
                raise ValueError("咨询案例之间问题高度重复，疑似过度拆分")
    return response


def topic_for_problem_category(category: str) -> str:
    """Map sample semantic categories to the existing advisor topic enum.

    No new remote metadata field is introduced.  An unmapped category returns
    an empty string so an incorrect hard filter is never created.
    """

    mapping = {
        "定位与方向": "战略与商业模式",
        "产品设计与产品梯度": "产品与客户",
        "交付与用户筛选": "产品与客户",
        "定价与销售": "市场销售与增长",
        "流量与引流": "市场销售与增长",
        "私域与转化": "市场销售与增长",
        "内容与表达": "市场销售与增长",
        "社群与留存": "市场销售与增长",
        "执行与增长节奏": "决策与执行",
    }
    return mapping.get(category, "")


def render_case_markdown(case_id: str, case: ConsultationRagCase) -> str:
    """Render one marker-free case for local storage.

    Dify child separators are injected only immediately before publication by
    the cleaning worker; retaining raw separators here would make reprocessing
    and source fallback unsafe.
    """

    lines = [
        f"# 匿名商业案例 {case_id}",
        "",
        "## 核心问题",
        case.primary_problem.strip(),
        "",
        "## 与该问题直接相关的背景",
    ]
    lines.extend(f"- {item.strip()}" for item in case.relevant_background if item.strip())
    if case.problem_signals:
        lines.extend(["", "## 问题表现"])
        lines.extend(f"- {item.strip()}" for item in case.problem_signals if item.strip())
    if case.reference_advice:
        lines.extend(["", "## 该案例中的处理思路（命中后参考，不作为核心相似条件）"])
        lines.extend(f"- {item.strip()}" for item in case.reference_advice if item.strip())
    lines.extend(["", "## 常见相似案例问法"])
    lines.extend(f"- {query.strip()}" for query in case.retrieval_queries if query.strip())
    return "\n".join(lines).strip() + "\n"


def render_publication_markdown(markdown: str, *, child_max_chars: int = 800) -> str:
    """Insert deterministic child boundaries without changing local Markdown."""

    from .chunking import DIFY_CHILD_SEPARATOR

    cleaned = re.sub(r"\n\s*<<<DIFY_(?:PARENT|CHILD)>>>\s*\n", "\n\n", markdown or "").strip()
    if not cleaned:
        return ""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", cleaned) if part.strip()]
    children: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        extra = len(paragraph) + (2 if current else 0)
        if current and size + extra > child_max_chars:
            children.append("\n\n".join(current))
            current = []
            size = 0
        current.append(paragraph)
        size += len(paragraph) + (2 if len(current) > 1 else 0)
    if current:
        children.append("\n\n".join(current))
    return f"\n\n{DIFY_CHILD_SEPARATOR}\n\n".join(children).strip() + "\n"


__all__ = [
    "CASE_STAGES",
    "PROBLEM_CATEGORIES",
    "SCHEMA_VERSION",
    "ConsultationRagCase",
    "ConsultationRagEvidence",
    "ConsultationRagResponse",
    "evidence_quote_matches",
    "render_case_markdown",
    "render_publication_markdown",
    "topic_for_problem_category",
    "validate_consultation_rag_response",
]
