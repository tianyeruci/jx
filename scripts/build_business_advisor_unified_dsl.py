from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "dify" / "商业顾问（当前使用版本）.yml"
DEFAULT_OUTPUT = ROOT / "dify" / "商业顾问_统一修复版_Dify-1.13.3.yml"


def _load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"DSL root must be a mapping: {path}")
    return value


def _nodes(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node["id"]): node["data"]
        for node in document["workflow"]["graph"]["nodes"]
    }


def build(source: dict[str, Any]) -> dict[str, Any]:
    generated = copy.deepcopy(source)
    generated["app"]["name"] = "商业顾问-统一修复候选"
    generated["app"]["description"] = (
        "以当前使用版 DSL 为唯一行为基线，合入课程合集候选校验、下一步建议内部依据字段、"
        "OCR/咨询案例联调所需的稳定输出契约；不改变 HTTP/SSE 外部接口。"
    )
    return generated


def validate(source: dict[str, Any], generated: dict[str, Any]) -> None:
    if generated["app"]["mode"] != source["app"]["mode"]:
        raise ValueError("app mode changed")
    if generated["version"] != source["version"]:
        raise ValueError("DSL schema version changed")
    source_start = _nodes(source)["start"]
    generated_start = _nodes(generated)["start"]
    if generated_start["variables"] != source_start["variables"]:
        raise ValueError("Start input contract changed")
    if generated["workflow"]["environment_variables"] != source["workflow"][
        "environment_variables"
    ]:
        raise ValueError("environment variable contract changed")
    if generated["workflow"]["conversation_variables"] != source["workflow"][
        "conversation_variables"
    ]:
        raise ValueError("conversation variable contract changed")
    source_nodes = _nodes(source)
    generated_nodes = _nodes(generated)
    for node_id in (
        "general_knowledge",
        "course_knowledge",
        "general_knowledge_mixed",
        "course_knowledge_mixed",
    ):
        if generated_nodes[node_id].get("dataset_ids") != source_nodes[node_id].get(
            "dataset_ids"
        ):
            raise ValueError(f"knowledge base binding changed: {node_id}")
        if generated_nodes[node_id].get("multiple_retrieval_config", {}).get(
            "reranking_model"
        ) != source_nodes[node_id].get("multiple_retrieval_config", {}).get(
            "reranking_model"
        ):
            raise ValueError(f"reranking model changed: {node_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = _load(args.source)
    expected = build(source)
    validate(source, expected)
    if args.check:
        if not args.output.exists():
            raise SystemExit(f"missing generated DSL: {args.output}")
        actual = _load(args.output)
        validate(source, actual)
        if actual != expected:
            raise SystemExit("generated DSL is stale; rerun without --check")
        print(f"ok: {args.output}")
        return 0
    args.output.write_text(
        yaml.safe_dump(expected, allow_unicode=True, sort_keys=False, width=1_000_000),
        encoding="utf-8",
    )
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
