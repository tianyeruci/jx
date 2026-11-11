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
EVIDENCE_FACT_STATES = (
    "client_statement",
    "advisor_judgment",
    "decision",
    "plan",
    "actual_result",
)


class ConsultationRagEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # This label improves retrieval and advice handling, but a missing or
    # malformed label cannot invalidate an otherwise traceable core unit.
    # ``advice`` remains meaningful only when it is explicitly labelled.
    kind: str = ""
    quote: str = Field(min_length=4, max_length=1200)
    # Retain the source's fact status so a planned action or an advisor's
    # diagnostic is not later presented as a verified result.  It is part of
    # the JSON contract, not public metadata.
    fact_state: Literal[
        "client_statement",
        "advisor_judgment",
        "decision",
        "plan",
        "actual_result",
    ]
    # This is not a display-only auxiliary field.  It is the boundary that
    # prevents a case, its advice and its derived method candidate from being
    # proved against a different discussion in the same meeting.
    source_block_ids: list[str] = Field(min_length=1, max_length=50)


class ConsultationRagCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary_problem: str = Field(min_length=10, max_length=500)
    problem_category: str = "其他商业问题"
    case_stage: str = "不明确"
    relevant_background: list[str] = Field(min_length=1, max_length=10)
    problem_signals: list[str] = Field(default_factory=list, max_length=10)
    retrieval_queries: list[str] = Field(default_factory=list, max_length=6)
    reference_advice: list[str] = Field(default_factory=list, max_length=10)
    advice_summary: str = Field(default="", max_length=1200)
    evidence: list[ConsultationRagEvidence] = Field(min_length=1, max_length=20)
    methodology_judgment: str = Field(default="", max_length=1200)
    methodology_steps: list[str] = Field(default_factory=list, max_length=10)
    methodology_limits: list[str] = Field(default_factory=list, max_length=10)


class ConsultationRagResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = SCHEMA_VERSION
    cases: list[ConsultationRagCase] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_schema_version(self) -> "ConsultationRagResponse":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"咨询案例schema_version必须为{SCHEMA_VERSION}")
        return self


_NON_FACTUAL_RE = re.compile(r"(?:建议|应该|可以|必须|不要|计划|准备|打算|拟|预计|未来)")
_NON_FACTUAL_PROBLEM_RE = re.compile(
    r"(?:建议|应该|可以|必须|不要|计划|准备|打算|希望|想要|拟|预计|未来)"
)
_OUTCOME_CLAIM_RE = re.compile(
    r"(?:已(?:经)?(?:完成|验证|实现|达成|取得)|验证(?:成功|完成)|取得(?:结果|成效))"
)
_NUMBER_OR_DATE_RE = re.compile(r"\d+(?:\.\d+)?(?:[%％]|年|月|日|天|周|次|个|元|万|亿)?")
_EXPLICIT_INDUSTRY_RE = re.compile(r"([^\s，。；、]{2,8})(?:行业|赛道)")


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


def _derivation_is_within_evidence(
    value: str,
    evidence: list[Any],
    *,
    require_advice_grounding: bool = False,
) -> bool:
    """Reject only objectively stronger free-form claims without reintroducing
    the old, incorrect "summary must exactly equal quote" rule.

    A model may faithfully reorganise wording for a solution or method.  We
    cannot decide semantic entailment deterministically without another model
    call, which this pipeline deliberately does not make.  The hard boundary
    is therefore limited to factual escalation: an asserted completed result
    needs an ``actual_result`` evidence item, and numbers/dates in the
    derivation must already occur in this case's evidence.
    """

    text = value.strip()
    if not text:
        return True
    evidence_text = "\n".join(
        str(item.get("quote") or "") for item in evidence if isinstance(item, dict)
    )
    has_actual_result = any(
        isinstance(item, dict) and item.get("fact_state") == "actual_result" for item in evidence
    )
    if _OUTCOME_CLAIM_RE.search(text) and not has_actual_result:
        return False
    evidence_numbers = set(_NUMBER_OR_DATE_RE.findall(evidence_text))
    if not set(_NUMBER_OR_DATE_RE.findall(text)) <= evidence_numbers:
        return False
    # Do not try to solve general semantic entailment with brittle string
    # equality.  An explicit "X行业/赛道" is an exception: it is a concrete
    # applicability fact, so its identifying suffix must occur in the
    # case's own evidence before it can be rendered in a case or method.
    for raw_industry in _EXPLICIT_INDUSTRY_RE.findall(text):
        if not any(
            raw_industry[-length:] in evidence_text
            for length in range(2, min(4, len(raw_industry)) + 1)
        ):
            return False
    if require_advice_grounding:
        advice_items = [
            str(item.get("quote") or "")
            for item in evidence
            if isinstance(item, dict) and item.get("kind") == "advice"
        ]
        if not _advice_derivation_is_grounded(text, advice_items):
            return False
    return True


