from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from .batch_pipeline import BatchCleaningPipeline, BatchOptions
from .classifier import DifyWorkflowClassifier
from .dify import DifyKnowledgeClient, publish_clean_output
from .evaluation import evaluate_retrieval, load_evaluation_cases
from .pipeline import CleaningPipeline, PipelineOptions

PILOT_PATTERNS = (
    "阿猫投资学_20260101-20260531",
    "高效读书课",
    "南歌",
)


def _add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, default=Path("文件清洗样本"))
    parser.add_argument("--output", type=Path, default=Path("清洗文档/批量清洗输出"))
    parser.add_argument("--pilot", action="store_true", help="仅处理三类代表性样本")
    parser.add_argument("--include", action="append", default=[], help="仅处理文件名包含该文字的文件，可重复")
    parser.add_argument("--review-overrides", type=Path, help="人工复核CSV；按doc_id覆盖keep/no_index/review")
    parser.add_argument("--no-ocr", action="store_true", help="扫描PDF不尝试本地PaddleOCR，直接进入复核")
    parser.add_argument("--no-classifier", action="store_true", help="模糊项不调用Dify分类Workflow")
    parser.add_argument("--legacy-pdf", action="store_true", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="商业顾问RAG源文件批量清洗工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    clean = subparsers.add_parser("clean", help="按来源策略清洗TXT、Markdown和PDF并生成完整快照")
    _add_batch_arguments(clean)

    review_export = subparsers.add_parser("review-export", help="导出当前快照的人工复核CSV")
    review_export.add_argument("--output", type=Path, default=Path("清洗文档/批量清洗输出"))
    review_export.add_argument("--target", type=Path, required=True)

    review_import = subparsers.add_parser("review-import", help="应用人工复核CSV并重新生成快照")
    _add_batch_arguments(review_import)
    review_import.add_argument("--decisions", type=Path, required=True)

    publish = subparsers.add_parser("publish", help="将当前快照中审核保留的文档发布到Dify")
    publish.add_argument("--output", type=Path, default=Path("清洗文档/批量清洗输出"))
    publish.add_argument("--base-url", default=os.getenv("DIFY_KNOWLEDGE_BASE_URL", ""))
    publish.add_argument("--api-key", default=os.getenv("DIFY_KNOWLEDGE_API_KEY", ""))
    publish.add_argument("--dataset-id", default=os.getenv("DIFY_DATASET_ID", ""))
    publish.add_argument("--publish", action="store_true", help="必须显式指定；否则不会发起网络请求")
    publish.add_argument("--indexing-timeout", type=int, default=900)

    evaluate = subparsers.add_parser("evaluate", help="使用黄金问题集测试Dify Recall@K和MRR")
    evaluate.add_argument("--cases", type=Path, required=True)
    evaluate.add_argument("--base-url", default=os.getenv("DIFY_KNOWLEDGE_BASE_URL", ""))
    evaluate.add_argument("--api-key", default=os.getenv("DIFY_KNOWLEDGE_API_KEY", ""))
    evaluate.add_argument("--dataset-id", default=os.getenv("DIFY_DATASET_ID", ""))
    evaluate.add_argument("--top-k", type=int, default=5)
    evaluate.add_argument("--report", type=Path)
    return parser


def _classifier_from_env(disabled: bool) -> DifyWorkflowClassifier | None:
    if disabled:
        return None
    base_url = os.getenv("DIFY_CLEANER_BASE_URL", "").strip()
    api_key = os.getenv("DIFY_CLEANER_API_KEY", "").strip()
    if not base_url or not api_key:
        return None
    return DifyWorkflowClassifier(
        base_url,
        api_key,
        user=os.getenv("DIFY_CLEANER_USER", "knowledge-cleaner").strip() or "knowledge-cleaner",
    )


def _run_batch(args: argparse.Namespace, *, decisions: Path | None = None) -> int:
    patterns = tuple(args.include) or (PILOT_PATTERNS if args.pilot else ())
    if args.legacy_pdf:
        result = CleaningPipeline(
            PipelineOptions(
                input_path=args.input.resolve(),
                output_path=args.output.resolve(),
                include_patterns=patterns,
            )
        ).run()
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    classifier = _classifier_from_env(args.no_classifier)
    try:
        result = BatchCleaningPipeline(
            BatchOptions(
                input_path=args.input.resolve(),
                output_path=args.output.resolve(),
                classifier=classifier,
                review_overrides=(decisions or args.review_overrides).resolve() if (decisions or args.review_overrides) else None,
                include_patterns=patterns,
                enable_ocr=not args.no_ocr,
            )
        ).run()
    finally:
        if classifier is not None:
            classifier.close()
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.parse_errors == 0 else 1


def _require_connection(args: argparse.Namespace) -> None:
    missing = [
        name
        for name, value in (("base-url", args.base_url), ("api-key", args.api_key), ("dataset-id", args.dataset_id))
        if not value
    ]
    if missing:
        raise SystemExit(f"缺少Dify配置：{', '.join(missing)}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "clean":
        return _run_batch(args)
    if args.command == "review-import":
        if not args.decisions.is_file():
            raise SystemExit(f"复核CSV不存在：{args.decisions}")
        return _run_batch(args, decisions=args.decisions)
    if args.command == "review-export":
        source = args.output.resolve() / "review.csv"
        if not source.is_file():
            raise SystemExit(f"找不到复核清单：{source}")
        target = args.target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        print(str(target))
        return 0
    if args.command == "publish":
        if not args.publish:
            print("安全停止：未指定 --publish，没有连接Dify，也没有上传任何文档。")
            return 2
        _require_connection(args)
        client = DifyKnowledgeClient(args.base_url, args.api_key, args.dataset_id)
        try:
            result = publish_clean_output(args.output.resolve(), client, indexing_timeout=args.indexing_timeout)
        finally:
            client.close()
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0 if result.failed == 0 else 1
    _require_connection(args)
    client = DifyKnowledgeClient(args.base_url, args.api_key, args.dataset_id)
    try:
        metrics = evaluate_retrieval(client, load_evaluation_cases(args.cases.resolve()), k=args.top_k)
    finally:
        client.close()
    payload = json.dumps(asdict(metrics), ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.report.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if metrics.failed == 0 else 1
