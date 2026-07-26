from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from knowledge_cleaner.dify import DifyKnowledgeClient
from knowledge_cleaner.evaluation import EvaluationCase, evaluate_retrieval

from .config import get_settings


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是有效 JSON") from exc
        if not isinstance(value, dict) or not str(value.get("query") or "").strip():
            raise ValueError(f"第 {line_number} 行缺少 query")
        if value.get("dataset") not in {"general", "course"}:
            raise ValueError(f"第 {line_number} 行 dataset 必须是 general 或 course")
        if not isinstance(value.get("relevant_ids"), list):
            raise ValueError(f"第 {line_number} 行 relevant_ids 必须是数组")
        cases.append(value)
    if not cases:
        raise ValueError("评测集为空")
    return cases


def evaluate(path: Path, *, top_k: int = 5) -> dict[str, Any]:
    settings = get_settings()
    cases = load_cases(path)
    result: dict[str, Any] = {
        "top_k": top_k,
        "total_cases": len(cases),
        "retrieval_cases": 0,
        "behavior_cases": [],
        "datasets": {},
    }
    all_recall: list[float] = []
    all_hits: list[bool] = []
    critical_hits: list[bool] = []
    for dataset_code, dataset_id in {
        "general": settings.dify_general_dataset_id,
        "course": settings.dify_course_dataset_id,
    }.items():
        selected = [
            item
            for item in cases
            if item["dataset"] == dataset_code and item["relevant_ids"]
        ]
        result["retrieval_cases"] += len(selected)
        if not selected:
            continue
        if not settings.dify_knowledge_api_key or not dataset_id:
            raise ValueError(
                f"评测 {dataset_code} 前必须配置知识库 API Key 和对应 Dataset ID"
            )
        client = DifyKnowledgeClient(
            settings.knowledge_base_url,
            settings.dify_knowledge_api_key,
            dataset_id,
            timeout=60,
        )
        try:
            metrics = evaluate_retrieval(
                client,
                [
                    EvaluationCase(
                        query=str(item["query"]),
                        relevant_ids=tuple(str(value) for value in item["relevant_ids"]),
                    )
                    for item in selected
                ],
                k=top_k,
                search_method="semantic_search",
            )
            result["datasets"][dataset_code] = asdict(metrics)
            for source_case, case_result in zip(selected, metrics.cases, strict=True):
                all_recall.append(case_result.recall)
                hit = case_result.reciprocal_rank > 0
                all_hits.append(hit)
                if source_case.get("critical"):
                    critical_hits.append(hit)
        finally:
            client.close()
    result["behavior_cases"] = [
        {
            "id": item.get("id"),
            "query": item["query"],
            "checks": [
                name
                for name in (
                    "expect_no_answer",
                    "expect_filtered",
                    "expect_no_progress",
                    "expect_no_mutation",
                )
                if item.get(name)
            ],
        }
        for item in cases
        if any(
            item.get(name)
            for name in (
                "expect_no_answer",
                "expect_filtered",
                "expect_no_progress",
                "expect_no_mutation",
            )
        )
    ]
    aggregate_recall = sum(all_recall) / len(all_recall) if all_recall else 0.0
    aggregate_hit = sum(all_hits) / len(all_hits) if all_hits else 0.0
    critical_hit = (
        sum(critical_hits) / len(critical_hits) if critical_hits else 0.0
    )
    result["aggregate"] = {
        "k": top_k,
        "recall_at_k": aggregate_recall,
        "hit_at_k": aggregate_hit,
        "critical_hit_at_k": critical_hit,
        "retrieval_gate_passed": (
            aggregate_recall >= 0.80 and critical_hit >= 0.90
        ),
        "remaining_manual_gates": [
            "引用完整率",
            "无依据回答率",
            "错误课程ID",
            "错误正式链接",
            "虚构路线",
        ],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="执行轻量 Dify 知识库检索评测")
    parser.add_argument("file", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.file, top_k=args.top_k)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
