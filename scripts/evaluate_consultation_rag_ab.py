"""Run consultation RAG rerank/threshold A/B evaluation.

This runner is read-only: it calls Dify's retrieval endpoint and writes a
matrix report.  It never imports, enables, disables or deletes a document.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_cleaner.consultation_v2 import evaluate_rerank_threshold_matrix
from knowledge_cleaner.dify import DifyKnowledgeClient
from knowledge_cleaner.evaluation import load_evaluation_cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评估咨询案例 RAG rerank/threshold A/B")
    parser.add_argument("--cases", type=Path, required=True, help="JSONL黄金问题集")
    parser.add_argument("--base-url", required=True, help="Dify知识库API地址")
    parser.add_argument("--api-key", required=True, help="知识库API Key（仅命令行输入，不写报告）")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    client = DifyKnowledgeClient(args.base_url, args.api_key, args.dataset_id, timeout=60)
    try:
        result = evaluate_rerank_threshold_matrix(
            client,
            load_evaluation_cases(args.cases),
            k=args.top_k,
            thresholds=(0.0, 0.1, 0.2, 0.3),
            rerank_enabled=(False, True),
        )
    finally:
        client.close()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
