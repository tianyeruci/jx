#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
老板活动 Markdown -> RAG 清洗 + metadata + 可选 Dify 上传。

设计原则：
1. 一个源 Markdown 可以包含多个活动。
2. 一个有效活动输出为一个独立 Markdown / Dify Document。
3. 必要事实缺失：记录并跳过该活动，不影响后续活动。
4. AI 只负责归纳/分类/检索增强，不允许编造时间、地点、价格、报名、嘉宾、状态等事实。
5. metadata 既写审计 JSON，也可通过 Dify Knowledge API 写入真正的 Document Metadata。
6. 默认使用 qwen3.7-plus，关闭思考模式，严格 JSON Schema + Pydantic + 业务语义校验。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
VOCAB_PATH = SCRIPT_DIR / "controlled_vocab.json"

DEFAULT_BASE_DIR = Path(os.getenv("EVENT_BASE_DIR", "/opt/ai-management/tmp/活动/"))
DEFAULT_TZ = os.getenv("EVENT_TIMEZONE", "Asia/Shanghai")
SCHEMA_VERSION = "event_enrichment_v2"

FIELD_ALIASES: Dict[str, List[str]] = {
    "name": ["活动名称", "名称", "活动名"],
    "business_event_id": ["活动编号", "活动ID", "活动id", "event_id"],
    "time_raw": ["活动时间", "时间", "举办时间", "日期"],
    "delivery_mode_raw": ["活动形式", "形式", "活动方式", "举办形式"],
    "location": ["城市/地点", "城市地点", "活动地点", "地点", "地址"],
    "main_content": ["活动主要讲什么", "主要内容", "活动内容", "内容", "活动介绍"],
    "problems": ["解决问题", "主要解决的问题", "痛点", "问题"],
    "target_audience": ["适合什么人", "适合人群", "目标人群", "参与对象", "适用人群"],
    "registration_method": ["报名方式", "参与方式"],
    "registration_deadline_raw": ["报名截止时间", "报名截止", "截止时间"],
    "price": ["价格", "费用", "活动费用", "报名费用"],
    "registration_link": ["报名链接", "报名地址", "链接"],
    "guests": ["嘉宾/老师", "嘉宾", "老师", "讲师", "主讲人"],
    "source_status_raw": ["活动状态", "状态", "报名状态"],
    "event_type_raw": ["活动类型", "类型"],
}

REQUIRED_CANONICAL = ["name", "time_raw", "delivery_mode", "main_content", "source_status_raw"]


def load_vocab() -> Dict[str, List[str]]:
    return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))


VOCAB = load_vocab()
EVENT_TYPES = set(VOCAB["event_types"])
TOPICS = set(VOCAB["topics"])
INDUSTRIES = set(VOCAB["industries"])
BUSINESS_STAGES = set(VOCAB["business_stages"])


class AIEnrichment(BaseModel):
    """模型只输出语义增强字段；事实字段不交给模型决定。"""

    model_config = ConfigDict(extra="forbid")

    target_audience: str = Field(min_length=2, max_length=240)
    event_type: str
    problems: List[str] = Field(min_length=1, max_length=8)
    topics: List[str] = Field(min_length=1, max_length=3)
    industries: List[str] = Field(default_factory=list, max_length=2)
    business_stages: List[str] = Field(default_factory=list, max_length=2)
    retrieval_summary: str = Field(min_length=20, max_length=500)
    search_phrases: List[str] = Field(min_length=3, max_length=10)

    @field_validator("target_audience", "retrieval_summary")
    @classmethod
    def strip_required_text(cls, v: str) -> str:
        v = clean_value(v)
        if not v:
            raise ValueError("字段不能为空")
        return v

    @field_validator("event_type")
    @classmethod
    def valid_event_type(cls, v: str) -> str:
        v = clean_value(v)
        if v not in EVENT_TYPES:
            raise ValueError(f"event_type 必须来自受控枚举: {sorted(EVENT_TYPES)}")
        return v

    @field_validator("problems", "search_phrases")
    @classmethod
    def clean_nonempty_list(cls, v: List[str]) -> List[str]:
        out = [clean_value(x) for x in v if clean_value(x)]
        out = list(dict.fromkeys(out))
        if not out:
            raise ValueError("列表不能为空")
        return out

    @field_validator("topics")
    @classmethod
    def valid_topics(cls, v: List[str]) -> List[str]:
        v = [clean_value(x) for x in v if clean_value(x)]
        bad = [x for x in v if x not in TOPICS]
        if bad:
            raise ValueError(f"非法 topic: {bad}")
        out = list(dict.fromkeys(v))[:3]
        if not out:
            raise ValueError("topics 至少一个")
        return out

    @field_validator("industries")
    @classmethod
    def valid_industries(cls, v: List[str]) -> List[str]:
        v = [clean_value(x) for x in v if clean_value(x)]
        bad = [x for x in v if x not in INDUSTRIES]
        if bad:
            raise ValueError(f"非法 industry: {bad}")
        return list(dict.fromkeys(v))[:2]

    @field_validator("business_stages")
    @classmethod
    def valid_business_stages(cls, v: List[str]) -> List[str]:
        v = [clean_value(x) for x in v if clean_value(x)]
        bad = [x for x in v if x not in BUSINESS_STAGES]
        if bad:
            raise ValueError(f"非法 business_stage: {bad}")
        return list(dict.fromkeys(v))[:2]


class SplitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: List[str] = Field(min_length=1)

    @field_validator("events")
    @classmethod
    def clean_events(cls, v: List[str]) -> List[str]:
        out = [x.strip() for x in v if isinstance(x, str) and x.strip()]
        if not out:
            raise ValueError("events 不能为空")
        return out


@dataclass
class EventFacts:
    name: str = ""
    business_event_id: str = ""
    time_raw: str = ""
    delivery_mode_raw: str = ""
    delivery_mode: str = ""
    location: str = ""
    city: str = ""
    main_content: str = ""
    problems_raw: str = ""
    target_audience_raw: str = ""
    registration_method: str = ""
    registration_deadline_raw: str = ""
    price: str = ""
    registration_link: str = ""
    guests: str = ""
    source_status_raw: str = ""
    source_status: str = ""
    event_type_raw: str = ""
    raw_text: str = ""
    source_file: str = ""
    source_index: int = 0


@dataclass
class ParsedTime:
    start: datetime
    end: datetime
    start_has_time: bool
    end_has_time: bool
    raw: str


class QwenCallError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: Optional[int] = None, request_id: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id


def enrichment_json_schema() -> Dict[str, Any]:
    """固定复用的严格 Schema。长度/数量等业务约束留给 Pydantic，避免依赖平台未明确承诺的 JSON Schema 关键字。"""
    return {
        "type": "object",
        "properties": {
            "target_audience": {"type": "string"},
            "event_type": {"type": "string", "enum": sorted(EVENT_TYPES)},
            "problems": {"type": "array", "items": {"type": "string"}},
            "topics": {"type": "array", "items": {"type": "string", "enum": sorted(TOPICS)}},
            "industries": {"type": "array", "items": {"type": "string", "enum": sorted(INDUSTRIES)}},
            "business_stages": {"type": "array", "items": {"type": "string", "enum": sorted(BUSINESS_STAGES)}},
            "retrieval_summary": {"type": "string"},
            "search_phrases": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "target_audience", "event_type", "problems", "topics", "industries",
            "business_stages", "retrieval_summary", "search_phrases",
        ],
        "additionalProperties": False,
    }


def split_json_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {"events": {"type": "array", "items": {"type": "string"}}},
        "required": ["events"],
        "additionalProperties": False,
    }


