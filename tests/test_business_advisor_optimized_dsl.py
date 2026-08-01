from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CURRENT_DSL = ROOT / "dify" / "商业顾问（当前使用版本）.yml"
OPTIMIZED_DSL = ROOT / "dify" / "商业顾问_并发准确率响应优化版_Dify-1.13.3.yml"
MODEL_NODE_TYPES = {"llm", "question-classifier", "parameter-extractor", "agent"}


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _nodes(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node["id"]): node["data"]
        for node in document["workflow"]["graph"]["nodes"]
    }


def _execute_code_node(node: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(node["code"], namespace)
    return namespace["main"](**kwargs)


def _terminal_paths(document: dict[str, Any]) -> list[list[str]]:
    graph = document["workflow"]["graph"]
    nodes = {str(node["id"]): node for node in graph["nodes"]}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        outgoing[str(edge["source"])].append(str(edge["target"]))
    starts = [
        node_id
        for node_id, node in nodes.items()
        if node["data"]["type"] == "start"
    ]
    paths: list[list[str]] = []

    def visit(node_id: str, path: list[str]) -> None:
        assert node_id not in path, f"DSL graph contains a cycle at {node_id}"
        next_path = [*path, node_id]
        if not outgoing[node_id]:
            paths.append(next_path)
            return
        for target in outgoing[node_id]:
            visit(target, next_path)

    for start in starts:
        visit(start, [])
    return paths


def test_optimized_dsl_exists_and_preserves_external_contract() -> None:
    current = _load(CURRENT_DSL)
    optimized = _load(OPTIMIZED_DSL)
    current_nodes = _nodes(current)
    optimized_nodes = _nodes(optimized)

    assert optimized["app"]["mode"] == current["app"]["mode"] == "advanced-chat"
    assert optimized["version"] == current["version"] == "0.6.0"
    assert optimized_nodes["start"]["variables"] == current_nodes["start"]["variables"]
    assert optimized["workflow"]["environment_variables"] == current["workflow"][
        "environment_variables"
    ]
    assert optimized["workflow"]["conversation_variables"] == []

    for node_id in (
        "general_knowledge",
        "course_knowledge",
        "general_knowledge_mixed",
        "course_knowledge_mixed",
    ):
        optimized_node = optimized_nodes[node_id]
        current_node = current_nodes[node_id]
        assert optimized_node["dataset_ids"] == current_node["dataset_ids"]
        assert optimized_node.get("metadata_filtering_conditions") == current_node.get(
            "metadata_filtering_conditions"
        )
        assert optimized_node["multiple_retrieval_config"]["reranking_model"] == (
            current_node["multiple_retrieval_config"]["reranking_model"]
        )


def test_graph_edges_reference_existing_nodes_and_model_paths_are_bounded() -> None:
    document = _load(OPTIMIZED_DSL)
    nodes = _nodes(document)
    for edge in document["workflow"]["graph"]["edges"]:
        assert str(edge["source"]) in nodes
        assert str(edge["target"]) in nodes

    paths = _terminal_paths(document)
    assert paths
    direct_paths = [path for path in paths if path[-1] == "quick_answer"]
    normal_paths = [path for path in paths if path not in direct_paths]
    assert direct_paths
    assert normal_paths

    for path in direct_paths:
        assert sum(nodes[node_id]["type"] in MODEL_NODE_TYPES for node_id in path) == 0
    for path in normal_paths:
        model_count = sum(
            nodes[node_id]["type"] in MODEL_NODE_TYPES for node_id in path
        )
        assert model_count <= 3
        if not any(node_id.endswith("_format_retry") for node_id in path):
            assert model_count <= 2


def test_llm_resource_limits_and_explicit_retry_policy() -> None:
    nodes = _nodes(_load(OPTIMIZED_DSL))
    for node_id, node in nodes.items():
        if node["type"] != "llm":
            continue
        retry = node.get("retry_config") or {}
        assert not retry.get("retry_enabled"), (
            f"{node_id} must not hide additional model requests behind node retries"
        )
        max_tokens = node["model"]["completion_params"].get("max_tokens")
        assert isinstance(max_tokens, int) and max_tokens > 0
        if node_id == "question_split":
            assert max_tokens <= 512
        else:
            assert max_tokens <= 2048


def test_quick_router_avoids_false_human_routes_and_skips_llm_for_service_work() -> None:
    quick_router = _nodes(_load(OPTIMIZED_DSL))["quick_intent"]

    direct = _execute_code_node(
        quick_router,
        query="我要申请退款",
        wechat_id="",
        wechat_display_name="",
        wechat_work_hours="",
        wechat_qr_url="",
    )
    assert direct["should_direct"] == "true"
    assert direct["direct_type"] == "service"
    assert "人工" in direct["direct_response"]

    human = _execute_code_node(
        quick_router,
        query="请帮我转人工客服",
        wechat_id="",
        wechat_display_name="",
        wechat_work_hours="",
        wechat_qr_url="",
    )
    assert human["should_direct"] == "true"
    assert human["direct_type"] == "human"

    for query in (
        "不需要人工，请分析我的商业模式",
        "微信营销怎么提高加微信率？",
        "企业微信运营应该关注哪些指标？",
    ):
        result = _execute_code_node(
            quick_router,
            query=query,
            wechat_id="",
            wechat_display_name="",
            wechat_work_hours="",
            wechat_qr_url="",
        )
        assert result["should_direct"] == "false", query


def test_evidence_compaction_bounds_context_and_keeps_identifiers() -> None:
    nodes = _nodes(_load(OPTIMIZED_DSL))
    large_results = [
        {
            "title": f"课程{i}",
            "content": "证据" * 5000,
            "score": 0.9,
            "metadata": {
                "course_id": f"course_{i}",
                "route_id": f"route_{i}",
                "status": "active",
                "unrelated": "噪声" * 5000,
            },
        }
        for i in range(8)
    ]

    for node_id in ("general_evidence_compact", "course_evidence_compact"):
        output = _execute_code_node(nodes[node_id], results=large_results)
        assert output["has_evidence"] == "true"
        assert len(output["evidence_text"]) <= 10_000
        assert "course_0" in output["evidence_text"]
        assert "route_0" in output["evidence_text"]
        assert "unrelated" not in output["evidence_text"]


def test_decision_parser_preserves_service_contract_and_rejects_empty_answer() -> None:
    parser = _nodes(_load(OPTIMIZED_DSL))["recommendation_decision_course"]
    structured_answer = (
        "【建议】\n"
        "先确认课程与当前目标是否匹配。\n\n"
        "【匹配依据】\n"
        "- 当前目标是快速掌握基础工具。\n"
        "- 课程证据覆盖入门场景。"
    )
    valid = _execute_code_node(
        parser,
        raw_text=json.dumps(
            {
                "intent": "direct_course",
                "candidate_course_ids": ["course_ai_001"],
                "candidate_route_id": "",
                "missing_information": [],
                "clarification_question": "",
                "answer_plan": "介绍课程",
                "expert_insight": "",
                "progress_action": {
                    "type": "none",
                    "route_id": "",
                    "stage_id": "",
                    "evidence_quote": "",
                },
                "final_answer": structured_answer,
                "handoff_required": False,
                "handoff_reason": "",
            },
            ensure_ascii=False,
        ),
        human_response="",
    )
    assert valid["valid"] == "true"
    assert valid["final_text"] == structured_answer
    assert "\n\n【匹配依据】\n-" in valid["final_text"]
    assert valid["candidate_course_ids_json"] == '["course_ai_001"]'
    assert valid["decision_json"]

    invalid = _execute_code_node(
        parser,
        raw_text='{"intent":"direct_course","final_answer":""}',
        human_response="",
    )
    assert invalid["valid"] == "false"
    assert invalid["decision_json"] == ""


def test_branch_prompts_only_reference_their_own_retrieval_outputs() -> None:
    nodes = _nodes(_load(OPTIMIZED_DSL))
    expected = {
        "combined_response_general": {"general_evidence_compact"},
        "combined_response_course": {"course_evidence_compact"},
        "combined_response_mixed": {"mixed_evidence_join"},
    }
    forbidden = {
        "general_knowledge",
        "course_knowledge",
        "general_knowledge_mixed",
        "course_knowledge_mixed",
        "general_evidence_compact",
        "course_evidence_compact",
        "mixed_evidence_join",
    }
    for node_id, allowed in expected.items():
        prompt = "\n".join(
            str(item.get("text") or "") for item in nodes[node_id]["prompt_template"]
        )
        referenced = set(re.findall(r"\{\{#([^.}]+)\.", prompt))
        assert referenced & forbidden == allowed


def test_answer_and_format_retry_prompts_share_adaptive_layout_contract() -> None:
    nodes = _nodes(_load(OPTIMIZED_DSL))
    branch_nodes = {
        "general": ("combined_response_general", "general_format_retry"),
        "course": ("combined_response_course", "course_format_retry"),
        "mixed": ("combined_response_mixed", "mixed_format_retry"),
    }
    shared_rules = (
        "回答排版契约（仅约束 final_answer）",
        "管理员配置的 business_output_style 继续控制语气",
        "简单答复、澄清问题和转人工说明",
        "复杂回答通常控制在 400～800 个中文字符",
        "不要使用 HTML、Markdown 表格、代码围栏、Markdown 标题（#）",
        "JSON 字符串中的换行必须写成转义序列 \\n",
    )
    branch_rules = {
        "general": ("【结论】", "【关键判断】", "【建议行动】"),
        "course": ("【建议】", "【匹配依据】", "【开始方式】"),
        "mixed": ("【结论】", "【业务建议】", "【学习建议】"),
    }

    for branch, node_ids in branch_nodes.items():
        for node_id in node_ids:
            system_prompt = "\n".join(
                str(item.get("text") or "")
                for item in nodes[node_id]["prompt_template"]
                if item.get("role") == "system"
            )
            for rule in shared_rules:
                assert rule in system_prompt, f"{node_id} missing shared rule: {rule}"
            for label in branch_rules[branch]:
                assert label in system_prompt, f"{node_id} missing label: {label}"


def test_layout_optimization_does_not_add_nodes_or_raise_answer_token_limits() -> None:
    current = _load(CURRENT_DSL)
    optimized = _load(OPTIMIZED_DSL)
    current_nodes = _nodes(current)
    optimized_nodes = _nodes(optimized)

    assert set(optimized_nodes) == set(current_nodes)
    for node_id in (
        "combined_response_general",
        "combined_response_course",
        "combined_response_mixed",
        "general_format_retry",
        "course_format_retry",
        "mixed_format_retry",
    ):
        assert optimized_nodes[node_id]["model"]["completion_params"]["max_tokens"] == 1800
