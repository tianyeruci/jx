from __future__ import annotations

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


def _paths(document: dict) -> list[list[str]]:
    graph = document["workflow"]["graph"]
    outgoing: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        outgoing.setdefault(str(edge["source"]), []).append(str(edge["target"]))

    def visit(node_id: str, path: list[str]) -> list[list[str]]:
        assert node_id not in path, f"DSL graph contains a cycle at {node_id}"
        next_path = [*path, node_id]
        if not outgoing.get(node_id):
            return [next_path]
        return [result for child in outgoing[node_id] for result in visit(child, next_path)]

    return visit("start", [])


def test_unified_dsl_is_generated_from_current_source_and_preserves_bindings() -> None:
    source = _load(SOURCE)
    generated = _load(OUTPUT)
    expected = build(source)
    validate(source, generated)
    assert generated == expected
    assert generated["app"]["mode"] == source["app"]["mode"] == "advanced-chat"
    assert (
        _nodes(generated)["general_knowledge"]["dataset_ids"]
        == _nodes(source)["general_knowledge"]["dataset_ids"]
    )


def test_current_source_has_only_the_four_supported_start_inputs() -> None:
    variables = {item["variable"] for item in _nodes(_load(SOURCE))["start"]["variables"]}
    assert variables == {
        "conversation_summary",
        "recent_history_text",
        "profile_json",
        "business_output_style",
    }


def test_current_source_has_one_main_llm_and_no_course_or_recommendation_nodes() -> None:
    nodes = _nodes(_load(SOURCE))
    assert sum(node["type"] == "llm" for node in nodes.values()) == 1
    forbidden = ("course", "recommendation", "question_split", "format_retry")
    assert not [node_id for node_id in nodes if any(term in node_id for term in forbidden)]
    assert {
        node_id for node_id, node in nodes.items() if node["type"] == "knowledge-retrieval"
    } == {
        "activity_knowledge",
        "activity_status_knowledge",
        "general_knowledge",
        "case_knowledge",
    }
    prompt = "\n".join(
        str(item.get("text") or "")
        for item in nodes["combined_response_general"]["prompt_template"]
    )
    assert "不再生成课程、路线或卡片决策 JSON" in nodes["combined_response_general"]["desc"]
    assert "不要推荐、比较、介绍或展示课程" in prompt


def test_all_normal_paths_use_one_llm_and_emit_the_retained_progress_answer() -> None:
    document = _load(SOURCE)
    nodes = _nodes(document)
    paths = _paths(document)
    assert any(path[-1] == "quick_answer" for path in paths)
    normal_paths = [path for path in paths if path[-1] == "answer_general"]
    assert normal_paths == [
        [
            "start",
            "quick_intent",
            "quick_route",
            "progress_answer",
            "activity_intent",
            "activity_route",
            "case_route",
            "general_knowledge",
            "advisor_retrieval_aggregate",
            "general_evidence_compact",
            "combined_response_general",
            "answer_general",
        ],
        [
            "start",
            "quick_intent",
            "quick_route",
            "progress_answer",
            "activity_intent",
            "activity_route",
            "case_route",
            "case_knowledge",
            "advisor_retrieval_aggregate",
            "general_evidence_compact",
            "combined_response_general",
            "answer_general",
        ],
    ]
    assert [path for path in paths if path[-1] == "activity_answer"] == [
        [
            "start",
            "quick_intent",
            "quick_route",
            "progress_answer",
            "activity_intent",
            "activity_route",
            "activity_window",
            "activity_status_route",
            "activity_knowledge",
            "activity_retrieval_aggregate",
            "activity_results",
            "activity_answer",
        ],
        [
            "start",
            "quick_intent",
            "quick_route",
            "progress_answer",
            "activity_intent",
            "activity_route",
            "activity_window",
            "activity_status_route",
            "activity_status_knowledge",
            "activity_retrieval_aggregate",
            "activity_results",
            "activity_answer",
        ],
    ]
    for path in normal_paths:
        assert "progress_answer" in path
        assert sum(nodes[node_id]["type"] == "llm" for node_id in path) == 1


def test_activity_results_reads_only_aggregated_retrieval_output() -> None:
    nodes = _nodes(_load(SOURCE))
    result_node = nodes["activity_results"]
    selectors = [item["value_selector"] for item in result_node["variables"]]
    assert ["activity_retrieval_aggregate", "output"] in selectors
    assert ["activity_knowledge", "result"] not in selectors
    assert ["activity_status_knowledge", "result"] not in selectors


def test_every_dsl_edge_declares_actual_source_and_target_types() -> None:
    document = _load(SOURCE)
    nodes = _nodes(document)
    for edge in document["workflow"]["graph"]["edges"]:
        assert edge["data"]["sourceType"] == nodes[edge["source"]]["type"]
        assert edge["data"]["targetType"] == nodes[edge["target"]]["type"]
