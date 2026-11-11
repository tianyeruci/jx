from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .dify import DifyKnowledgeClient


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    query: str
    relevant_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        query = self.query.strip()
        relevant = tuple(dict.fromkeys(str(item).strip() for item in self.relevant_ids))
        if not query:
            raise ValueError("评测问题不能为空")
        if not relevant or any(not item for item in relevant):
            raise ValueError("每条评测问题至少需要一个relevant_id")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "relevant_ids", relevant)


@dataclass(frozen=True, slots=True)
class CaseResult:
    query: str
    relevant_ids: tuple[str, ...]
    retrieved_ids: tuple[tuple[str, ...], ...]
    recall: float
    reciprocal_rank: float
    error: str = ""


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    k: int
    total: int
    failed: int
    recall_at_k: float
    mrr: float
    hit_rate_at_k: float
    cases: tuple[CaseResult, ...]


def _metadata_identifiers(raw: Any) -> set[str]:
    identifiers: set[str] = set()
    supported_names = {
        "doc_id",
        "course_id",
        "route_id",
        "source_file_id",
    }
    if isinstance(raw, dict):
        for name in supported_names:
            value = raw.get(name)
            if value:
                identifiers.add(str(value))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name") in supported_names and item.get("value"):
                identifiers.add(str(item["value"]))
    return identifiers


def record_identifiers(record: dict[str, Any]) -> set[str]:
    """Extract both Dify physical IDs and stable ``doc_id`` metadata."""
    identifiers: set[str] = set()
    for node in (record, record.get("segment"), record.get("document")):
        if not isinstance(node, dict):
            continue
        for key in ("id", "document_id", "name"):
            if node.get(key):
                identifiers.add(str(node[key]))
        identifiers.update(_metadata_identifiers(node.get("doc_metadata")))
        identifiers.update(_metadata_identifiers(node.get("metadata")))
        nested_document = node.get("document")
        if isinstance(nested_document, dict):
            for key in ("id", "document_id", "name"):
                if nested_document.get(key):
                    identifiers.add(str(nested_document[key]))
            identifiers.update(_metadata_identifiers(nested_document.get("doc_metadata")))
    return identifiers


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    """Load strict JSONL golden questions.

    Accepted relevant-id field names are ``relevant_ids`` and the more verbose
    ``relevant_document_ids``.  This keeps hand-authored and exported sets easy
    to use without making malformed lines disappear silently.
    """
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"评测集第{line_number}行不是有效JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"评测集第{line_number}行必须是JSON对象")
        relevant = item.get("relevant_ids") or item.get("relevant_document_ids")
        if not isinstance(relevant, list):
            raise ValueError(f"评测集第{line_number}行缺少relevant_ids数组")
        cases.append(
            EvaluationCase(
                query=str(item.get("query") or ""),
                relevant_ids=tuple(dict.fromkeys(str(value) for value in relevant)),
            )
        )
    if not cases:
        raise ValueError("评测集为空")
    return cases


def evaluate_retrieval(
    client: DifyKnowledgeClient,
    cases: Iterable[EvaluationCase],
    *,
    k: int = 5,
    search_method: str = "hybrid_search",
    score_threshold: float | None = None,
    reranking: dict[str, Any] | None = None,
) -> RetrievalMetrics:
    """Compute macro Recall@K, MRR and HitRate@K over a golden set.

    API failures are counted as zero-score cases and exposed in ``failed`` and
    each case's ``error``.  This conservative behavior prevents outages from
    making a partial run look better than it was.
    """
    if k < 1:
        raise ValueError("k必须大于0")
    case_list = list(cases)
    if not case_list:
        raise ValueError("评测集为空")
    results: list[CaseResult] = []
    for case in case_list:
        relevant = set(case.relevant_ids)
        try:
            records = client.retrieve(
                case.query,
                top_k=k,
                search_method=search_method,
                score_threshold=score_threshold,
                reranking=reranking,
            )
            ranked = tuple(tuple(sorted(record_identifiers(record))) for record in records[:k])
            found: set[str] = set()
            first_rank = 0
            for rank, identifiers in enumerate(ranked, start=1):
                matches = relevant.intersection(identifiers)
                if matches and not first_rank:
                    first_rank = rank
                found.update(matches)
            recall = len(found) / len(relevant)
            reciprocal_rank = 1 / first_rank if first_rank else 0.0
            results.append(
                CaseResult(
                    query=case.query,
                    relevant_ids=case.relevant_ids,
                    retrieved_ids=ranked,
                    recall=recall,
                    reciprocal_rank=reciprocal_rank,
                )
            )
        except Exception as exc:
            results.append(
                CaseResult(
                    query=case.query,
                    relevant_ids=case.relevant_ids,
                    retrieved_ids=(),
                    recall=0.0,
                    reciprocal_rank=0.0,
                    error=str(exc),
                )
            )
    total = len(results)
    return RetrievalMetrics(
        k=k,
        total=total,
        failed=sum(bool(item.error) for item in results),
        recall_at_k=sum(item.recall for item in results) / total,
        mrr=sum(item.reciprocal_rank for item in results) / total,
        hit_rate_at_k=sum(item.reciprocal_rank > 0 for item in results) / total,
        cases=tuple(results),
    )
