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


def test_current_source_preserves_supported_start_inputs_and_task_state() -> None:
    variables = {item["variable"] for item in _nodes(_load(SOURCE))["start"]["variables"]}
    assert variables == {
        "conversation_summary",
        "recent_history_text",
        "profile_json",
        "business_output_style",
        "task_state_json",
    }


def test_current_source_has_explicit_intent_offer_and_answer_roles() -> None:
    nodes = _nodes(_load(SOURCE))
    assert {node_id for node_id, node in nodes.items() if node["type"] == "llm"} == {
        "intent_understanding",
        "offer_response",
        "combined_response_general",
    }
    assert all(
        not (nodes[node_id].get("retry_config") or {}).get("retry_enabled")
        for node_id in ("intent_understanding", "offer_response", "combined_response_general")
    )
    forbidden = ("course", "recommendation", "question_split", "format_retry")
    assert not [node_id for node_id in nodes if any(term in node_id for term in forbidden)]
    assert {
        node_id for node_id, node in nodes.items() if node["type"] == "knowledge-retrieval"
    } == {
        "activity_knowledge",
        "activity_knowledge_time",
        "activity_knowledge_unbounded",
        "activity_knowledge_retry",
        "general_knowledge",
        "case_knowledge",
        "offer_case_knowledge",
        "offer_methodology_knowledge",
    }
    assert {"task_context", "intent_parse", "save_task_state", "offer_route"} <= set(nodes)


def test_all_non_direct_paths_preserve_the_current_route_guards() -> None:
    document = _load(SOURCE)
    nodes = _nodes(document)
    paths = _paths(document)
    assert any(path[-1] == "quick_answer" for path in paths)
    normal_paths = [path for path in paths if path[-1] == "answer_general"]
    assert len(normal_paths) == 2
    for path in normal_paths:
        assert path[:8] == [
            "start",
            "quick_intent",
            "quick_route",
            "task_context",
            "intent_understanding",
            "intent_parse",
            "save_task_state",
            "offer_route",
        ]
        assert {
            "activity_route",
            "case_route",
            "advisor_retrieval_aggregate",
            "general_evidence_compact",
            "combined_response_general",
            "general_answer_guard",
        } <= set(path)
        assert {"general_search", "case_search"} & set(path)
        assert [node_id for node_id in path if nodes[node_id]["type"] == "llm"] == [
            "intent_understanding",
            "combined_response_general",
        ]

    offer_paths = [path for path in paths if path[-1] == "offer_answer"]
    assert len(offer_paths) == 2
    for path in offer_paths:
        assert {
            "intent_parse",
            "save_task_state",
            "offer_route",
            "offer_evidence_compact",
            "offer_response",
            "offer_answer_guard",
        } <= set(path)
        assert {"offer_case_knowledge", "offer_method_search"} & set(path)
        assert [node_id for node_id in path if nodes[node_id]["type"] == "llm"] == [
            "intent_understanding",
            "offer_response",
        ]

    activity_paths = [path for path in paths if path[-1] == "activity_answer"]
    assert len(activity_paths) == 5
    for path in activity_paths:
        assert {
            "intent_parse",
            "save_task_state",
            "offer_route",
            "activity_route",
            "activity_results",
        } <= set(path)
        assert [node_id for node_id in path if nodes[node_id]["type"] == "llm"] == [
            "intent_understanding",
        ]


def test_activity_results_reads_only_aggregated_retrieval_output() -> None:
    nodes = _nodes(_load(SOURCE))
    result_node = nodes["activity_results"]
    selectors = [item["value_selector"] for item in result_node["variables"]]
    assert ["activity_retrieval_aggregate", "output"] in selectors
    assert ["activity_supplement_aggregate", "output"] in selectors
    assert ["activity_knowledge", "result"] not in selectors
    assert ["activity_knowledge_retry", "result"] not in selectors


def test_every_dsl_edge_declares_actual_source_and_target_types() -> None:
    document = _load(SOURCE)
    nodes = _nodes(document)
    for edge in document["workflow"]["graph"]["edges"]:
        assert edge["data"]["sourceType"] == nodes[edge["source"]]["type"]
        assert edge["data"]["targetType"] == nodes[edge["target"]]["type"]