_HARD_FACT_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?"),
    re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"),
    re.compile(r"(?<!\d)\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(r"(?<!\d)\d{1,2}\s*[:：]\s*\d{2}(?!\d)"),
    re.compile(r"(?:￥|¥)\s*\d+(?:\.\d+)?"),
    re.compile(r"\d+(?:\.\d+)?\s*元"),
    re.compile(r"(?<![A-Za-z])(?:免费|无费用|无需付费)(?![A-Za-z])"),
    re.compile(r"https?://[^\s)）]+", re.I),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
]
_COMMON_CITIES = [
    "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "苏州", "成都", "武汉",
    "西安", "长沙", "郑州", "青岛", "厦门", "福州", "济南", "合肥", "宁波", "东莞", "佛山",
    "珠海", "无锡", "昆明", "南昌", "沈阳", "大连", "哈尔滨", "长春", "石家庄", "太原",
    "南宁", "海口", "贵阳", "乌鲁木齐", "兰州", "银川", "西宁", "呼和浩特",
]


def validate_ai_business(ai: AIEnrichment, facts: EventFacts) -> None:
    """业务语义校验：AI 只能生成语义字段，不得偷偷加入新的硬事实。"""
    source = facts.raw_text or ""
    generated = "\n".join(
        [ai.target_audience, *ai.problems, ai.retrieval_summary, *ai.search_phrases]
    )
    invented: List[str] = []
    for pat in _HARD_FACT_PATTERNS:
        for m in pat.finditer(generated):
            token = m.group(0).strip().rstrip("。；;，,")
            if token and token not in source:
                invented.append(token)
    for city in _COMMON_CITIES:
        if city in generated and city not in source:
            invented.append(city)
    if invented:
        raise ValueError(f"AI 生成字段包含原文不存在的硬事实: {sorted(set(invented))[:8]}")