_NEGATED_ACTION_RE = re.compile(r"(?:不要|不|避免|禁止|勿|别)([\u4e00-\u9fff]{2,8})")
_ASCII_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_ADVICE_ESCALATION_TERMS = (
    "承诺",
    "保证",
    "退款",
    "赔付",
    "保底",
    "永久免费",
    "付费",
    "收费",
    "服务费",
    "费用",
    "佣金",
    "订金",
    "定金",
    "支付",
    "购买",
    "续费",
    "签约",
    "合同",
    "投放",
    "降价",
    "招聘",
    "裁员",
    "第三方",
    "代理",
    "员工",
    "团队",
    "仅限",
    "前提是",
)
_ADVICE_ACTOR_TERMS = (
    "创始人",
    "负责人",
    "销售人员",
    "销售团队",
    "运营人员",
    "客服人员",
    "交付人员",
    "客户经理",
    "代理人",
    "合伙人",
    "管理层",
)
_ADVICE_ACTION_BOUNDARY_RE = re.compile(
    r"(?:[,，;；。]|然后|之后|以后|再|同时|以及|并且|并|后|从而|进而|以便|借此)"
)
_GENERIC_GROUNDING_TERMS = {
    "客户",
    "用户",
    "需求",
    "问题",
    "业务",
    "方案",
    "方法",
    "数据",
    "建议",
    "调整",
    "验证",
}
_GENERIC_ASCII_GROUNDING_TERMS = {
    "api",
    "app",
    "crm",
    "erp",
    "saas",
}
_GENERIC_FOUR_CHARACTER_ANCHORS = {
    "客户需求",
    "客户数据",
    "客户问题",
    "用户需求",
    "用户数据",
    "用户问题",
    "业务需求",
    "业务问题",
}
_UNSUPPORTED_ADVICE_ACTION_TERMS = ("泄露", "窃取", "伪造", "欺诈", "规避", "绕过")


def _grounding_terms(value: str) -> set[str]:
    """Return small, deterministic anchors without pretending to entail text."""

    compact = _normalized(value)
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", compact)
    terms = {
        run[index : index + (3 if len(run) >= 3 else 2)]
        for run in chinese
        for index in range(max(0, len(run) - (3 if len(run) >= 3 else 2) + 1))
    }
    terms.update(term.casefold() for term in _ASCII_TERM_RE.findall(value))
    return terms


def _has_specific_evidence_anchor(value: str, evidence_text: str) -> bool:
    """Require a case-specific phrase instead of a generic entity overlap.

    This is a deterministic safety screen, not a claim of natural-language
    entailment. Exact fields or two independent non-generic anchors preserve
    ordinary faithful rewrites; one short overlap cannot authenticate a new
    commercial fact or action.
    """

    value_compact = _normalized(value)
    evidence_compact = _normalized(evidence_text)
    if not value_compact or not evidence_compact:
        return False
    if value_compact == evidence_compact:
        return True

    def chinese_ngrams(text: str, width: int) -> set[str]:
        return {
            run[index : index + width]
            for run in re.findall(r"[\u4e00-\u9fff]{%d,}" % width, text)
            for index in range(len(run) - width + 1)
        }

    def has_two_independent_anchors(terms: set[str]) -> bool:
        """Require anchors which are not merely overlapping slices of one phrase.

        A single four-character phrase also produces two overlapping 3-grams
        (for example ``经营效`` / ``营效率``).  Treating those slices as two
        facts would let one generic outcome phrase authenticate an invented
        problem.  The screen is intentionally conservative: two anchors have
        to be disjoint phrases, not adjacent windows over the same text.
        """

        ordered = sorted(terms, key=lambda term: (-len(term), term))
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                if not set(first).intersection(second):
                    return True
        return False

    shared_four = chinese_ngrams(value_compact, 4) & chinese_ngrams(evidence_compact, 4)
    meaningful_four = shared_four - _GENERIC_FOUR_CHARACTER_ANCHORS
    if has_two_independent_anchors(meaningful_four):
        return True
    evidence_ascii = {term.casefold() for term in _ASCII_TERM_RE.findall(evidence_text)}
    shared_ascii = {term.casefold() for term in _ASCII_TERM_RE.findall(value)} & evidence_ascii
    meaningful_ascii = {
        term
        for term in shared_ascii
        if term not in _GENERIC_ASCII_GROUNDING_TERMS and len(term) >= 4
    }
    if any(len(term) >= 8 for term in meaningful_ascii) or len(meaningful_ascii) >= 2:
        return True
    shared_three = chinese_ngrams(value_compact, 3) & chinese_ngrams(evidence_compact, 3)
    meaningful_three = {
        term
        for term in shared_three
        if not any(generic in term for generic in _GENERIC_GROUNDING_TERMS)
    }
    return has_two_independent_anchors(meaningful_three)


