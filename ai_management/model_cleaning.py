from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_STRATEGIES = frozenset(
    {
        "personal_business_consultation",
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
        "course_route_catalog",
    }
)
POST_STRATEGIES = frozenset(
    {
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
    }
)
REALTIME_ONLY_STRATEGIES = frozenset({"recent_activity"})


def get_provider_batch_ids(task: Any, *, include_terminal: bool = False) -> list[str]:
    """Return provider Batch IDs from both current and legacy task fields.

    Older rows stored only ``provider_batch_id`` while current rows also
    maintain the plural field for multi-file Batch submissions.  Runtime
    cleanup and polling must keep seeing the legacy ID until the row is
    normalized by the current worker. Polling may additionally include IDs
    persisted in ``result_counts.provider_terminal_batches``; the default view
    excludes those already-terminal IDs so cancellation and admin views do not
    treat them as active work.
    """

    batch_ids = [str(value) for value in (getattr(task, "provider_batch_ids", None) or []) if value]
    legacy_batch_id = str(getattr(task, "provider_batch_id", None) or "")
    if legacy_batch_id and legacy_batch_id not in batch_ids:
        batch_ids.append(legacy_batch_id)
    if include_terminal:
        result_counts = getattr(task, "result_counts", None) or {}
        terminal_batches = result_counts.get("provider_terminal_batches", {})
        if isinstance(terminal_batches, dict):
            for provider_batch_id in terminal_batches:
                value = str(provider_batch_id or "")
                if value and value not in batch_ids:
                    batch_ids.append(value)
    return batch_ids


ALLOWED_TOPICS = frozenset(
    {
        "战略与商业模式",
        "产品与客户",
        "市场销售与增长",
        "组织与管理",
        "财务与风险",
        "学习与成长",
        "决策与执行",
        "AI与智能体",
    }
)


@dataclass(frozen=True, slots=True)
class PromptRequest:
    strategy_code: str
    item_id: str
    content_sha256: str
    title: str
    blocks: dict[str, str]


class ModelNoResponseError(RuntimeError):
    """The provider did not return a usable model message.

    The worker is allowed one document-level retry for this class only.  A
    non-empty response that cannot satisfy the JSON contract uses a different
    exception so it is recorded and skipped without another model call.
    """


class ModelPayloadError(ValueError):
    """The provider returned non-empty content that is not usable JSON."""