class QwenClient:
    def __init__(self, mode: str = "realtime", mock: bool = False):
        self.mock = mock
        self.call_count = 0
        self.retry_count = 0
        self.api_key = os.getenv("BAILIAN_API_KEY", "")
        self.model = os.getenv("QWEN_MODEL", "qwen3.7-plus")
        self.mode = mode
        self.realtime_base_url = os.getenv(
            "QWEN_REALTIME_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.batch_chat_base_url = os.getenv(
            "QWEN_BATCH_CHAT_BASE_URL", "https://batch.dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.batch_file_base_url = os.getenv(
            "QWEN_BATCH_FILE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.timeout = float(os.getenv(
            "QWEN_BATCH_TIMEOUT_SECONDS" if mode == "batch_chat" else "QWEN_TIMEOUT_SECONDS",
            "1800" if mode == "batch_chat" else "180",
        ))
        self.max_retries = max(1, int(os.getenv("QWEN_MAX_RETRIES", "3")))
        self.max_tokens = int(os.getenv("QWEN_MAX_TOKENS", "2500"))
        self.semantic_repair_retries = max(0, int(os.getenv("QWEN_SEMANTIC_REPAIR_RETRIES", "1")))

    def _base_url_for_sync(self) -> str:
        return self.batch_chat_base_url if self.mode == "batch_chat" else self.realtime_base_url

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError("未设置 BAILIAN_API_KEY")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _build_payload(self, system: str, user: str, schema_name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "enable_thinking": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }

    @staticmethod
    def _retryable_status(status: int) -> bool:
        return status in {408, 409, 429} or status >= 500

    def _post_json_with_retry(self, url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        headers = self._headers()
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self.call_count += 1
            if attempt > 1:
                self.retry_count += 1
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
                request_id = resp.headers.get("x-request-id", "")
                if not resp.ok:
                    retryable = self._retryable_status(resp.status_code)
                    err = QwenCallError(
                        f"Qwen HTTP {resp.status_code}: {resp.text[:800]}",
                        retryable=retryable,
                        status_code=resp.status_code,
                        request_id=request_id,
                    )
                    if not retryable or attempt >= self.max_retries:
                        raise err
                    last_error = err
                    retry_after = resp.headers.get("Retry-After", "")
                    try:
                        sleep_s = float(retry_after) if retry_after else min(2 ** attempt + random.random(), 60)
                    except ValueError:
                        sleep_s = min(2 ** attempt + random.random(), 60)
                    logging.warning("Qwen 临时错误，第 %s/%s 次，%.1fs 后重试: %s", attempt, self.max_retries, sleep_s, err)
                    time.sleep(sleep_s)
                    continue
                return resp.json()
            except QwenCallError:
                raise
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                sleep_s = min(2 ** attempt + random.random(), 60)
                logging.warning("Qwen 网络/超时错误，第 %s/%s 次，%.1fs 后重试: %s", attempt, self.max_retries, sleep_s, exc)
                time.sleep(sleep_s)
            except requests.RequestException as exc:
                # 其它本地/协议错误不盲目重试。
                raise QwenCallError(f"Qwen 请求失败: {exc}", retryable=False) from exc
            except ValueError as exc:
                raise QwenCallError(f"Qwen 响应不是合法 JSON: {exc}", retryable=False) from exc
        raise QwenCallError(f"Qwen 网络调用最终失败: {last_error}", retryable=True)

    def _chat_structured(self, system: str, user: str, schema_name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        if self.mock:
            raise RuntimeError("mock 模式不应调用真实 _chat_structured")
        payload = self._build_payload(system, user, schema_name, schema)
        data = self._post_json_with_retry(
            f"{self._base_url_for_sync()}/chat/completions", payload, self.timeout
        )
        try:
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise QwenCallError("Qwen 输出因 max_tokens 截断；请提高 QWEN_MAX_TOKENS 或缩小单活动输入", retryable=False)
            if finish_reason not in (None, "stop"):
                raise QwenCallError(f"Qwen 非正常结束: finish_reason={finish_reason}", retryable=False)
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise QwenCallError("Qwen 返回空内容", retryable=False)
            return json.loads(content)
        except QwenCallError:
            raise
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise QwenCallError(f"Qwen 响应结构异常: {exc}", retryable=False) from exc

    def split_events(self, text: str) -> List[str]:
        if self.mock:
            return [text]
        # Batch File 是异步批处理；活动边界兜底切分是少量同步控制任务，固定走实时端点。
        old_mode = self.mode
        try:
            if self.mode == "batch_file":
                self.mode = "realtime"
            system = (
                "你只负责把原始Markdown按活动实例切开。禁止改写、补充、删减、总结。"
                "每个 events 元素必须是原文中的连续活动文本。"
            )
            user = f"请按一个活动一个元素切分。若只能识别一个活动就返回一个元素。\n\n原文：\n{text}"
            data = self._chat_structured(system, user, "event_split_v1", split_json_schema())
            return SplitResponse.model_validate(data).events
        finally:
            self.mode = old_mode

    def build_enrichment_messages(self, facts: EventFacts, repair_error: str = "") -> Tuple[str, str]:
        # 只向模型提供完成语义任务所需证据；硬事实不参与 AI 生成，减少污染与误改风险。
        evidence = {
            "活动名称": facts.name,
            "主要内容": facts.main_content,
            "原始解决问题": facts.problems_raw or None,
            "原始适合人群": facts.target_audience_raw or None,
            "原始活动类型": facts.event_type_raw or None,
        }
        system = (
            "你是RAG活动资料的语义增强器。只做归纳、分类和检索表达生成，不生成或修改活动硬事实。"
            "禁止在输出中新增日期时间、城市地点、价格、报名链接/方式、截止时间、嘉宾、名额、活动状态。"
            "event_type/topics/industries/business_stages 只能从给定受控枚举中选择。"
            "原文已有适合人群/解决问题时必须忠实整理；缺失时才可依据主要内容归纳。"
            "industries/business_stages 没有明确证据时返回空数组。"
        )
        user = (
            "完成一次活动语义增强。retrieval_summary 重点说明活动是什么、主要内容/问题、适合谁；"
            "search_phrases 生成3-10条用户可能检索的短语/问法，不写时间、城市、价格等硬事实。\n\n"
            f"受控词表：\n{json.dumps(VOCAB, ensure_ascii=False)}\n\n"
            f"语义证据：\n{json.dumps(evidence, ensure_ascii=False)}"
        )
        if repair_error:
            user += (
                "\n\n上一次结果未通过程序校验。只修正下述错误对应字段，仍必须遵守原始证据和受控枚举：\n"
                + repair_error[:1500]
            )
        return system, user

    def build_enrichment_payload(self, facts: EventFacts, repair_error: str = "") -> Dict[str, Any]:
        system, user = self.build_enrichment_messages(facts, repair_error)
        return self._build_payload(system, user, SCHEMA_VERSION, enrichment_json_schema())

    def _validate_enrichment_data(self, data: Dict[str, Any], facts: EventFacts) -> AIEnrichment:
        ai = AIEnrichment.model_validate(data)
        validate_ai_business(ai, facts)
        return ai

    def enrich(self, facts: EventFacts) -> AIEnrichment:
        if self.mock:
            event_type = normalize_event_type(facts.event_type_raw, facts.name)
            topics = mock_topics(facts.main_content + " " + facts.problems_raw + " " + facts.name)
            audience = facts.target_audience_raw or "根据活动内容确定的目标参与人群"
            problems = split_semantic_items(facts.problems_raw) or ["根据活动内容提炼的核心问题"]
            phrases = [
                f"{facts.name}是什么活动",
                f"适合参加的{event_type}",
                f"{topics[0]}相关活动",
            ]
            return AIEnrichment(
                target_audience=audience,
                event_type=event_type,
                problems=problems,
                topics=topics,
                industries=[],
                business_stages=[],
                retrieval_summary=f"{facts.name}，面向{audience}，主要内容为：{facts.main_content}",
                search_phrases=phrases,
            )

        last_validation_error = ""
        for repair_idx in range(self.semantic_repair_retries + 1):
            system, user = self.build_enrichment_messages(facts, last_validation_error)
            data = self._chat_structured(system, user, SCHEMA_VERSION, enrichment_json_schema())
            try:
                return self._validate_enrichment_data(data, facts)
            except (ValidationError, ValueError) as exc:
                last_validation_error = str(exc)
                if repair_idx >= self.semantic_repair_retries:
                    raise RuntimeError(f"Qwen 结果业务/结构校验最终失败: {exc}") from exc
                logging.warning("Qwen 结果未通过校验，执行定向修复 %s/%s: %s", repair_idx + 1, self.semantic_repair_retries, exc)
        raise RuntimeError("Qwen 语义增强未获得有效结果")

    def _batch_api_request(self, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
                           files: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None,
                           timeout: float = 180.0) -> requests.Response:
        if not self.api_key:
            raise RuntimeError("未设置 BAILIAN_API_KEY")
        url = f"{self.batch_file_base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if files is None:
            headers["Content-Type"] = "application/json"
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self.call_count += 1
            if attempt > 1:
                self.retry_count += 1
            try:
                resp = requests.request(method, url, headers=headers, json=json_body, files=files, data=data, timeout=timeout)
                if resp.ok:
                    return resp
                retryable = self._retryable_status(resp.status_code)
                err = QwenCallError(
                    f"Qwen Batch File HTTP {resp.status_code}: {resp.text[:800]}",
                    retryable=retryable, status_code=resp.status_code,
                    request_id=resp.headers.get("x-request-id", ""),
                )
                if not retryable or attempt >= self.max_retries:
                    raise err
                last_error = err
            except QwenCallError:
                raise
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
            sleep_s = min(2 ** attempt + random.random(), 60)
            logging.warning("Qwen Batch File 临时错误，第 %s/%s 次，%.1fs 后重试: %s", attempt, self.max_retries, sleep_s, last_error)
            time.sleep(sleep_s)
        raise QwenCallError(f"Qwen Batch File 调用最终失败: {last_error}", retryable=True)

    def build_batch_input_line(self, custom_id: str, facts: EventFacts) -> Dict[str, Any]:
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": self.build_enrichment_payload(facts),
        }

    def parse_batch_output_line(self, row: Dict[str, Any], facts: EventFacts) -> AIEnrichment:
        if row.get("error"):
            raise RuntimeError(f"Batch 子任务失败: {row['error']}")
        response = row.get("response") or {}
        status = int(response.get("status_code") or 0)
        if status != 200:
            raise RuntimeError(f"Batch 子任务 HTTP {status}: {response}")
        try:
            body = response["body"]
            choice = body["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise RuntimeError("Batch 子任务输出被截断")
            if finish_reason not in (None, "stop"):
                raise RuntimeError(f"Batch 子任务非正常结束: finish_reason={finish_reason}")
            content = choice["message"]["content"]
            data = json.loads(content)
            return self._validate_enrichment_data(data, facts)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Batch 子任务响应解析失败: {exc}") from exc

    def enrich_many_batch_file(self, items: List[Tuple[str, EventFacts]], base_dir: Path) -> Dict[str, AIEnrichment]:
        """真正的 Batch File：JSONL 上传 -> 创建异步任务 -> 轮询 -> 下载结果。"""
        if self.mock:
            return {cid: self.enrich(facts) for cid, facts in items}
        if not items:
            return {}
        job_dir = base_dir / "logs" / "batch_jobs" / datetime.now().strftime("%Y%m%d_%H%M%S")
        job_dir.mkdir(parents=True, exist_ok=True)
        input_path = job_dir / "input.jsonl"
        with input_path.open("w", encoding="utf-8") as f:
            for cid, facts in items:
                f.write(json.dumps(self.build_batch_input_line(cid, facts), ensure_ascii=False) + "\n")

        with input_path.open("rb") as fh:
            upload = self._batch_api_request(
                "POST", "/files", files={"file": (input_path.name, fh, "application/jsonl")},
                data={"purpose": "batch"}, timeout=self.timeout,
            ).json()
        input_file_id = upload.get("id")
        if not input_file_id:
            raise RuntimeError(f"Batch File 上传响应缺少 id: {upload}")

        batch = self._batch_api_request(
            "POST", "/batches",
            json_body={
                "input_file_id": input_file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": os.getenv("QWEN_BATCH_COMPLETION_WINDOW", "24h"),
                "metadata": {"schema_version": SCHEMA_VERSION, "task": "event_rag_enrichment"},
            },
            timeout=self.timeout,
        ).json()
        batch_id = batch.get("id")
        if not batch_id:
            raise RuntimeError(f"创建 Batch 响应缺少 id: {batch}")
        (job_dir / "job_created.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")

        poll_seconds = max(1.0, float(os.getenv("QWEN_BATCH_POLL_SECONDS", "10")))
        max_wait = max(60.0, float(os.getenv("QWEN_BATCH_MAX_WAIT_SECONDS", "86400")))
        started = time.monotonic()
        terminal = {"completed", "failed", "expired", "cancelled"}
        while True:
            batch = self._batch_api_request("GET", f"/batches/{batch_id}", timeout=self.timeout).json()
            status = str(batch.get("status", ""))
            if status in terminal:
                break
            if time.monotonic() - started > max_wait:
                raise RuntimeError(f"Batch File 等待超过 QWEN_BATCH_MAX_WAIT_SECONDS，batch_id={batch_id}")
            logging.info("Batch File %s 状态=%s，继续等待", batch_id, status or "unknown")
            time.sleep(poll_seconds)
        (job_dir / "job_final.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        if batch.get("status") != "completed":
            raise RuntimeError(f"Batch File 未成功完成: status={batch.get('status')} batch_id={batch_id}")

        output_file_id = batch.get("output_file_id")
        if not output_file_id:
            raise RuntimeError(f"Batch File completed 但缺少 output_file_id: {batch}")
        output_text = self._batch_api_request("GET", f"/files/{output_file_id}/content", timeout=self.timeout).text
        output_path = job_dir / "output.jsonl"
        output_path.write_text(output_text, encoding="utf-8")
        error_file_id = batch.get("error_file_id")
        if error_file_id:
            error_text = self._batch_api_request("GET", f"/files/{error_file_id}/content", timeout=self.timeout).text
            (job_dir / "error.jsonl").write_text(error_text, encoding="utf-8")

        facts_by_id = {cid: facts for cid, facts in items}
        results: Dict[str, AIEnrichment] = {}
        failures: Dict[str, str] = {}
        for line in output_text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = str(row.get("custom_id", ""))
            if cid not in facts_by_id:
                continue
            try:
                results[cid] = self.parse_batch_output_line(row, facts_by_id[cid])
            except Exception as exc:
                failures[cid] = str(exc)

        missing_ids = [cid for cid in facts_by_id if cid not in results]
        for cid in missing_ids:
            failures.setdefault(cid, "Batch 输出中没有有效结果")

        if failures and os.getenv("QWEN_BATCH_FALLBACK_REALTIME", "true").lower() in {"1", "true", "yes", "on"}:
            logging.warning("Batch File 有 %s 个子任务失败，按配置逐条回退实时 API", len(failures))
            fallback = QwenClient(mode="realtime", mock=False)
            for cid in list(failures):
                try:
                    results[cid] = fallback.enrich(facts_by_id[cid])
                    del failures[cid]
                except Exception as exc:
                    failures[cid] = f"Batch失败后实时回退仍失败: {exc}"
            self.call_count += fallback.call_count
            self.retry_count += fallback.retry_count

        (job_dir / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        # 不让单个 Batch 子任务污染其余结果；调用方按 event_id 分别记录失败。
        for cid, msg in failures.items():
            logging.error("Batch 子任务失败 event_id=%s: %s", cid, msg)
        return results

def clean_value(v: str) -> str:
    v = (v or "").strip()
    v = re.sub(r"^[\s：:]+|[\s。；;]+$", "", v)
    return v.strip()


def alias_pattern(alias: str) -> str:
    return re.escape(alias).replace(r"/", r"\s*/\s*")


def extract_labeled_value(block: str, aliases: Iterable[str]) -> str:
    # 支持“字段：值”，值可跨多行，直到遇到下一个已知字段标签或明显标题。
    all_aliases = [a for xs in FIELD_ALIASES.values() for a in xs]
    alias_union = "|".join(sorted((alias_pattern(a) for a in all_aliases), key=len, reverse=True))
    for alias in sorted(aliases, key=len, reverse=True):
        p = re.compile(
            rf"(?ms)^[ \t]*(?:[-*][ \t]*)?(?:#{{1,6}}[ \t]*)?{alias_pattern(alias)}[ \t]*[：:][ \t]*(.*?)"
            rf"(?=\n[ \t]*(?:[-*][ \t]*)?(?:#{{1,6}}[ \t]*)?(?:{alias_union})[ \t]*[：:]|\Z)"
        )
        m = p.search(block)
        if m:
            return clean_value(m.group(1))
    return ""


def split_by_activity_name(text: str) -> List[str]:
    p = re.compile(r"(?m)^\s*(?:#{1,6}\s*)?活动名称\s*[：:]")
    matches = list(p.finditer(text))
    if not matches:
        return []
    blocks: List[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def infer_delivery_mode(mode_raw: str, location: str) -> str:
    mode_text = (mode_raw or "").strip().lower()
    loc_text = (location or "").strip().lower()
    text = f"{mode_text} {loc_text}".strip()
    if not text:
        return ""
    has_online = any(x in text for x in ["线上", "online", "网络", "腾讯会议", "直播间"])
    has_offline = any(x in text for x in ["线下", "offline", "现场"])
    physical_pat = re.compile(
        r"北京|上海|天津|重庆|广州|深圳|杭州|南京|苏州|成都|武汉|西安|长沙|郑州|青岛|厦门|"
        r"福州|济南|合肥|宁波|东莞|佛山|珠海|无锡|昆明|南昌|沈阳|大连|哈尔滨|长春|石家庄|"
        r"太原|南宁|海口|贵阳|乌鲁木齐|兰州|银川|西宁|呼和浩特|[\u4e00-\u9fff]{2,8}(?:省|市|区|县)|"
        r"(?:路|街|大道|酒店|会议中心|园区|大厦|中心|会场)"
    )
    has_physical_location = bool(loc_text and physical_pat.search(loc_text))
    uncertain_location = is_uncertain_location(location)

    if has_online and (has_offline or (not mode_text and has_physical_location)):
        return "hybrid"
    if has_online:
        return "online"
    if has_offline:
        return "offline"
    # 模板案例/样本存在“只写地点，不单列活动形式”的情况。
    # 仅在地点看起来确实是物理地点时确定性归一为 offline；待定/泛词不猜。
    if location and not uncertain_location and has_physical_location:
        return "offline"
    return ""


def is_uncertain_location(location: str) -> bool:
    """只有纯粹的待定/未公布占位语不能作为线下地点事实。"""
    value = clean_value(location).lower()
    return bool(re.fullmatch(r"(?:待定|待公布|待确认|另行通知|稍后公布|暂无|未知|tbd)", value, re.I))

def extract_city(location: str, delivery_mode: str) -> str:
    if not location or delivery_mode == "online":
        return ""
    loc = location.strip()
    # 优先带“市”的城市。
    m = re.search(r"([\u4e00-\u9fff]{2,8}市)", loc)
    if m:
        return m.group(1).removesuffix("市")
    # 常见直辖市/城市简写。只从地点原文中取，不生成新城市。
    common = [
        "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "苏州", "成都", "武汉",
        "西安", "长沙", "郑州", "青岛", "厦门", "福州", "济南", "合肥", "宁波", "东莞", "佛山",
        "珠海", "无锡", "昆明", "南昌", "沈阳", "大连", "哈尔滨", "长春", "石家庄", "太原",
        "南宁", "海口", "贵阳", "乌鲁木齐", "兰州", "银川", "西宁", "呼和浩特"
    ]
    for city in common:
        if city in loc:
            return city
    return ""


def normalize_source_status(raw: str) -> str:
    x = (raw or "").lower()
    if any(k in x for k in ["取消", "cancel"]):
        return "cancelled"
    if any(k in x for k in ["延期", "改期", "时间待定", "待定"]):
        return "postponed_unknown"
    if any(k in x for k in ["已完成", "已结束", "已举办", "结束"]):
        return "completed"
    if any(k in x for k in ["满员", "已满"]):
        return "full"
    if any(k in x for k in ["报名截止", "停止报名", "关闭报名"]):
        return "registration_closed"
    if any(k in x for k in ["正常", "报名", "进行中", "已开营", "开放", "可报名", "即将开始", "未开始", "待开营"]):
        return "active"
    return "unknown"


def normalize_event_type(raw: str, name: str) -> str:
    rules = [
        ("训练营", ["训练营", "实战营", "增长营", "特训营", "训练班"]),
        ("圆桌会", ["圆桌"]),
        ("私董会", ["私董"]),
        ("工作坊", ["工作坊", "workshop"]),
        ("公开课", ["公开课"]),
        ("直播", ["直播"]),
        ("沙龙", ["沙龙"]),
        ("峰会", ["峰会"]),
        ("论坛", ["论坛"]),
        ("分享会", ["分享会"]),
        ("交流会", ["交流会"]),
        ("课程", ["课程", "课"]),
        ("会议", ["会议", "大会"]),
    ]
    # 源资料明确提供活动类型时优先使用源字段，不能被名称中的其它关键词覆盖。
    raw_text = clean_value(raw)
    if raw_text in EVENT_TYPES:
        return raw_text
    for label, keys in rules:
        if any(k.lower() in raw_text.lower() for k in keys):
            return label
    combined = f"{raw_text} {name}"
    for label, keys in rules:
        if any(k.lower() in combined.lower() for k in keys):
            return label
    return "其他"


def mock_topics(text: str) -> List[str]:
    mapping = [
        ("AI应用", ["ai", "gpt", "人工智能"]),
        ("私域运营", ["私域", "朋友圈", "社群", "微信"]),
        ("个人IP", ["个人ip", "自媒体"]),
        ("内容营销", ["写作", "内容", "公众号"]),
        ("获客增长", ["获客", "增长"]),
        ("销售转化", ["成交", "转化"]),
    ]
    lower = text.lower()
    out = [label for label, keys in mapping if any(k.lower() in lower for k in keys)]
    return out[:3] or ["其他"]


def split_semantic_items(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"[；;。\n]+|(?<!\d)、", text)
    return [clean_value(x) for x in parts if clean_value(x)][:8]


def parse_facts(block: str, source_file: str, source_index: int) -> EventFacts:
    vals = {key: extract_labeled_value(block, aliases) for key, aliases in FIELD_ALIASES.items()}
    mode = infer_delivery_mode(vals["delivery_mode_raw"], vals["location"])
    return EventFacts(
        name=vals["name"],
        business_event_id=vals["business_event_id"],
        time_raw=vals["time_raw"],
        delivery_mode_raw=vals["delivery_mode_raw"],
        delivery_mode=mode,
        location=vals["location"],
        city=extract_city(vals["location"], mode),
        main_content=vals["main_content"],
        problems_raw=vals["problems"],
        target_audience_raw=vals["target_audience"],
        registration_method=vals["registration_method"],
        registration_deadline_raw=vals["registration_deadline_raw"],
        price=vals["price"],
        registration_link=vals["registration_link"],
        guests=vals["guests"],
        source_status_raw=vals["source_status_raw"],
        source_status=normalize_source_status(vals["source_status_raw"]),
        event_type_raw=vals["event_type_raw"],
        raw_text=block.strip(),
        source_file=source_file,
        source_index=source_index,
    )


CHINESE_DT_RE = re.compile(
    r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?"
    r"(?:\s*(?P<h>\d{1,2})\s*[:：]\s*(?P<mi>\d{2}))?"
)
ISO_DT_RE = re.compile(
    r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})"
    r"(?:\s+(?P<h>\d{1,2})\s*[:：]\s*(?P<mi>\d{2}))?"
)


def _dt_from_match(m: re.Match[str], tz: ZoneInfo, end_of_day_if_no_time: bool = False) -> Tuple[datetime, bool]:
    y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
    h = m.groupdict().get("h")
    mi = m.groupdict().get("mi")
    has_time = h is not None
    if has_time:
        return datetime(y, mo, d, int(h), int(mi), tzinfo=tz), True
    if end_of_day_if_no_time:
        return datetime.combine(datetime(y, mo, d).date(), dt_time(23, 59, 59), tzinfo=tz), False
    return datetime(y, mo, d, 0, 0, 0, tzinfo=tz), False


def parse_event_time(raw: str, timezone: str = DEFAULT_TZ) -> ParsedTime:
    text = clean_value(raw).replace("至", "—").replace("到", "—").replace("~", "—").replace("～", "—").replace("–", "—")
    tz = ZoneInfo(timezone)
    first = CHINESE_DT_RE.search(text) or ISO_DT_RE.search(text)
    if not first:
        raise ValueError("活动时间必须包含明确的年、月、日")
    start, start_has_time = _dt_from_match(first, tz, end_of_day_if_no_time=False)
    remainder = text[first.end():].strip()
    remainder = re.sub(r"^[\s—-]+", "", remainder)

    if not remainder:
        # 单个日期但无时刻 -> 覆盖整天；单个明确时刻 -> 结束时刻未知，保守等于开始时刻（不猜时长）。
        if start_has_time:
            end = start
            end_has_time = True
        else:
            end = datetime.combine(start.date(), dt_time(23, 59, 59), tzinfo=tz)
            end_has_time = False
        return ParsedTime(start, end, start_has_time, end_has_time, raw)

    # 1) 完整年月日
    m = CHINESE_DT_RE.search(remainder) or ISO_DT_RE.search(remainder)
    if m:
        end, end_has_time = _dt_from_match(m, tz, end_of_day_if_no_time=True)
        if end < start:
            raise ValueError("活动结束时间早于开始时间")
        return ParsedTime(start, end, start_has_time, end_has_time, raw)

    # 2) 同年：8月23日 [HH:MM]
    m2 = re.search(r"(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?(?:\s*(?P<h>\d{1,2})\s*[:：]\s*(?P<mi>\d{2}))?", remainder)
    if m2:
        has_time = m2.group("h") is not None
        h = int(m2.group("h")) if has_time else 23
        mi = int(m2.group("mi")) if has_time else 59
        sec = 0 if has_time else 59
        end = datetime(start.year, int(m2.group("m")), int(m2.group("d")), h, mi, sec, tzinfo=tz)
        if end < start:
            raise ValueError("活动结束时间早于开始时间")
        return ParsedTime(start, end, start_has_time, has_time, raw)

    # 3) 同年月：23日 [HH:MM]
    m3 = re.search(r"(?P<d>\d{1,2})\s*日(?:\s*(?P<h>\d{1,2})\s*[:：]\s*(?P<mi>\d{2}))?", remainder)
    if m3:
        has_time = m3.group("h") is not None
        h = int(m3.group("h")) if has_time else 23
        mi = int(m3.group("mi")) if has_time else 59
        sec = 0 if has_time else 59
        end = datetime(start.year, start.month, int(m3.group("d")), h, mi, sec, tzinfo=tz)
        if end < start:
            raise ValueError("活动结束时间早于开始时间")
        return ParsedTime(start, end, start_has_time, has_time, raw)

    # 4) 同一天时刻范围：20:00—22:00
    m4 = re.search(r"(?P<h>\d{1,2})\s*[:：]\s*(?P<mi>\d{2})", remainder)
    if m4:
        end = start.replace(hour=int(m4.group("h")), minute=int(m4.group("mi")), second=0)
        if end < start:
            raise ValueError("活动结束时间早于开始时间")
        return ParsedTime(start, end, start_has_time, True, raw)

    raise ValueError("活动时间区间无法可靠解析；不允许 AI 猜时间")


def parse_optional_deadline(raw: str, timezone: str = DEFAULT_TZ) -> Optional[int]:
    if not raw:
        return None
    try:
        p = parse_event_time(raw, timezone)
        # 只有日期的报名截止，应视为该日结束；有明确时刻则用该时刻。
        chosen = p.start if p.start_has_time else p.end
        return int(chosen.timestamp())
    except Exception:
        return None


def validate_required(facts: EventFacts, timezone: str) -> Tuple[List[str], Optional[ParsedTime]]:
    missing: List[str] = []
    if not facts.name:
        missing.append("活动名称")
    if not facts.time_raw:
        missing.append("活动时间")
    if not facts.delivery_mode:
        missing.append("活动形式")
    if not facts.main_content:
        missing.append("主要内容")
    if not facts.source_status_raw:
        missing.append("活动状态")
    elif facts.source_status == "unknown":
        missing.append("活动状态无法标准化")
    if facts.delivery_mode in {"offline", "hybrid"} and (
        not facts.location or is_uncertain_location(facts.location)
    ):
        missing.append("线下/混合活动地点")

    parsed: Optional[ParsedTime] = None
    if facts.time_raw:
        try:
            parsed = parse_event_time(facts.time_raw, timezone)
        except Exception as exc:
            missing.append(f"活动时间不可用({exc})")
    return missing, parsed


def stable_event_id(name: str, start: datetime, business_event_id: str = "") -> str:
    if business_event_id:
        key = f"business_id|{clean_value(business_event_id).lower()}"
    else:
        normalized = re.sub(r"\s+", "", name).lower()
        key = f"{normalized}|{start.strftime('%Y-%m-%d')}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return f"evt_{digest}"


def safe_filename(name: str, event_id: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name).strip().strip(".")
    s = re.sub(r"\s+", "_", s)
    if len(s) > 80:
        s = s[:80]
    return f"{s}__{event_id}.md"


def source_ref(path: str) -> str:
    """在日志/审计输出中用稳定哈希替代老板原始文件名和路径。"""
    resolved = str(Path(path).resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return f"source_{digest}"


def audit_facts(facts: EventFacts) -> Dict[str, Any]:
    """审计 JSON 只保留结构化事实，不持久化原始活动正文或文件标识。"""
    data = asdict(facts)
    data.pop("raw_text", None)
    data.pop("source_file", None)
    return data


def metadata_for_event(facts: EventFacts, ai: AIEnrichment, pt: ParsedTime, event_id: str, timezone: str = DEFAULT_TZ) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "record_type": "event",
        "event_id": event_id,
        "event_type": ai.event_type,
        # Dify String 支持 contains；多值采用 | 分隔，避免把开放文本当枚举。
        "topic": "|".join(ai.topics),
        "industry": "|".join(ai.industries),
        "business_stage": "|".join(ai.business_stages),
        "delivery_mode": facts.delivery_mode,
        "city": facts.city,
        "event_start_ts": int(pt.start.timestamp()),
        "event_end_ts": int(pt.end.timestamp()),
        "source_status": facts.source_status,
        "source_title": facts.name,
    }
    deadline = parse_optional_deadline(facts.registration_deadline_raw, timezone)
    if deadline is not None:
        meta["registration_deadline_ts"] = deadline
    # 不把空值写入 Dify metadata，减少无意义字段。
    return {k: v for k, v in meta.items() if v not in (None, "", [])}


def render_markdown(facts: EventFacts, ai: AIEnrichment, pt: ParsedTime, event_id: str) -> str:
    """只输出会进入向量索引的业务正文；审计 metadata 独立写 .metadata.json，避免污染检索。"""
    child = os.getenv("DIFY_CHILD_SEPARATOR", "<<<DIFY_CHILD>>>")
    problems = ai.problems or split_semantic_items(facts.problems_raw)
    problem_md = "\n".join(f"- {x}" for x in problems) if problems else "- 未提供"
    phrases_md = "\n".join(f"- {x}" for x in ai.search_phrases)

    mode_label = {"online": "线上", "offline": "线下", "hybrid": "线上+线下"}.get(facts.delivery_mode, facts.delivery_mode)
    facts_lines = [
        f"- 活动类型：{ai.event_type}",
        f"- 活动时间：{facts.time_raw}",
        f"- 活动形式：{mode_label}",
        f"- 地点：{facts.location or ('线上' if facts.delivery_mode == 'online' else '未提供')}",
        f"- 活动状态：{facts.source_status_raw}",
        f"- 适合人群：{ai.target_audience}",
    ]
    optional = [
        ("报名截止时间", facts.registration_deadline_raw),
        ("报名方式", facts.registration_method),
        ("报名链接", facts.registration_link),
        ("价格", facts.price),
        ("嘉宾/老师", facts.guests),
    ]
    facts_lines.extend(f"- {k}：{v}" for k, v in optional if v)

    return f"""# {facts.name}

{child}

## 活动检索摘要

{ai.retrieval_summary}

{child}

## 活动事实

{chr(10).join(facts_lines)}

{child}

## 主要内容与解决问题

### 主要内容

{facts.main_content}

### 解决问题

{problem_md}

{child}

## 适合人群

{ai.target_audience}

{child}

## 用户可能的检索表达

{phrases_md}
""".strip() + "\n"


DIFY_METADATA_TYPES: Dict[str, str] = {
    "record_type": "string",
    "event_id": "string",
    "event_type": "string",
    "topic": "string",
    "industry": "string",
    "business_stage": "string",
    "delivery_mode": "string",
    "city": "string",
    "event_start_ts": "number",
    "event_end_ts": "number",
    "registration_deadline_ts": "number",
    "source_status": "string",
    "source_title": "string",
}


class DifyClient:
    def __init__(self):
        self.base = os.getenv("DIFY_API_BASE", "").rstrip("/")
        self.api_key = os.getenv("DIFY_API_KEY", "")
        self.dataset_id = os.getenv("DIFY_DATASET_ID", "")
        self.timeout = float(os.getenv("DIFY_TIMEOUT_SECONDS", "60"))
        if not (self.base and self.api_key and self.dataset_id):
            raise RuntimeError("启用 --upload-dify 时必须设置 DIFY_API_BASE / DIFY_API_KEY / DIFY_DATASET_ID")

    @property
    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        resp = requests.request(method, url, headers=self.headers, timeout=self.timeout, **kwargs)
        if not resp.ok:
            raise RuntimeError(f"Dify HTTP {resp.status_code} {path}: {resp.text[:1000]}")
        if not resp.text.strip():
            return {}
        return resp.json()

    def ensure_metadata_fields(self) -> Dict[str, Dict[str, Any]]:
        data = self._request("GET", f"/datasets/{self.dataset_id}/metadata")
        existing = {x["name"]: x for x in data.get("doc_metadata", [])}
        for name, type_ in DIFY_METADATA_TYPES.items():
            if name in existing:
                if existing[name].get("type") != type_:
                    raise RuntimeError(
                        f"Dify metadata 字段类型冲突: {name} 现有={existing[name].get('type')} 需要={type_}"
                    )
                continue
            created = self._request(
                "POST", f"/datasets/{self.dataset_id}/metadata", json={"type": type_, "name": name}
            )
            existing[name] = created
        return existing

    def hierarchical_process_rule(self) -> Dict[str, Any]:
        parent_sep = os.getenv("DIFY_PARENT_SEPARATOR", "<<<DIFY_PARENT>>>")
        child_sep = os.getenv("DIFY_CHILD_SEPARATOR", "<<<DIFY_CHILD>>>")
        parent_max = int(os.getenv("DIFY_PARENT_MAX_TOKENS", "4000"))
        child_max = int(os.getenv("DIFY_CHILD_MAX_TOKENS", "800"))
        return {
            "mode": "hierarchical",
            "rules": {
                "pre_processing_rules": [
                    {"id": "remove_extra_spaces", "enabled": False},
                    {"id": "remove_urls_emails", "enabled": False},
                ],
                "parent_mode": "paragraph",
                "segmentation": {
                    "separator": parent_sep,
                    "max_tokens": parent_max,
                    "chunk_overlap": 0,
                },
                "subchunk_segmentation": {
                    "separator": child_sep,
                    "max_tokens": child_max,
                    "chunk_overlap": 50,
                },
            },
        }

    def create_document(self, name: str, text: str) -> Dict[str, Any]:
        payload = {
            "name": name,
            "text": text,
            "indexing_technique": "high_quality",
            "doc_form": "hierarchical_model",
            "doc_language": "Chinese",
            "process_rule": self.hierarchical_process_rule(),
        }
        return self._request(
            "POST", f"/datasets/{self.dataset_id}/document/create-by-text", json=payload
        )

    def update_document(self, document_id: str, name: str, text: str) -> Dict[str, Any]:
        payload = {
            "name": name,
            "text": text,
            "doc_form": "hierarchical_model",
            "doc_language": "Chinese",
            "process_rule": self.hierarchical_process_rule(),
        }
        return self._request(
            "POST", f"/datasets/{self.dataset_id}/documents/{document_id}/update-by-text", json=payload
        )

    def set_document_metadata(
        self,
        document_id: str,
        metadata: Dict[str, Any],
        fields: Dict[str, Dict[str, Any]],
    ) -> None:
        metadata_list = []
        for name, value in metadata.items():
            if name not in fields:
                continue
            metadata_list.append({"id": fields[name]["id"], "name": name, "value": value})
        payload = {
            "operation_data": [
                {
                    "document_id": document_id,
                    "metadata_list": metadata_list,
                    "partial_update": False,
                }
            ]
        }
        self._request("POST", f"/datasets/{self.dataset_id}/documents/metadata", json=payload)


class RunLogger:
    def __init__(self, base_dir: Path):
        self.log_dir = base_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.skipped_path = self.log_dir / "skipped_events.jsonl"
        self.errors_path = self.log_dir / "errors.jsonl"
        self.summary_path = self.log_dir / "run_summary.json"
        self.summary = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "files": 0,
            "events_seen": 0,
            "events_cleaned": 0,
            "events_skipped": 0,
            "events_failed": 0,
            "dify_uploaded": 0,
            "dify_updated": 0,
            "qwen_calls": 0,
            "qwen_retries": 0,
        }
        # 每次运行的审计文件只记录本次结果，避免重复运行累积旧条目。
        for path in (self.skipped_path, self.errors_path):
            path.write_text("", encoding="utf-8")

    def jsonl(self, path: Path, obj: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def skipped(self, facts: EventFacts, reasons: List[str]) -> None:
        self.summary["events_skipped"] += 1
        self.jsonl(
            self.skipped_path,
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "source_file": facts.source_file,
                "source_index": facts.source_index,
                "event_name": facts.name or None,
                "reason": "missing_or_invalid_required_fields",
                "missing_or_invalid": reasons,
            },
        )

    def error(self, facts: Optional[EventFacts], error: Exception, stage: str, source_file: str = "") -> None:
        self.summary["events_failed"] += 1
        self.jsonl(
            self.errors_path,
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "source_file": facts.source_file if facts else source_file,
                "source_index": facts.source_index if facts else None,
                "event_name": facts.name if facts else None,
                "stage": stage,
                "error": str(error),
            },
        )

    def finish(self, qwen: Optional["QwenClient"] = None) -> None:
        if qwen is not None:
            self.summary["qwen_calls"] = qwen.call_count
            self.summary["qwen_retries"] = qwen.retry_count
        self.summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.summary_path.write_text(json.dumps(self.summary, ensure_ascii=False, indent=2), encoding="utf-8")


def find_input_files(base_dir: Path) -> List[Path]:
    input_dir = base_dir / "input"
    if input_dir.exists():
        files = sorted(input_dir.rglob("*.md"))
        if files:
            return files
    excluded = {"output", "logs", "archive", ".git", ".venv", "venv"}
    files = []
    for p in base_dir.rglob("*.md"):
        try:
            rel_parts = p.relative_to(base_dir).parts
        except ValueError:
            continue
        if any(x in excluded for x in rel_parts):
            continue
        files.append(p)
    return sorted(files)


def load_state(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: Dict[str, Dict[str, Any]]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_source_overrides(facts: EventFacts, ai: AIEnrichment) -> AIEnrichment:
    """老板提供的事实优先于 AI；模型不能覆盖源事实。"""
    data = ai.model_dump()
    if facts.target_audience_raw:
        data["target_audience"] = clean_value(facts.target_audience_raw)
    if facts.event_type_raw:
        data["event_type"] = normalize_event_type(facts.event_type_raw, facts.name)
    if facts.problems_raw:
        data["problems"] = split_semantic_items(facts.problems_raw)
    return AIEnrichment.model_validate(data)


def finalize_event(
    facts: EventFacts,
    ai: AIEnrichment,
    pt: ParsedTime,
    event_id: str,
    base_dir: Path,
    timezone: str,
    runlog: RunLogger,
    dify: Optional[DifyClient],
    dify_fields: Optional[Dict[str, Dict[str, Any]]],
    state: Dict[str, Dict[str, Any]],
    state_path: Path,
    qwen_mode: str,
) -> None:
    ai = apply_source_overrides(facts, ai)
    # 源事实覆盖后再做一次业务校验；源事实本身允许包含硬事实。
    validate_ai_business(ai, facts)

    metadata = metadata_for_event(facts, ai, pt, event_id, timezone)
    markdown = render_markdown(facts, ai, pt, event_id)

    out_clean = base_dir / "output" / "cleaned"
    out_meta = base_dir / "output" / "metadata"
    out_clean.mkdir(parents=True, exist_ok=True)
    out_meta.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(facts.name, event_id)
    md_path = out_clean / filename
    meta_path = out_meta / filename.replace(".md", ".metadata.json")
    md_path.write_text(markdown, encoding="utf-8")
    meta_doc = {
        "schema_version": SCHEMA_VERSION,
        "qwen_mode": qwen_mode,
        "event_id": event_id,
        "source_ref": facts.source_file,
        "source_index": facts.source_index,
        "facts": audit_facts(facts),
        "parsed_time": {
            "start_iso": pt.start.isoformat(),
            "end_iso": pt.end.isoformat(),
            "start_has_time": pt.start_has_time,
            "end_has_time": pt.end_has_time,
        },
        "ai_enrichment": ai.model_dump(),
        "dify_metadata": metadata,
        "output_markdown": str(md_path),
    }
    meta_path.write_text(json.dumps(meta_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = base_dir / "output" / "manifest.jsonl"
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event_id": event_id,
            "event_name": facts.name,
            "source_ref": facts.source_file,
            "markdown": str(md_path),
            "metadata": str(meta_path),
            "schema_version": SCHEMA_VERSION,
        }, ensure_ascii=False) + "\n")

    runlog.summary["events_cleaned"] += 1

    if dify is not None and dify_fields is not None:
        doc_name = filename
        known = state.get(event_id, {})
        if known.get("document_id"):
            result = dify.update_document(known["document_id"], doc_name, markdown)
            document_id = result.get("document", {}).get("id") or known["document_id"]
            runlog.summary["dify_updated"] += 1
            action = "updated"
        else:
            result = dify.create_document(doc_name, markdown)
            document_id = result.get("document", {}).get("id")
            if not document_id:
                raise RuntimeError(f"Dify 创建成功响应中没有 document.id: {result}")
            runlog.summary["dify_uploaded"] += 1
            action = "created"
        # 远端创建/更新是有副作用的：拿到 document_id 立即落盘，避免后续 metadata 写入失败后重跑又创建重复文档。
        state[event_id] = {
            "document_id": document_id,
            "dataset_id": dify.dataset_id,
            "last_action": action,
            "metadata_synced": False,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "source_ref": facts.source_file,
        }
        save_state(state_path, state)
        dify.set_document_metadata(document_id, metadata, dify_fields)
        state[event_id]["metadata_synced"] = True
        state[event_id]["metadata_synced_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(state_path, state)


def process_one_event(
    facts: EventFacts,
    qwen: QwenClient,
    base_dir: Path,
    timezone: str,
    runlog: RunLogger,
    dify: Optional[DifyClient],
    dify_fields: Optional[Dict[str, Dict[str, Any]]],
    state: Dict[str, Dict[str, Any]],
    state_path: Path,
) -> None:
    missing, pt = validate_required(facts, timezone)
    if missing or pt is None:
        runlog.skipped(facts, missing)
        logging.warning("跳过活动 #%s %s: %s", facts.source_index, facts.name or "<无名称>", ", ".join(missing))
        return

    event_id = stable_event_id(facts.name, pt.start, facts.business_event_id)
    ai = qwen.enrich(facts)
    finalize_event(
        facts=facts, ai=ai, pt=pt, event_id=event_id,
        base_dir=base_dir, timezone=timezone, runlog=runlog,
        dify=dify, dify_fields=dify_fields, state=state, state_path=state_path,
        qwen_mode=qwen.mode,
    )

def run(args: argparse.Namespace) -> int:
    base_dir = Path(args.base_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "output").mkdir(parents=True, exist_ok=True)
    (base_dir / "logs").mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(base_dir / "logs" / "run.log", mode="w", encoding="utf-8"),
        ],
    )

    runlog = RunLogger(base_dir)
    runlog.summary["schema_version"] = SCHEMA_VERSION
    runlog.summary["qwen_mode"] = args.qwen_mode
    qwen = QwenClient(mode=args.qwen_mode, mock=args.mock_ai)
    (base_dir / "output" / "manifest.jsonl").write_text("", encoding="utf-8")
    files = find_input_files(base_dir)
    if args.file:
        files = [Path(args.file).resolve()]
    if not files:
        logging.error("没有找到 Markdown 文件。扫描目录: %s", base_dir)
        runlog.finish(qwen)
        return 2

    dify: Optional[DifyClient] = None
    dify_fields: Optional[Dict[str, Dict[str, Any]]] = None
    state_path = base_dir / "output" / "dify_state.json"
    state = load_state(state_path)
    if args.upload_dify:
        dify = DifyClient()
        dify_fields = dify.ensure_metadata_fields()
        logging.info("Dify metadata 字段已检查/创建，共 %s 个", len(dify_fields))

    # true Batch File 模式先收集所有通过事实校验的活动，再一次提交异步 JSONL 批任务。
    batch_items: List[Tuple[str, EventFacts, ParsedTime]] = []

    for path in files:
        runlog.summary["files"] += 1
        source_identifier = source_ref(str(path))
        logging.info("处理源文件: %s", source_identifier)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            runlog.error(None, exc, "read_file", source_identifier)
            continue

        blocks = split_by_activity_name(text)
        if not blocks:
            try:
                logging.info("未发现标准‘活动名称：’边界，使用 Qwen 兜底切分: %s", source_identifier)
                blocks = qwen.split_events(text)
            except Exception as exc:
                runlog.error(None, exc, "split_events", source_identifier)
                continue

        logging.info("识别到 %s 个活动候选", len(blocks))
        for idx, block in enumerate(blocks, 1):
            runlog.summary["events_seen"] += 1
            facts = parse_facts(block, source_identifier, idx)
            if args.qwen_mode == "batch_file" and not args.mock_ai:
                missing, pt = validate_required(facts, args.timezone)
                if missing or pt is None:
                    runlog.skipped(facts, missing)
                    logging.warning("跳过活动 #%s %s: %s", idx, facts.name or "<无名称>", ", ".join(missing))
                    continue
                event_id = stable_event_id(facts.name, pt.start, facts.business_event_id)
                batch_items.append((event_id, facts, pt))
                continue

            try:
                process_one_event(
                    facts=facts,
                    qwen=qwen,
                    base_dir=base_dir,
                    timezone=args.timezone,
                    runlog=runlog,
                    dify=dify,
                    dify_fields=dify_fields,
                    state=state,
                    state_path=state_path,
                )
            except Exception as exc:
                logging.exception("活动处理失败但继续后续活动: %s", facts.name or f"#{idx}")
                runlog.error(facts, exc, "process_event")

    if args.qwen_mode == "batch_file" and not args.mock_ai and batch_items:
        # event_id 理论上稳定唯一；若老板重复提交同一实例，仅保留最后一份并警告。
        by_id: Dict[str, Tuple[EventFacts, ParsedTime]] = {}
        for event_id, facts, pt in batch_items:
            if event_id in by_id:
                logging.warning("发现重复 event_id=%s，Batch 中保留后出现的活动: %s", event_id, facts.name)
            by_id[event_id] = (facts, pt)
        batch_job_failed = False
        try:
            ai_map = qwen.enrich_many_batch_file(
                [(event_id, facts) for event_id, (facts, _pt) in by_id.items()], base_dir
            )
        except Exception as exc:
            # 整个 Batch 控制任务失败时，逐活动记错；不会伪装成“清洗成功”。
            logging.exception("Qwen Batch File 整体失败")
            for event_id, (facts, _pt) in by_id.items():
                runlog.error(facts, exc, "batch_file_job")
            ai_map = {}
            batch_job_failed = True

        for event_id, (facts, pt) in by_id.items():
            ai = ai_map.get(event_id)
            if ai is None:
                if not batch_job_failed:
                    # 子任务失败细节已写 batch_jobs/failures.json；这里为活动级日志留明确出口。
                    runlog.error(facts, RuntimeError("Batch File 未获得有效 AI 结果"), "batch_file_result")
                continue
            try:
                finalize_event(
                    facts=facts, ai=ai, pt=pt, event_id=event_id,
                    base_dir=base_dir, timezone=args.timezone, runlog=runlog,
                    dify=dify, dify_fields=dify_fields, state=state, state_path=state_path,
                    qwen_mode="batch_file",
                )
            except Exception as exc:
                logging.exception("Batch 活动最终处理失败但继续: %s", facts.name)
                runlog.error(facts, exc, "batch_finalize")

    runlog.finish(qwen)
    logging.info("完成: %s", json.dumps(runlog.summary, ensure_ascii=False))
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="老板活动 Markdown RAG 清洗器")
    p.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="活动根目录")
    p.add_argument("--file", help="只处理一个指定 Markdown 文件")
    p.add_argument("--timezone", default=DEFAULT_TZ, help="活动默认时区")
    p.add_argument(
        "--qwen-mode",
        choices=["realtime", "batch_chat", "batch_file"],
        default="realtime",
        help="realtime=实时API；batch_chat=单请求同步排队端点；batch_file=真正JSONL异步批处理",
    )
    p.add_argument("--upload-dify", action="store_true", help="清洗后创建/更新 Dify 文档并写入 Document Metadata")
    p.add_argument("--mock-ai", action="store_true", help="仅用于本地结构测试，不调用千问；生产不要使用")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
