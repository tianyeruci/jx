from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.build_business_advisor_unified_dsl import build, validate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dify" / "商业顾问（当前使用版本）.yml"
OUTPUT = ROOT / "dify" / "商业顾问_统一修复版_Dify-1.13.3.yml"
GOLDEN_CASES = ROOT / "test_data" / "rag" / "business_dsl_optimization_cases.jsonl"

DECISION_PARSER_IDS = (
    "recommendation_decision_general",
    "recommendation_decision_course",
    "recommendation_decision_mixed",
    "general_retry_decision_parse",
    "course_retry_decision_parse",
    "mixed_retry_decision_parse",
)

FINAL_ANSWER_NODE_IDS = {
    "recommendation_decision_general": "final_answer_general",
    "recommendation_decision_course": "final_answer_course",
    "recommendation_decision_mixed": "final_answer_mixed",
    "general_retry_decision_parse": "general_retry_final_answer",
    "course_retry_decision_parse": "course_retry_final_answer",
    "mixed_retry_decision_parse": "mixed_retry_final_answer",
}

ROUTE_GATE_PROMPT_NODE_IDS = (
    "combined_response_course",
    "course_format_retry",
    "combined_response_mixed",
    "mixed_format_retry",
)

ROUTE_GATE_REQUIRED_CLAUSES = (
    "路线推荐状态机（强制门禁）",
    "direct_course 仅适用于用户明确查询有审核证据的具体课程或课程合集",
    "先展示该路线并等待用户确认",
    "只有用户当前问题明确确认所在阶段后，才允许推荐课程",
    "candidate_course_ids、expert_course_ids、expert_insight 必须为空",
    "不得列举、比较、暗示报名或推荐任何具体课程或课程合集",
    "多条路线均可能匹配且用户未在当前问题明确选定一条时",
    "若证据表明没有任何审核路线可匹配，可以明确说明未找到官方路线后转为独立课程路径",
    "当前用户本轮明确表达 > 最近未摘要历史中的明确确认 > 会话摘要中的已确认内容 > 路线进度 JSON > 用户画像 JSON",
    "权威路线状态上下文 JSON 与权威课程上下文 JSON",
    "candidate_course_ids 必须与其中已确认路线的已确认当前阶段全部可用课程 ID 完全一致并保持顺序",
    "不得限制为 5 门，不得包含下一阶段、其他阶段、其他路线或检索额外课程",
    "final_answer 只做中性状态或行动提示，不得说明推荐理由、匹配依据",
)


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


def test_current_source_exposes_authoritative_route_inputs_and_active_filters() -> None:
    nodes = _nodes(_load(SOURCE))
    variables = {item["variable"] for item in nodes["start"]["variables"]}
    assert {"route_state_context_json", "authoritative_course_context_json"} <= variables
    for node_id in ("course_knowledge", "course_knowledge_mixed"):
        assert nodes[node_id]["metadata_filtering_mode"] == "manual"
        assert nodes[node_id]["metadata_filtering_conditions"] == {
            "logical_operator": "and",
            "conditions": [
                {"name": "status", "comparison_operator": "is", "value": "active"}
            ],
        }

    split_prompt = "\n".join(
        str(template.get("text") or "") for template in nodes["question_split"]["prompt_template"]
    )
    assert "route_state_context_json" in split_prompt
    assert "短续问、阶段确认、进度更新或重新开始请求一律分类为 course" in split_prompt


def test_current_source_uses_next_step_contract_in_all_main_and_retry_paths() -> None:
    source = _load(SOURCE)
    nodes = _nodes(source)
    generation_prompts = [
        template["text"]
        for node in nodes.values()
        for template in node.get("prompt_template", [])
        if isinstance(template, dict)
        and isinstance(template.get("text"), str)
        and "必须输出以下结构" in template["text"]
    ]

    assert len(generation_prompts) == 6
    assert sum(node.get("type") == "llm" for node in nodes.values()) == 7
    for prompt in generation_prompts:
        assert "answer_plan" not in prompt
        assert '"expert_course_ids":[]' in prompt
        assert "expert_course_ids 必须与 candidate_course_ids 完全一致" in prompt
        assert "不得写入或重复 final_answer" in prompt
        assert "无课程候选、待路线或阶段确认、证据不足时两者必须为空" in prompt