class PostKnowledgeUnit(BaseModel):
    """Evidence-bound business knowledge extracted from one source article."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    topic: str = ""
    problem: str = ""
    conclusion: str = ""
    reasoning: str = ""
    applicable_conditions: list[str] = Field(default_factory=list, max_length=10)
    actions: list[str] = Field(default_factory=list, max_length=10)
    risks_or_limits: list[str] = Field(default_factory=list, max_length=10)
    source_block_ids: list[str] = Field(min_length=1)


class PostSemanticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: Literal["keep", "exclude", "review"]
    business_relevance: Literal["direct", "transferable", "none", "ambiguous"]
    reason_code: str = Field(min_length=1, max_length=80)
    keep_block_ids: list[str]
    drop_block_ids: list[str]
    topic: str = ""
    time_sensitive: bool
    confidence: float = Field(ge=0, le=1)
    knowledge_units: list[PostKnowledgeUnit] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_semantics(self) -> "PostSemanticResponse":
        if self.disposition == "keep" and not self.keep_block_ids:
            raise ValueError("保留内容必须至少包含一个来源块")
        if self.disposition == "exclude" and self.keep_block_ids:
            raise ValueError("排除内容不能保留来源块")
        if self.topic and self.topic not in ALLOWED_TOPICS:
            raise ValueError("topic不在受控商业主题词表")
        for unit in self.knowledge_units:
            if unit.topic and unit.topic not in ALLOWED_TOPICS:
                raise ValueError("knowledge_unit topic不在受控商业主题词表")
            if not any(
                value.strip()
                for value in (
                    unit.problem,
                    unit.conclusion,
                    unit.reasoning,
                    *unit.applicable_conditions,
                    *unit.actions,
                    *unit.risks_or_limits,
                )
            ):
                raise ValueError("knowledge_unit必须至少包含一个知识字段")
        return self


class ConsultationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: Literal[
        "背景与目标",
        "关键问题",
        "顾问判断",
        "已确认决定",
        "行动计划",
        "实际结果",
    ]
    text: str = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)
    fact_state: Literal[
        "client_statement",
        "advisor_judgment",
        "decision",
        "plan",
        "actual_result",
    ]


class ConsultationSemanticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[ConsultationSection] = Field(min_length=1, max_length=6)


class CourseEnrichmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction_candidate: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=4000)
    problems: list[str] = Field(default_factory=list, max_length=10)
    audience: str = Field(default="", max_length=1000)
    highlights: list[str] = Field(default_factory=list, max_length=10)
    learning_outcomes: list[str] = Field(default_factory=list, max_length=10)
    route_target: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def normalize_text_fields(cls, value: Any) -> Any:
        """Tolerate qwen-plus returning a list for a single text field.

        The prompt and schema require strings, but production responses have
        occasionally used a one-or-more-item JSON array for fields such as
        ``audience`` and ``direction_candidate``.  Joining those values keeps
        the semantic candidate reviewable while preserving the strict output
        shape stored by the service.  Other field types remain subject to the
        normal Pydantic contract.
        """

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field_name in (
            "direction_candidate",
            "description",
            "audience",
            "route_target",
        ):
            raw = normalized.get(field_name)
            if raw is None:
                normalized[field_name] = ""
            elif isinstance(raw, list):
                normalized[field_name] = "；".join(
                    str(item).strip() for item in raw if str(item).strip()
                )
            elif not isinstance(raw, str):
                normalized[field_name] = str(raw)
        return normalized

    @model_validator(mode="after")
    def reject_unsupported_claims(self) -> "CourseEnrichmentResponse":
        serialized = self.model_dump_json()
        if "http://" in serialized.lower() or "https://" in serialized.lower():
            raise ValueError("课程AI补全不得返回链接")
        forbidden = ("保证", "必赚", "百分百", "一定能", "具体课节", "时间点")
        if any(value in serialized for value in forbidden):
            raise ValueError("课程AI补全包含未经来源支持的承诺或课节细节")
        return self


_POST_SYSTEM_PROMPT = """你是商业顾问知识库的证据门禁和知识单元提取器。请输出严格 JSON。
目标是提高商业顾问问答质量，不是机械去广告。文件名不可见，也不得猜测作者或来源。
只按给出的来源块判断：
1. 保留直接商业知识，或可迁移的决策、学习、执行方法及必要证据和限制；
2. 纯红包、节日祝福、行政通知、闲聊、纯联系方式和范围外内容排除；
3. 商业正文附带引流尾部时保留正文块、删除推广和联系方式块；
4. 推广本身是营销方法或案例证据时保留；
5. 无法无损分离、事实归属不清或证据不足时 review；
6. 不得改写来源块，不得返回不存在的块 ID；知识单元中的概括必须能由引用来源块支持。
JSON 字段必须且只能包含 disposition、business_relevance、reason_code、
keep_block_ids、drop_block_ids、topic、time_sensitive、confidence、knowledge_units。
格式契约：
- disposition 只能是 keep、exclude、review；
- business_relevance 只能是 direct、transferable、none、ambiguous；
- reason_code 必须是 1 至 80 个字符的小写英文/数字/下划线机器码；
- keep_block_ids 与 drop_block_ids 只能列出输入中的 block ID，且两者合起来必须覆盖全部 block；
- topic 只能为空字符串，或战略与商业模式、产品与客户、市场销售与增长、组织与管理、财务与风险、学习与成长、决策与执行、AI与智能体之一；
- time_sensitive 只能是 JSON 布尔值 true 或 false；
- confidence 必须是 0 到 1 的 JSON 数字，不要使用“高/中/低”、百分号或字符串。
- knowledge_units 是提取出的可检索商业知识单元数组；keep 时至少返回一个。
- 每个 knowledge_unit 必须包含 unit_id、title、topic、problem、conclusion、reasoning、
  applicable_conditions、actions、risks_or_limits、source_block_ids；没有依据的字段留空。