def _core_field_is_grounded(
    value: str,
    evidence: list[dict[str, Any]],
    *,
    allowed_kinds: set[str],
) -> bool:
    """Require a core case fact to have a role-compatible source anchor.

    Evidence ``kind`` remains auxiliary: a missing/unknown kind can still
    preserve a directly anchored fact.  An explicit incompatible kind cannot
    however turn background/advice into proof of a different core problem.
    """

    for item in evidence:
        kind = str(item.get("kind") or "").strip()
        if kind and kind not in allowed_kinds:
            continue
        if _has_specific_evidence_anchor(value, str(item.get("quote") or "")):
            return True
    return False


def _advice_derivation_is_grounded(value: str, advice_text: str | list[str]) -> bool:
    """Keep a free rewrite only when it has a current-case advice anchor.

    This is intentionally a conservative *screen*, not a semantic-entailment
    claim.  A field with no deterministic anchor is omitted while the
    independently useful case remains available; it never triggers another
    model request or a source-text fallback.
    """

    def is_subsequence(candidate: str, source: str) -> bool:
        source_iter = iter(source)
        return all(character in source_iter for character in candidate)

    advice_items = [advice_text] if isinstance(advice_text, str) else advice_text
    return any(
        _advice_derivation_matches_item(value, item, is_subsequence) for item in advice_items
    )


