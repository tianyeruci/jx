from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "test_data" / "rag" / "business_dsl_optimization_cases.jsonl"
SCRIPT = ROOT / "scripts" / "evaluate_dify_chatflow_ab.py"
SPEC = importlib.util.spec_from_file_location("evaluate_dify_chatflow_ab", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
evaluate_checks = MODULE.evaluate_checks
inspect_execution = MODULE.inspect_execution
parse_sse_lines = MODULE.parse_sse_lines
read_cases = MODULE.read_cases


def test_optimization_cases_cover_critical_branches_and_limits() -> None:
    cases = read_cases(CASES)
    branches = {case["expected_branch"] for case in cases}
    categories = {case["category"] for case in cases}
    layout_profiles = {
        case["layout_profile"]
        for case in cases
        if case.get("layout_profile")
    }

    assert len(cases) >= 28
    assert branches == {"direct", "general", "course", "mixed"}
    assert {
        "fixed",
        "routing",
        "general",
        "course",
        "mixed",
        "safety",
        "layout",
    } <= categories
    assert layout_profiles == {
        "complex_general",
        "complex_course",
        "complex_mixed",
        "compact_clarification",
    }
    assert all(case["max_llm_calls"] == 0 for case in cases[:6])
    assert all(case["max_llm_calls"] <= 2 for case in cases)


def test_parse_sse_and_inspect_execution_collects_trace_contract() -> None:
    lines = [
        'data: {"event":"node_finished","conversation_id":"conv-1","data":{"node_id":"quick_intent","node_type":"code","outputs":{"direct_type":"business"}}}',
        "",
        'data: {"event":"node_finished","conversation_id":"conv-1","data":{"node_id":"question_split","node_type":"llm","outputs":{}}}',
        "",
        'data: {"event":"node_finished","conversation_id":"conv-1","data":{"node_id":"combined_response_course","node_type":"llm","outputs":{}}}',
        "",
        'data: {"event":"node_finished","conversation_id":"conv-1","data":{"node_id":"recommendation_decision_course","node_type":"code","outputs":{"decision_json":"{\\"candidate_course_ids\\":[\\"course_ai_001\\"],\\"candidate_route_id\\":\\"route_ai_basic\\"}"}}}',
        "",
        'data: {"event":"message","conversation_id":"conv-1","answer":"正在检索。"}',
        "",
        'data: {"event":"message","conversation_id":"conv-1","answer":"AI工具入门"}',
        "",
    ]

    execution = inspect_execution(
        {
            "events": parse_sse_lines(lines),
            "first_token_ms": 120.0,
            "total_ms": 800.0,
        }
    )

    assert execution["answer"] == "正在检索。AI工具入门"
    assert execution["conversation_id"] == "conv-1"
    assert execution["observed_branch"] == "course"
    assert execution["llm_calls"] == 2
    assert execution["candidate_course_ids"] == ["course_ai_001"]
    assert execution["candidate_route_id"] == "route_ai_basic"


def test_accuracy_checks_enforce_branch_contract_and_hallucination_guard() -> None:
    case = {
        "expected_branch": "course",
        "expected_direct_type": None,
        "max_llm_calls": 2,
        "expected_course_ids": [],
        "expected_route_id": None,
        "must_contain_all": [],
        "must_contain_any": ["无法", "未找到"],
        "must_not_contain": ["https://"],
    }
    execution = {
        "answer": "未找到可核验的官方课程，因此无法提供链接。",
        "error": "",
        "observed_branch": "course",
        "direct_type": "business",
        "llm_calls": 2,
        "candidate_course_ids": [],
        "candidate_route_id": None,
    }

    checks = evaluate_checks(case, execution)

    assert all(checks.values())


def test_accuracy_checks_measure_layout_without_changing_business_contract() -> None:
    case = {
        "expected_branch": "general",
        "expected_direct_type": None,
        "max_llm_calls": 2,
        "expected_course_ids": [],
        "expected_route_id": None,
        "must_contain_all": [],
        "must_contain_any": ["增长"],
        "must_not_contain": ["https://"],
        "required_labels_all": ["【结论】", "【关键判断】", "【建议行动】"],
        "min_blank_lines": 2,
        "max_answer_chars": 800,
    }
    execution = {
        "answer": (
            "【结论】\n增长放缓首先要拆分流量与转化。\n\n"
            "【关键判断】\n- 流量是否下降。\n- 转化是否下降。\n\n"
            "【建议行动】\n1. 核对渠道数据。\n2. 复盘转化路径。\n3. 做小规模验证。"
        ),
        "error": "",
        "observed_branch": "general",
        "direct_type": "business",
        "llm_calls": 2,
        "candidate_course_ids": [],
        "candidate_route_id": None,
    }

    checks = evaluate_checks(case, execution)

    assert all(checks.values())
    assert {"layout_labels", "layout_blank_lines", "answer_length"} <= checks.keys()

    execution["answer"] = "增长放缓首先要拆分流量与转化，然后逐项排查。"
    failed = evaluate_checks(case, execution)
    assert not failed["layout_labels"]
    assert not failed["layout_blank_lines"]