- knowledge_units 只能引用 keep_block_ids 中的来源块；不要编造数字、日期、人物、结果或承诺。
- 如果一篇文章只有一个完整主题，knowledge_units 只返回一个；若包含多个互不依赖的主题，可拆成多个。
business_relevance 是分类标签，不是相关度分数，也不是布尔值。返回格式示例：
{"disposition":"keep","business_relevance":"direct","reason_code":"business_method","keep_block_ids":["b001"],"drop_block_ids":[],"topic":"产品与客户","time_sensitive":false,"confidence":0.8,"knowledge_units":[{"unit_id":"u001","title":"定位验证","topic":"产品与客户","problem":"客户问题不清晰","conclusion":"先访谈客户再验证定位","reasoning":"来源明确给出验证顺序","applicable_conditions":["需要确认真实客户问题"],"actions":["访谈客户","验证定位"],"risks_or_limits":["不能把计划当成结果"],"source_block_ids":["b001"]}]}"""

_CONSULTATION_SYSTEM_PROMPT = """你是商业咨询案例 RAG 清洗器。请输出严格 JSON。
本策略当前主契约是 consultation-rag-v1：把一份匿名化会议纪要按“背景与目标相同
才属于同一案”的规则拆成 1~8 个可独立检索的问题案例。相同商业背景/目标下的多个
领域建议仍保留在同一个 case；只有独立背景/目标才拆分。主契约 JSON 只能包含：
{"schema_version":"consultation-rag-v1","cases":[{"primary_problem":"...",
"problem_category":"...","case_stage":"...","relevant_background":[],
"problem_signals":[],"retrieval_queries":[],"reference_advice":[],
"advice_summary":"","methodology_judgment":"","methodology_steps":[],
"methodology_limits":[],"evidence":[{"kind":"background|problem|advice","quote":"...","fact_state":"client_statement|advisor_judgment|decision|plan|actual_result","source_block_ids":["b001"]}]}]}。
 problem_category 是辅助分类，缺失或不规范时使用“其他商业问题”。可用值包括：流量与引流、私域与转化、产品设计与产品梯度、定位与方向、
定价与销售、交付与用户筛选、内容与表达、社群与留存、执行与增长节奏、其他商业问题。
case_stage 只能使用：从0到1探索、已有基础未闭环、初步验证待优化、已验证待放大、
放大后的交付或效率优化、不明确。evidence 的 kind 只能是 background、problem、advice，
 quote 必须能在输入来源块中逐字回溯，source_block_ids 必须只列该 quote 所在的输入 block ID。kind 是辅助分类；缺失或不规范的分类、阶段、检索问法和细粒度 evidence 不得单独阻塞有可回溯核心内容的案例。