def test_course_and_mixed_main_retry_prompts_keep_route_stage_course_gate() -> None:
    nodes = _nodes(_load(SOURCE))

    for node_id in ROUTE_GATE_PROMPT_NODE_IDS:
        system_prompts = [
            template["text"]
            for template in nodes[node_id]["prompt_template"]
            if template.get("role") == "system" and isinstance(template.get("text"), str)
        ]
        assert len(system_prompts) == 1
        prompt = system_prompts[0]
        for clause in ROUTE_GATE_REQUIRED_CLAUSES:
            assert clause in prompt, f"{node_id} 缺少路线推荐门禁：{clause}"
        user_prompt = "\n".join(
            str(template.get("text") or "")
            for template in nodes[node_id]["prompt_template"]
            if template.get("role") == "user"
        )
        assert "route_state_context_json" in user_prompt
        assert "authoritative_course_context_json" in user_prompt


def test_route_gate_golden_cases_cover_pending_and_direct_course_exception() -> None:
    cases = {
        case["id"]: case
        for case in (
            json.loads(line)
            for line in GOLDEN_CASES.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        )
    }

    assert cases["direct_course_without_route"]["expected_course_ids"] == ["course_ai_001"]
    assert cases["direct_course_without_route"]["expected_route_id"] is None
    assert cases["route_ai"]["expected_course_ids"] == []
    assert cases["route_ai"]["expected_route_id"] == "route_ai_basic"
    assert cases["route_confirmed_stage_pending"]["expected_course_ids"] == []
    assert cases["route_confirmed_stage_pending"]["expected_route_id"] == "route_ai_basic"
    assert cases["route_multiple_candidates"]["expected_course_ids"] == []
    assert cases["route_multiple_candidates"]["expected_route_id"] is None


def test_all_decision_parsers_keep_expert_ids_and_drop_answer_plan() -> None:
    nodes = _nodes(_load(SOURCE))
    raw_text = json.dumps(
        {
            "intent": "direct_course",
            "candidate_course_ids": ["course_a", "course_b"],
            "expert_course_ids": ["course_a", "course_b"],
            "candidate_route_id": "",
            "missing_information": [],
            "clarification_question": "",
            "expert_insight": "先学习基础内容，再完成两个课程的练习并复盘。",
            "progress_action": {"type": "none"},
            "final_answer": "建议从基础内容开始。",
            "handoff_required": False,
            "handoff_reason": "",
        },
        ensure_ascii=False,
    )

    for node_id in DECISION_PARSER_IDS:
        node = nodes[node_id]
        assert "answer_plan" not in node["code"]
        assert "expert_course_ids_json" in node["outputs"]
        value = _run(node, raw_text=raw_text, human_response="")
        assert value["valid"] == "true"
        assert json.loads(value["expert_course_ids_json"]) == ["course_a", "course_b"]
        decision = json.loads(value["decision_json"])
        assert decision["expert_course_ids"] == ["course_a", "course_b"]
        assert "answer_plan" not in decision

        final_node = nodes[FINAL_ANSWER_NODE_IDS[node_id]]
        assert "expert_course_ids_json" in final_node["outputs"]
        assert any(
            item.get("variable") == "expert_course_ids_json"
            for item in final_node["variables"]
        )
        forwarded = _run(
            final_node,
            final_text=value["final_text"],
            valid=value["valid"],
            decision_json=value["decision_json"],
            candidate_course_ids_json=value["candidate_course_ids_json"],
            expert_course_ids_json=value["expert_course_ids_json"],
            candidate_route_id=value["candidate_route_id"],
            progress_action_json=value["progress_action_json"],
            handoff_required=value["handoff_required"],
            parse_error=value["parse_error"],
        )
        assert forwarded["expert_course_ids_json"] == value["expert_course_ids_json"]
