from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx


DOMAIN_CLASSES = frozenset(
    {"business_core", "business_support", "off_scope", "promotion_or_catalog", "uncertain"}
)
QUALITY_CLASSES = frozenset({"useful", "low_info", "uncertain"})
CLASSIFICATION_KEYS = frozenset(
    {
        "domain_class",
        "content_type",
        "knowledge_nature",
        "topics",
        "time_sensitive",
        "quality_class",
        "evidence_spans",
        "confidence",
    }
)

Decision = Literal["keep", "no_index", "review"]


class ClassificationValidationError(ValueError):
    """The workflow completed, but did not return the agreed strict schema."""


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    domain_class: str
    content_type: str
    knowledge_nature: str
    topics: tuple[str, ...]
    time_sensitive: bool
    quality_class: str
    evidence_spans: tuple[str, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class ClassificationOutcome:
    decision: Decision
    result: ClassificationResult | None = None
    error: str = ""

    @property
    def requires_review(self) -> bool:
        return self.decision == "review"


def _nonempty_string(value: Any, field: str, *, maximum: int = 100) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClassificationValidationError(f"{field}必须是非空字符串")
    result = value.strip()
    if len(result) > maximum:
        raise ClassificationValidationError(f"{field}超过{maximum}字符")
    return result


def _string_array(value: Any, field: str, *, maximum_items: int, maximum_length: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ClassificationValidationError(f"{field}必须是字符串数组")
    if len(value) > maximum_items:
        raise ClassificationValidationError(f"{field}最多允许{maximum_items}项")
    result: list[str] = []
    for item in value:
        text = _nonempty_string(item, field, maximum=maximum_length)
        if text not in result:
            result.append(text)
    return tuple(result)


def parse_classification(payload: str | dict[str, Any], source_text: str) -> ClassificationResult:
    """Parse the classifier's exact JSON contract and verify cited evidence.

    Markdown code fences, missing fields and extra fields are rejected.  Keeping
    this parser strict prevents a successful HTTP response from becoming an
    unsafe automatic exclusion.
    """
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ClassificationValidationError("分类器输出不是严格JSON") from exc
    elif isinstance(payload, dict):
        parsed = payload
    else:
        raise ClassificationValidationError("分类器输出必须是JSON对象")
    if not isinstance(parsed, dict):
        raise ClassificationValidationError("分类器输出顶层必须是JSON对象")
    keys = frozenset(parsed)
    if keys != CLASSIFICATION_KEYS:
        missing = sorted(CLASSIFICATION_KEYS - keys)
        extra = sorted(keys - CLASSIFICATION_KEYS)
        raise ClassificationValidationError(f"分类器字段不匹配，缺少={missing}，多余={extra}")

    domain = _nonempty_string(parsed["domain_class"], "domain_class")
    if domain not in DOMAIN_CLASSES:
        raise ClassificationValidationError(f"未知domain_class：{domain}")
    quality = _nonempty_string(parsed["quality_class"], "quality_class")
    if quality not in QUALITY_CLASSES:
        raise ClassificationValidationError(f"未知quality_class：{quality}")
    content_type = _nonempty_string(parsed["content_type"], "content_type")
    nature = _nonempty_string(parsed["knowledge_nature"], "knowledge_nature")
    topics = _string_array(parsed["topics"], "topics", maximum_items=12, maximum_length=50)
    evidence = _string_array(
        parsed["evidence_spans"], "evidence_spans", maximum_items=8, maximum_length=300
    )
    missing_evidence = [span for span in evidence if span not in source_text]
    if missing_evidence:
        raise ClassificationValidationError("evidence_spans包含原文中不存在的内容")
    time_sensitive = parsed["time_sensitive"]
    if not isinstance(time_sensitive, bool):
        raise ClassificationValidationError("time_sensitive必须是布尔值")
    confidence = parsed["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ClassificationValidationError("confidence必须是0到1之间的数值")
    confidence = float(confidence)
    if not 0 <= confidence <= 1:
        raise ClassificationValidationError("confidence必须是0到1之间的数值")
    return ClassificationResult(
        domain_class=domain,
        content_type=content_type,
        knowledge_nature=nature,
        topics=topics,
        time_sensitive=time_sensitive,
        quality_class=quality,
        evidence_spans=evidence,
        confidence=confidence,
    )


def classification_decision(result: ClassificationResult, *, body_length: int) -> Decision:
    has_evidence = bool(result.evidence_spans)
    if (
        result.domain_class in {"business_core", "business_support"}
        and result.quality_class == "useful"
        and result.confidence >= 0.85
        and has_evidence
    ):
        return "keep"
    excluded = result.domain_class in {"off_scope", "promotion_or_catalog"} or result.quality_class == "low_info"
    if excluded and result.confidence >= 0.92 and has_evidence:
        # Short, self-contained observations are easy for a model to under-rate.
        return "review" if body_length < 150 else "no_index"
    return "review"


class DifyWorkflowClassifier:
    """Optional Dify Workflow classifier with fail-closed-to-review semantics."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        user: str = "knowledge-cleaner",
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.user = user
        self.client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> DifyWorkflowClassifier:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _extract_output(response: dict[str, Any]) -> str | dict[str, Any]:
        data = response.get("data") if isinstance(response.get("data"), dict) else response
        status = str(data.get("status") or "")
        if status and status != "succeeded":
            raise RuntimeError(str(data.get("error") or f"Dify Workflow状态：{status}"))
        outputs = data.get("outputs")
        if not isinstance(outputs, dict):
            raise ClassificationValidationError("Dify Workflow响应缺少outputs对象")
        if frozenset(outputs) == CLASSIFICATION_KEYS:
            return outputs
        candidates = [outputs[key] for key in ("result", "classification", "output") if key in outputs]
        if len(candidates) != 1:
            raise ClassificationValidationError("outputs必须直接符合分类schema或只包含一个约定结果字段")
        return candidates[0]

    def classify(self, title: str, body: str, source_type: str) -> ClassificationOutcome:
        source_text = f"{title}\n{body}"
        try:
            response = self.client.post(
                "workflows/run",
                json={
                    "inputs": {"title": title, "body": body, "source_type": source_type},
                    "response_mode": "blocking",
                    "user": self.user,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ClassificationValidationError("Dify Workflow响应顶层必须是对象")
            result = parse_classification(self._extract_output(payload), source_text)
            return ClassificationOutcome(
                decision=classification_decision(result, body_length=len(body.strip())),
                result=result,
            )
        except Exception as exc:
            # Classification is advisory.  A timeout, unpublished workflow or
            # malformed model response must never auto-delete source material.
            return ClassificationOutcome(decision="review", error=str(exc))