relevant_background/problem_signals 只能写已发生事实，不能把建议、计划或预计结果当事实。
 retrieval_queries 是可选的简体中文用户自然问法，不得输出英文或新增输入不存在的事实；不能生成时返回空数组。
 如果来源块中明确出现顾问建议、解决方案、采取的做法或处理步骤，必须尽量完整保留：用 kind=advice 的 evidence 逐字引用，reference_advice 只放对应的原文建议引文，advice_summary 对这些引文做忠实整理或改写，不要求逐字等于引文。
 methodology_judgment、methodology_steps、methodology_limits 是基于本案例 advice evidence 自动提炼的候选方法，分别填写判断依据、执行步骤和限制；不要等待人工另行提炼，也不要把单案例候选包装成跨案例验证的通用规律。没有可回溯 advice evidence 时，这四个字段必须为空，不能编造方案或方法。
 advice 的 fact_state 必须区分顾问判断、已确认决定、行动计划和实际结果；计划、判断或建议绝不能写成实际结果。解决方案可能分散在会议的不同来源块中：只把与当前问题、背景/目标和处理策略直接对应的 advice 放入同一个 case，不能仅因主题相似把另一问题的方案错配过来。
 同一背景、目标且共享处理策略的关联问题可放在一个 case；即使背景相同，只要问题和方案不同也必须拆成不同 case。共享背景只保留理解该问题和方案所必需的事实。
 模型/调用失败或全部核心单元不可用时服务端最多完整重跑一次；不得产生新的人工复核状态或原文兜底发布。"""

_COURSE_SYSTEM_PROMPT = """你是课程目录语义补全助手。请输出严格 JSON。
只可提出方向候选、内容/问题、适用人群、课程重点、学习目标和路线目标。
不得生成或修改链接、ID、课程名、路线名、阶段、顺序、前置课程、课节、时间点、
收益承诺或来源中不存在的课程。输出全部是 pending_review 候选。
JSON 必须且只能包含 direction_candidate、description、problems、audience、
highlights、learning_outcomes、route_target；无证据的字段使用空字符串或空数组。"""


def build_prompt_messages(
    request: PromptRequest,
    *,
    repair_hint: str = "",
) -> list[dict[str, str]]:
    if request.strategy_code in POST_STRATEGIES:
        system = _POST_SYSTEM_PROMPT
    elif request.strategy_code == "personal_business_consultation":
        system = _CONSULTATION_SYSTEM_PROMPT
    elif request.strategy_code == "course_route_catalog":
        system = _COURSE_SYSTEM_PROMPT
    else:
        raise ValueError("策略不使用大模型语义清洗")
    payload = {
        "item_id": request.item_id,
        "content_sha256": request.content_sha256,
        "title": request.title,
        "blocks": request.blocks,
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "请根据以下 JSON 来源块返回 JSON 结果：\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            + (
                "\n\n上一次输出未通过结构契约。请在不改写来源正文的前提下"
                "重新输出完整 JSON，并满足这一项纠正要求：" + repair_hint.strip()
                if repair_hint.strip()
                else ""
            ),
        },
    ]


def build_custom_id(request: PromptRequest, prompt_version: str) -> str:
    raw = "|".join(
        [
            request.strategy_code,
            request.item_id,
            request.content_sha256,
            prompt_version,
        ]
    )
    return f"clean_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def choose_call_mode(
    strategy_code: str,
    candidate_count: int,
    estimated_input_tokens: int,
    *,
    batch_candidate_threshold: int = 100,
    batch_token_threshold: int = 200_000,
    batch_enabled: bool = False,
) -> Literal["realtime", "batch"]:
    if strategy_code in REALTIME_ONLY_STRATEGIES:
        return "realtime"
    if strategy_code not in MODEL_STRATEGIES:
        raise ValueError("策略不使用大模型")
    # Content strategies may use Batch only when the operator explicitly
    # enables it.  The default remains realtime, preserving per-document
    # isolation for normal production runs.
    if not batch_enabled:
        return "realtime"
    if (
        candidate_count >= batch_candidate_threshold
        or estimated_input_tokens >= batch_token_threshold
    ):
        return "batch"
    return "realtime"


def build_model_request_body(
    messages: list[dict[str, str]],
    *,
    model: str = "qwen-plus",
    max_tokens: int = 8192,
) -> dict[str, Any]:
    """Build the single canonical Qwen request used by realtime and Batch.

    Keeping the request body in one seam prevents the two transports from
    silently drifting in temperature, thinking, or structured-output policy.
    The provider currently supports JSON-object mode; a JSON Schema is not
    forced here because the configured Qwen-compatible endpoint does not
    advertise the stricter schema variant.
    """

    if not 1 <= max_tokens <= 32768:
        raise ValueError("max_tokens 必须在 1-32768 之间")
    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.01,
        "seed": 1234,
        "enable_thinking": False,
        "response_format": {"type": "json_object"},
    }


def _normalize_post_semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt known qwen presentation variants without relaxing evidence checks."""

    normalized = dict(payload)
    disposition = str(normalized.get("disposition") or "").strip().casefold()
    normalized["disposition"] = {
        "保留": "keep",
        "partial_keep": "keep",
        "keep_partial": "keep",
        "排除": "exclude",
        "删除": "exclude",
        "drop": "exclude",
        "复核": "review",
        "待复核": "review",
    }.get(disposition, disposition)
    raw_relevance = normalized.get("business_relevance")
    if isinstance(raw_relevance, bool):
        normalized["business_relevance"] = "direct" if raw_relevance else "none"
    elif isinstance(raw_relevance, (int, float)):
        # Qwen sometimes emits a relevance score despite the categorical
        # contract.  These observed score bands only map the taxonomy; source
        # block coverage and the rest of the semantic contract stay strict.
        normalized["business_relevance"] = (
            "direct"
            if raw_relevance >= 0.65
            else "transferable"
            if raw_relevance >= 0.35
            else "none"
        )
    else:
        relevance = str(raw_relevance or "").strip().casefold()
        normalized["business_relevance"] = {
            "直接": "direct",
            "直接相关": "direct",
            "强相关": "direct",
            "high": "direct",
            "高度相关": "direct",
            "可迁移": "transferable",
            "可复用": "transferable",
            "可迁移复用": "transferable",
            "medium": "transferable",
            "中等相关": "transferable",
            "无关": "none",
            "不相关": "none",
            "范围外": "none",
            "low": "none",
            "不确定": "ambiguous",
            "模糊": "ambiguous",
        }.get(relevance, relevance)
    reason_code = str(normalized.get("reason_code") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", reason_code):
        normalized["reason_code"] = {
            "keep": "business_method",
            "exclude": "out_of_scope",
            "review": "ambiguous_evidence",
        }.get(str(normalized.get("disposition") or ""), "invalid_model_reason")
    confidence = normalized.get("confidence")
    if isinstance(confidence, str):
        raw_confidence = confidence.strip().casefold()
        known_confidence = {
            "高": 0.8,
            "较高": 0.8,
            "很高": 0.9,
            "中": 0.5,
            "中等": 0.5,
            "一般": 0.5,
            "低": 0.2,
            "较低": 0.2,
            "很低": 0.1,
        }
        if raw_confidence in known_confidence:
            normalized["confidence"] = known_confidence[raw_confidence]
        else:
            try:
                normalized["confidence"] = (
                    float(raw_confidence[:-1]) / 100
                    if raw_confidence.endswith("%")
                    else float(raw_confidence)
                )
            except ValueError:
                pass
    if isinstance(normalized.get("time_sensitive"), str):
        value = str(normalized["time_sensitive"]).strip().casefold()
        normalized["time_sensitive"] = {
            "是": True,
            "true": True,
            "否": False,
            "false": False,
        }.get(value, normalized["time_sensitive"])
    return normalized


def validate_semantic_response(
    strategy_code: str,
    payload: dict[str, Any],
    *,
    available_block_ids: set[str],
    source_blocks: dict[str, str] | None = None,
) -> PostSemanticResponse:
    if strategy_code not in POST_STRATEGIES:
        raise ValueError("该校验器只适用于帖子/文章策略")
    normalized = _normalize_post_semantic_payload(payload)
    raw_units = normalized.get("knowledge_units")
    normalized["knowledge_units"] = []
    result = PostSemanticResponse.model_validate(normalized)
    referenced = result.keep_block_ids + result.drop_block_ids
    if len(referenced) != len(set(referenced)):
        raise ValueError("来源块不能重复归类")
    unknown = set(referenced) - available_block_ids
    if unknown:
        raise ValueError(f"模型返回未知来源块：{sorted(unknown)}")
    if result.disposition == "keep" and not result.keep_block_ids:
        raise ValueError("保留内容必须至少包含一个来源块")
    kept_ids = set(result.keep_block_ids)
    unit_ids: set[str] = set()
    evidence = "\n".join(
        source_blocks[block_id]
        for block_id in result.keep_block_ids
        if source_blocks and block_id in source_blocks
    )
    valid_units: list[PostKnowledgeUnit] = []
    for raw_unit in raw_units if isinstance(raw_units, list) else []:
        try:
            unit = PostKnowledgeUnit.model_validate(raw_unit)
        except Exception:
            continue
        if unit.unit_id in unit_ids:
            continue
        unit_ids.add(unit.unit_id)
        unknown_unit_blocks = set(unit.source_block_ids) - available_block_ids
        if unknown_unit_blocks:
            continue
        if not set(unit.source_block_ids) <= kept_ids:
            continue
        if source_blocks is not None:
            unit_text = "\n".join(
                [
                    unit.problem,
                    unit.conclusion,
                    unit.reasoning,
                    *unit.applicable_conditions,
                    *unit.actions,
                    *unit.risks_or_limits,
                ]
            )
            numeric_tokens = re.findall(
                r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?%?(?![A-Za-z0-9_])",
                unit_text,
            )
            unit_evidence = "\n".join(source_blocks[block_id] for block_id in unit.source_block_ids)
            if any(token not in unit_evidence for token in numeric_tokens):
                continue
        valid_units.append(unit)
    result.knowledge_units = valid_units
    if (
        source_blocks is not None
        and result.disposition in {"keep", "review"}
        and result.keep_block_ids
        and not result.knowledge_units
    ):
        raise ValueError("模型未返回可检索的知识单元")
    if (
        source_blocks is not None
        and result.disposition == "keep"
        and result.knowledge_units
        and not evidence
    ):
        raise ValueError("knowledge_unit缺少来源证据")
    return result


def validate_consultation_response(
    payload: dict[str, Any],
    *,
    available_block_ids: set[str],
    source_blocks: dict[str, str] | None = None,
) -> ConsultationSemanticResponse:
    result = ConsultationSemanticResponse.model_validate(_normalize_consultation_payload(payload))
    headings = [section.heading for section in result.sections]
    if len(headings) != len(set(headings)):
        raise ValueError("咨询结构章节不能重复")
    for section in result.sections:
        unknown = set(section.source_block_ids) - available_block_ids
        if unknown:
            raise ValueError(f"咨询结果引用未知来源块：{sorted(unknown)}")
        if source_blocks is not None:
            evidence = "\n".join(source_blocks[block_id] for block_id in section.source_block_ids)
            numeric_tokens = re.findall(
                r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?%?(?![A-Za-z0-9_])",
                section.text,
            )
            if any(token not in evidence for token in numeric_tokens):
                raise ValueError("咨询结果包含来源块未支持的数字或日期")
            # Chinese negation has interchangeable surface forms (for example
            # "未" and "没有").  Requiring the exact same spelling rejected
            # faithful summaries, including table-extraction results.  Keep
            # the gate, but compare the semantic category rather than a
            # particular character sequence.
            negations = ("没有", "不能", "并非", "不", "未", "无")
            if any(value in section.text for value in negations) and not any(
                value in evidence for value in negations
            ):
                raise ValueError("咨询结果改变了来源块的否定状态")
            people = set(re.findall(r"案例人物[A-Z]", section.text))
            if any(person not in evidence for person in people):
                raise ValueError("咨询结果改变了来源块的人物归属")
    state_contract = {
        "顾问判断": {"advisor_judgment"},
        "已确认决定": {"decision"},
        "行动计划": {"plan", "decision"},
        "实际结果": {"actual_result"},
    }
    for section in result.sections:
        allowed = state_contract.get(section.heading)
        if allowed and section.fact_state not in allowed:
            raise ValueError("咨询章节与事实状态不一致")
    return result


def _normalize_consultation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the fixed Chinese fact-state labels emitted by qwen to the schema.

    This is intentionally a closed mapping: unknown values still fail validation,
    and no source, number, negation, or section constraints are relaxed.
    """

    normalized = dict(payload)
    sections = normalized.get("sections")
    if not isinstance(sections, list):
        return normalized
    fact_states = {
        "客户陈述": "client_statement",
        "客户描述": "client_statement",
        "顾问判断": "advisor_judgment",
        "已确认决定": "decision",
        "决策": "decision",
        "行动计划": "plan",
        "计划": "plan",
        "实际结果": "actual_result",
    }
    normalized_sections: list[Any] = []
    for section in sections:
        if not isinstance(section, dict):
            normalized_sections.append(section)
            continue
        normalized_section = dict(section)
        # Qwen occasionally emits an empty template placeholder for a heading
        # without evidence.  It conveys no business fact and the prompt
        # expressly says not to output such headings, so discard only the
        # fully empty placeholder.  Any partially populated malformed section
        # remains a contract error for human review.
        if not str(normalized_section.get("text") or "").strip() and not normalized_section.get(
            "source_block_ids"
        ):
            continue
        value = str(normalized_section.get("fact_state") or "").strip()
        normalized_section["fact_state"] = fact_states.get(value, value)
        normalized_sections.append(normalized_section)
    normalized["sections"] = normalized_sections
    return normalized


class BailianClient:
    """Minimal OpenAI-compatible Bailian client for realtime and Batch APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 180,
    ) -> None:
        if not api_key:
            raise ValueError("百炼 API Key 未配置")
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=f"{self.base_url}/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds, connect=10),
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = self.client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response
                last_error = httpx.HTTPStatusError(
                    "百炼接口暂时不可用",
                    request=response.request,
                    response=response,
                )
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = float(2**attempt)
                if attempt < 4:
                    time.sleep(min(60.0, max(1.0, delay)))
                    continue
            if attempt < 4:
                time.sleep(min(60.0, float(2**attempt)))
        assert last_error is not None
        raise last_error

    def close(self) -> None:
        self.client.close()

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "qwen-plus",
        max_tokens: int = 8192,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        try:
            response = self._request(
                "POST",
                "chat/completions",
                json=build_model_request_body(messages, model=model, max_tokens=max_tokens),
            )
        except Exception as exc:
            raise ModelNoResponseError("模型服务未返回可用响应") from exc
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ModelNoResponseError("模型响应缺少可用消息内容") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelNoResponseError("模型返回内容为空")
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise ModelPayloadError("模型返回了非 JSON 内容") from exc
        if not isinstance(parsed, dict):
            raise ModelPayloadError("百炼结构化输出必须是JSON对象")
        usage = payload.get("usage") or {}
        return parsed, {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        }

    def upload_batch_file(self, *, name: str, content: bytes) -> str:
        response = self._request(
            "POST",
            "files",
            data={"purpose": "batch"},
            files={"file": (name, content, "application/jsonl")},
        )
        file_id = str(response.json().get("id") or "")
        if not file_id:
            raise ValueError("百炼文件上传响应缺少file id")
        return file_id

    def upload_batch_path(self, path: str) -> str:
        with open(path, "rb") as handle:
            response = self._request(
                "POST",
                "files",
                data={"purpose": "batch"},
                files={
                    "file": (
                        Path(path).name,
                        handle,
                        "application/jsonl",
                    )
                },
            )
        file_id = str(response.json().get("id") or "")
        if not file_id:
            raise ValueError("百炼文件上传响应缺少file id")
        return file_id

    def create_batch(self, input_file_id: str) -> str:
        response = self._request(
            "POST",
            "batches",
            json={
                "input_file_id": input_file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            },
        )
        batch_id = str(response.json().get("id") or "")
        if not batch_id:
            raise ValueError("百炼Batch创建响应缺少batch id")
        return batch_id

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        response = self._request("GET", f"batches/{batch_id}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("百炼Batch状态响应无效")
        return payload

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        response = self._request("POST", f"batches/{batch_id}/cancel")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def get_file_content(self, file_id: str) -> bytes:
        response = self._request("GET", f"files/{file_id}/content")
        return response.content

    def delete_file(self, file_id: str) -> None:
        try:
            self._request("DELETE", f"files/{file_id}")
        except httpx.HTTPStatusError as exc:
            # Provider Batch completion may remove its transient input/output
            # files before our cleanup task runs.  Deletion is idempotent for
            # this resource, so a missing file is already the desired state.
            if exc.response is not None and exc.response.status_code == 404:
                return
            raise


def batch_request_line(
    request: PromptRequest,
    *,
    prompt_version: str,
    model: str = "qwen-plus",
    repair_hint: str = "",
    max_tokens: int = 8192,
) -> bytes:
    line = {
        "custom_id": build_custom_id(request, prompt_version),
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": build_model_request_body(
            build_prompt_messages(request, repair_hint=repair_hint),
            model=model,
            max_tokens=max_tokens,
        ),
    }
    return (json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


__all__ = [
    "BailianClient",
    "ConsultationSemanticResponse",
    "CourseEnrichmentResponse",
    "PostSemanticResponse",
    "PromptRequest",
    "batch_request_line",
    "build_custom_id",
    "build_model_request_body",
    "build_prompt_messages",
    "choose_call_mode",
    "validate_consultation_response",
    "validate_semantic_response",
]
