from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.build_business_advisor_unified_dsl import build, validate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dify" / "商业顾问（当前使用版本）.yml"
OUTPUT = ROOT / "dify" / "商业顾问_统一修复版_Dify-1.13.3.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _nodes(document: dict) -> dict[str, dict]:
    return {str(node["id"]): node["data"] for node in document["workflow"]["graph"]["nodes"]}


def _run(node: dict, **kwargs: object) -> dict:
    namespace: dict[str, object] = {}
    exec(node["code"], namespace)
    return namespace["main"](**kwargs)


def test_unified_dsl_is_generated_from_current_source_and_preserves_bindings() -> None:
    source = _load(SOURCE)
    generated = _load(OUTPUT)
    expected = build(source)
    validate(source, generated)
    assert generated == expected
    assert generated["app"]["mode"] == source["app"]["mode"]
    source_nodes = _nodes(source)
    generated_nodes = _nodes(generated)
    for node_id in ("general_knowledge", "course_knowledge", "general_knowledge_mixed", "course_knowledge_mixed"):
        assert generated_nodes[node_id]["dataset_ids"] == source_nodes[node_id]["dataset_ids"]


def test_current_source_exposes_three_ai_course_suggested_questions() -> None:
    source = _load(SOURCE)
    questions = source["workflow"]["features"]["suggested_questions"]
    assert questions == [
        "我想系统学习AI工具，应该从哪里开始？",
        "有没有适合公众号运营入门的课程？",
        "我已经有一些基础，如何选择AI写作进阶课程？",
    ]


def test_course_decision_parser_preserves_expert_course_ids_as_internal_json() -> None:
    nodes = _nodes(_load(OUTPUT))
    value = _run(
        nodes["recommendation_decision_course"],
        raw_text=json.dumps(
            {
                "intent": "direct_course",
                "candidate_course_ids": ["course_a", "course_b"],
                "expert_course_ids": ["course_b"],
                "candidate_route_id": "",
                "missing_information": [],
                "clarification_question": "",
                "answer_plan": "介绍课程",
                "expert_insight": "先学习基础课程。",
                "progress_action": {"type": "none"},
                "final_answer": "建议先从基础课程开始。",
                "handoff_required": False,
                "handoff_reason": "",
            },
            ensure_ascii=False,
        ),
        human_response="",
    )
    assert value["valid"] == "true"
    assert json.loads(value["expert_course_ids_json"]) == ["course_b"]
    decision = json.loads(value["decision_json"])
    assert decision["expert_course_ids"] == ["course_b"]