def _advice_derivation_matches_item(value: str, advice_text: str, is_subsequence: Any) -> bool:
    """Apply the deterministic advice screen to one quote, not a whole case."""

    # Advice can be faithfully shortened (or use the controlled 核验/验证
    # wording pair) without inventing a new action.  This is deliberately
    # advice-only: core facts still need a specific evidence anchor.
    normalized_value = _normalized(value).replace("核验", "验证")
    normalized_advice = _normalized(advice_text).replace("核验", "验证")
    faithful_subsequence = len(normalized_value) >= 4 and is_subsequence(
        normalized_value, normalized_advice
    )
    if not faithful_subsequence and not _has_specific_evidence_anchor(value, advice_text):
        return False
    # A shared short anchor is not permission to append a new commercial
    # promise, actor, paid condition or operational action.  These tokens are
    # intentionally narrow and deterministic: when one is present in a
    # derived field it must appear in this case's advice evidence itself.
    # This preserves faithful rewrites without pretending that an n-gram
    # overlap proves broad natural-language entailment.
    for term in _ADVICE_ESCALATION_TERMS:
        if term in value and term not in advice_text:
            return False
    # These actions are not an ordinary commercial condition or actor swap:
    # even a precise shared object (for example ``客户数据``) cannot make a
    # newly introduced disclosure or theft action a faithful rewrite.
    for term in _UNSUPPORTED_ADVICE_ACTION_TERMS:
        if term in value and term not in advice_text:
            return False
    # This is deliberately a small actor vocabulary, not an attempt at named
    # entity recognition.  It catches a material role reassignment while
    # retaining faithful rewrites whose subject is still supported by the
    # current case's advice evidence.
    for actor in _ADVICE_ACTOR_TERMS:
        if actor in value and actor not in advice_text:
            return False
    # Each linked action clause needs its own advice anchor.  One shared
    # phrase cannot authorize a following action such as "举办线下展会" or
    # "调整销售表达" that the advisor never proposed.  This remains a
    # deterministic fail-closed screen rather than a claim of full semantic
    # entailment, and it leaves the independently usable case intact.
    for clause in _ADVICE_ACTION_BOUNDARY_RE.split(value):
        # A faithful subsequence has already been bound to this exact advice
        # quote above.  Requiring two separate n-grams again would reject a
        # safe shortening such as “先验证真实需求” from “先验证目标客户的真实
        # 需求”.  Free rewrites still require every linked action to carry its
        # own independent anchor.
        if (
            not faithful_subsequence
            and len(_normalized(clause)) >= 3
            and not _has_specific_evidence_anchor(clause, advice_text)
        ):
            return False
    # A negation changes the recommendation's direction.  It is safer to
    # omit an ambiguous free rewrite than to turn an advisor's instruction
    # into its opposite.  The model's per-field evidence does not identify a
    # narrower advice item, so this guard intentionally remains conservative.
    if bool(_NEGATED_ACTION_RE.search(advice_text)) != bool(_NEGATED_ACTION_RE.search(value)):
        return False
    source_compact = _normalized(advice_text)
    derived_compact = _normalized(value)
    for action in _NEGATED_ACTION_RE.findall(advice_text):
        if action in derived_compact and not any(
            marker + action in derived_compact
            for marker in ("不要", "不", "避免", "禁止", "勿", "别")
        ):
            return False
    for action in _NEGATED_ACTION_RE.findall(value):
        if action in source_compact and not any(
            marker + action in source_compact
            for marker in ("不要", "不", "避免", "禁止", "勿", "别")
        ):
            return False
    return True


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
        if case.get("problem_category") not in PROBLEM_CATEGORIES:
            case["problem_category"] = "其他商业问题"
        if case.get("case_stage") not in CASE_STAGES:
            case["case_stage"] = "不明确"
        queries = case.get("retrieval_queries")
        if not isinstance(queries, list):
            case["retrieval_queries"] = []
        else:
            case["retrieval_queries"] = [
                value for value in queries if isinstance(value, str) and _contains_chinese(value)
            ][:6]
        background = case.get("relevant_background")
        if isinstance(background, list):
            case["relevant_background"] = [
                value
                for value in background
                if isinstance(value, str) and value.strip() and _is_factual(value)
            ]
        signals = case.get("problem_signals")
        if isinstance(signals, list):
            case["problem_signals"] = [
                value
                for value in signals
                if isinstance(value, str)
                and value.strip()
                and _is_factual(value, problem_signal=True)
            ]
        for field_name in (
            "retrieval_queries",
            "reference_advice",
            "methodology_steps",
            "methodology_limits",
        ):
            value = case.get(field_name)
            case[field_name] = (
                [item for item in value if isinstance(item, str) and item.strip()][:10]
                if isinstance(value, list)
                else []
            )
        for field_name in ("advice_summary", "methodology_judgment"):
            value = case.get(field_name)
            case[field_name] = value if isinstance(value, str) else ""
        evidence = case.get("evidence")
        if isinstance(evidence, list):
            normalized_evidence: list[Any] = []
            state_aliases = {
                "客户陈述": "client_statement",
                "客户事实": "client_statement",
                "顾问判断": "advisor_judgment",
                "诊断": "advisor_judgment",
                "已确认决定": "decision",
                "决定": "decision",
                "行动计划": "plan",
                "计划": "plan",
                "实际结果": "actual_result",
                "结果": "actual_result",
            }
            for raw_evidence in evidence:
                if not isinstance(raw_evidence, dict):
                    normalized_evidence.append(raw_evidence)
                    continue
                item = dict(raw_evidence)
                raw_kind = str(item.get("kind") or "").strip()
                item["kind"] = raw_kind if raw_kind in {"background", "problem", "advice"} else ""
                raw_state = str(item.get("fact_state") or "").strip()
                state = state_aliases.get(raw_state, raw_state)
                item["fact_state"] = state
                normalized_evidence.append(item)
            case["evidence"] = normalized_evidence
            evidence = normalized_evidence
        advice_quotes = (
            {
                str(item.get("quote")).strip()
                for item in evidence
                if isinstance(item, dict)
                and item.get("kind") == "advice"
                and str(item.get("quote") or "").strip()
            }
            if isinstance(evidence, list)
            else set()
        )
        reference_advice = case.get("reference_advice")
        if isinstance(reference_advice, list) and advice_quotes:
            case["reference_advice"] = [
                value
                for value in reference_advice
                if isinstance(value, str) and value.strip() in advice_quotes
            ]
        else:
            case["reference_advice"] = []
        if not advice_quotes:
            case["advice_summary"] = ""
            case["methodology_judgment"] = ""
            case["methodology_steps"] = []
            case["methodology_limits"] = []
        else:
            if not _derivation_is_within_evidence(
                case["advice_summary"], evidence, require_advice_grounding=True
            ):
                case["advice_summary"] = ""
            if not _derivation_is_within_evidence(
                case["methodology_judgment"], evidence, require_advice_grounding=True
            ):
                case["methodology_judgment"] = ""
            case["methodology_steps"] = [
                value
                for value in case["methodology_steps"]
                if _derivation_is_within_evidence(value, evidence, require_advice_grounding=True)
            ]
            case["methodology_limits"] = [
                value
                for value in case["methodology_limits"]
                if _derivation_is_within_evidence(value, evidence, require_advice_grounding=True)
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
    """Validate structure, evidence coverage and semantic safety per case.

    An invalid unit cannot hold back independently useful, evidence-bound
    cases.  The caller still fails when no usable core case remains and never
    receives a source-Markdown fallback from this validator.
    """

    normalized = _normalize_model_payload(payload)
    raw_cases = normalized.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("咨询模型未返回cases数组")
    accepted: list[ConsultationRagCase] = []
    rejection_reason = ""
    for index, raw_case in enumerate(raw_cases, start=1):
        try:
            case = ConsultationRagCase.model_validate(raw_case)
            if any(not _is_factual(value) for value in case.relevant_background):
                raise ValueError("背景/问题表现包含建议或未来动作")
            if any(
                _is_factual(value, problem_signal=True) is False for value in case.problem_signals
            ):
                raise ValueError("背景/问题表现包含建议或未来动作")
            if not case.primary_problem.strip() or not case.relevant_background:
                raise ValueError("缺少核心问题或理解问题所需的必要情境")
            for evidence in case.evidence:
                if not set(evidence.source_block_ids) <= set(source_blocks):
                    raise ValueError("存在未知来源块引用")
                if not any(
                    evidence_quote_matches(evidence.quote, source_blocks[block_id])
                    for block_id in evidence.source_block_ids
                ):
                    raise ValueError("证据引文不在所声明的来源块中")
            evidence_payloads = [evidence.model_dump() for evidence in case.evidence]
            if not _core_field_is_grounded(
                case.primary_problem,
                evidence_payloads,
                allowed_kinds={"problem"},
            ):
                raise ValueError("咨询案例核心问题缺少problem来源锚点")
            for value in case.relevant_background:
                if not _core_field_is_grounded(
                    value,
                    evidence_payloads,
                    allowed_kinds={"background"},
                ):
                    raise ValueError("咨询案例必要背景缺少background来源锚点")
            for value in case.problem_signals:
                if not _core_field_is_grounded(
                    value,
                    evidence_payloads,
                    allowed_kinds={"problem", "background"},
                ):
                    raise ValueError("咨询案例问题表现缺少问题/背景来源锚点")
            for value in (
                case.primary_problem,
                *case.relevant_background,
                *case.problem_signals,
            ):
                if not _derivation_is_within_evidence(value, evidence_payloads):
                    raise ValueError("咨询案例核心事实超出本案例来源证据")
            advice_quotes = {item.quote.strip() for item in case.evidence if item.kind == "advice"}
            if (case.reference_advice or case.advice_summary.strip()) and not advice_quotes:
                raise ValueError("方案整理缺少可回溯advice证据")
            serialized = case.model_dump_json()
            for token in sensitive_tokens:
                if token and token.casefold() in serialized.casefold():
                    raise ValueError("咨询案例模型结果仍包含受保护身份")
            signature = (
                _normalized(case.primary_problem),
                tuple(sorted(_normalized(value) for value in case.relevant_background)),
                tuple(sorted(_normalized(item.quote) for item in case.evidence)),
            )
            if any(
                (
                    _normalized(existing.primary_problem),
                    tuple(sorted(_normalized(value) for value in existing.relevant_background)),
                    tuple(sorted(_normalized(item.quote) for item in existing.evidence)),
                )
                == signature
                for existing in accepted
            ):
                rejection_reason = rejection_reason or "咨询案例与已有案例问题、背景和证据完全重复"
                continue
            accepted.append(case)
        except Exception as exc:
            rejection_reason = rejection_reason or str(exc)
            continue
    if not accepted:
        raise ValueError(rejection_reason or "咨询模型未返回任何可用核心案例")
    response = ConsultationRagResponse(schema_version=SCHEMA_VERSION, cases=accepted)
    for case in response.cases:
        serialized = case.model_dump_json()
        for token in sensitive_tokens:
            if token and token.casefold() in serialized.casefold():
                raise ValueError("咨询案例模型结果仍包含受保护身份")
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
    advice_evidence = [item for item in case.evidence if item.kind == "advice"]
    if advice_evidence or case.advice_summary.strip():
        lines.extend(["", "## 该案例中的处理思路（命中后参考，不作为核心相似条件）"])
        state_labels = {
            "client_statement": "当事人陈述",
            "advisor_judgment": "顾问判断",
            "decision": "已确认决定",
            "plan": "行动计划",
            "actual_result": "实际结果",
        }
        lines.extend(
            f"- [{state_labels[item.fact_state]}] {item.quote.strip()}"
            for item in advice_evidence
            if item.quote.strip()
        )
        if case.advice_summary.strip():
            lines.append(case.advice_summary.strip())
    lines.extend(["", "## 常见相似案例问法"])
    lines.extend(f"- {query.strip()}" for query in case.retrieval_queries if query.strip())
    return "\n".join(lines).strip() + "\n"


def render_methodology_candidate(case_id: str, case: ConsultationRagCase) -> str:
    """Render a case-scoped method candidate without claiming general validity."""

    advice_evidence = [item for item in case.evidence if item.kind == "advice"]
    source_advice = [item.quote.strip() for item in advice_evidence if item.quote.strip()]
    advice = (
        case.advice_summary.strip()
        or "；".join(item.strip() for item in case.reference_advice if item.strip())
        or "；".join(source_advice)
    )
    if not advice:
        return ""
    state_labels = {
        "client_statement": "当事人陈述",
        "advisor_judgment": "顾问判断",
        "decision": "已确认决定",
        "plan": "行动计划",
        "actual_result": "实际结果",
    }
    lines = [
        f"# 匿名方法候选 {case_id}",
        "",
        "> 方法候选仅整理本案例证据，不代表跨案例验证的通用方法。",
        "",
        "## 适用问题与背景",
        case.primary_problem.strip(),
        *[f"- {item.strip()}" for item in case.relevant_background if item.strip()],
        "",
        "## 适用场景",
        "；".join(item.strip() for item in case.relevant_background if item.strip()),
        "",
        "## 判断依据",
        case.methodology_judgment.strip() or source_advice[0],
    ]
    if case.methodology_steps:
        lines.extend(
            [
                "",
                "## 执行步骤",
                *[f"- {item.strip()}" for item in case.methodology_steps if item.strip()],
            ]
        )
    if case.methodology_limits:
        lines.extend(
            [
                "",
                "## 限制",
                *[f"- {item.strip()}" for item in case.methodology_limits if item.strip()],
            ]
        )
    lines.extend(["", "## 本案例中的处理思路", advice, "", "## 来源证据边界"])
    lines.extend(
        f"- [{state_labels[item.fact_state]}] {item.quote.strip()}"
        for item in advice_evidence
        if item.quote.strip()
    )
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
    "EVIDENCE_FACT_STATES",
    "PROBLEM_CATEGORIES",
    "SCHEMA_VERSION",
    "ConsultationRagCase",
    "ConsultationRagEvidence",
    "ConsultationRagResponse",
    "evidence_quote_matches",
    "render_case_markdown",
    "render_methodology_candidate",
    "render_publication_markdown",
    "topic_for_problem_category",
    "validate_consultation_rag_response",
]
