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
        ["general_search", "output"],
        ["case_search", "output"],
    ]
    selectors = {
        item["variable"]: item["value_selector"]
        for item in nodes["general_evidence_compact"]["variables"]
    }
    assert selectors == {
        "results": ["advisor_retrieval_aggregate", "output"],
        "primary_intent": ["intent_parse", "primary_intent"],
        "expected_tasks": ["intent_parse", "retrieval_tasks"],
    }
    namespace: dict[str, object] = {}
    exec(nodes["general_evidence_compact"]["code"], namespace)
    result = namespace["main"](
        [{"title": "资料", "content": "证据" * 20_000, "metadata": {"internal_id": "hidden"}}],
        "general",
        [],
    )
    assert result["has_evidence"] == "true"
    assert len(result["evidence_text"]) <= 12_000
    assert "internal_id" not in result["evidence_text"]


def test_current_dsl_main_answer_has_no_hidden_model_retry() -> None:
    model = _nodes(_load())["combined_response_general"]
    assert model["model"]["completion_params"]["temperature"] == 0.1
    assert not (model.get("retry_config") or {}).get("retry_enabled")
