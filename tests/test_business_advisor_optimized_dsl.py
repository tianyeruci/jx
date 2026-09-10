from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CURRENT_DSL = ROOT / "dify" / "商业顾问（当前使用版本）.yml"


def _load() -> dict:
    return yaml.safe_load(CURRENT_DSL.read_text(encoding="utf-8"))


def _nodes(document: dict) -> dict[str, dict]:
    return {str(node["id"]): node["data"] for node in document["workflow"]["graph"]["nodes"]}


def test_current_dsl_edges_reference_existing_nodes() -> None:
    document = _load()
    nodes = _nodes(document)
    for edge in document["workflow"]["graph"]["edges"]:
        assert str(edge["source"]) in nodes
        assert str(edge["target"]) in nodes


def test_current_dsl_keeps_reranking_and_bounded_evidence() -> None:
    nodes = _nodes(_load())
    for node_id, operator in (("general_knowledge", "is not"), ("case_knowledge", "is")):
        retrieval = nodes[node_id]
        assert retrieval["multiple_retrieval_config"]["reranking_enable"] is True
        assert retrieval["multiple_retrieval_config"]["top_k"] == 5
        condition = retrieval["metadata_filtering_conditions"]["conditions"][0]
        assert condition == {
            "comparison_operator": operator,
            "name": "record_type",
            "value": "consulting_case",
        }
    assert nodes["advisor_retrieval_aggregate"]["variables"] == [
        ["general_knowledge", "result"],
        ["case_knowledge", "result"],
    ]
    assert nodes["general_evidence_compact"]["variables"] == [
        {"value_selector": ["advisor_retrieval_aggregate", "output"], "variable": "results"}
    ]
    namespace: dict[str, object] = {}
    exec(nodes["general_evidence_compact"]["code"], namespace)
    result = namespace["main"](
        [{"title": "资料", "content": "证据" * 20_000, "metadata": {"internal_id": "hidden"}}]
    )
    assert result["has_evidence"] == "true"
    assert len(result["evidence_text"]) <= 12_000
    assert "internal_id" not in result["evidence_text"]


def test_current_dsl_main_answer_has_no_hidden_model_retry() -> None:
    model = _nodes(_load())["combined_response_general"]
    assert model["model"]["completion_params"]["temperature"] == 0.2
    assert not (model.get("retry_config") or {}).get("retry_enabled")


def test_tool_canonical_dsls_enable_memory_window_six() -> None:
    for filename in ("moments_advisor.yml", "writing_advisor.yml"):
        document = yaml.safe_load((ROOT / "dify" / "dsl" / filename).read_text(encoding="utf-8"))
        nodes = _nodes(document)
        llm = next(data for data in nodes.values() if data.get("type") == "llm")
        assert llm["memory"]["window"] == {"enabled": True, "size": 6}


def test_business_main_model_memory_remains_disabled() -> None:
    for data in _nodes(_load()).values():
        if data.get("type") == "llm":
            assert not (data.get("memory") or {}).get("window", {}).get("enabled", False)
