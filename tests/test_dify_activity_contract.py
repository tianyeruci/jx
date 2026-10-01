from __future__ import annotations

import json
from pathlib import Path

import yaml

DSL = Path("dify/商业顾问（当前使用版本）.yml")


def _nodes() -> dict[str, dict]:
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    return {str(node["id"]): node["data"] for node in data["workflow"]["graph"]["nodes"]}


def _edges() -> set[tuple[str, str]]:
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    return {
        (str(edge["source"]), str(edge["target"])) for edge in data["workflow"]["graph"]["edges"]
    }


def _main(node_id: str):
    scope: dict[str, object] = {}
    exec(_nodes()[node_id]["code"], scope)
    return scope["main"]


def _activity_plan(**overrides: object) -> str:
    plan: dict[str, object] = {
        "mode": "recent_list",
        "signup_rule": "none",
        "name": "",
        "start_ts": 4076000000,
        "end_ts": 4077000000,
        "now_ts": 4076000000,
        "limit": 50,
        "city": "",
        "delivery_mode": "",
        "keywords": [],
        "need_links": False,
        "request_text": "最近有哪些活动？",
        "requested_all": False,
        "time_target": "event_start",
        "statuses": ["normal", "postponed"],
        "upper_exclusive": True,
        "retrieval_top_k": 50,
    }
    plan.update(overrides)
    return json.dumps(plan, ensure_ascii=False)


def _event(event_id: str, title: str, status: str, start: int, **metadata: object) -> dict:
    return {
        "title": title,
        "metadata": {
            "doc_metadata": {
                "record_type": "event",
                "event_id": event_id,
                "event_status": status,
                "event_start_ts": start,
                "event_end_ts": start + 3600,
                "source_title": title,
                **metadata,
            }
        },
    }


def test_chatflow_has_closed_activity_branch_and_aggregates_each_route() -> None:
    nodes = _nodes()
    edges = _edges()
    required = {
        "intent_understanding",
        "intent_parse",
        "activity_route",
        "activity_filter_route",
        "activity_knowledge",
        "activity_knowledge_time",
        "activity_knowledge_unbounded",
        "activity_retrieval_aggregate",
        "activity_retry_gate",
        "activity_retry_route",
        "activity_knowledge_retry",
        "activity_supplement_aggregate",
        "activity_results",
        "activity_answer",
    }
    assert required <= set(nodes)
    assert {
        ("activity_route", "activity_filter_route"),
        ("activity_route", "activity_retry_gate"),
        ("activity_knowledge", "activity_retrieval_aggregate"),
        ("activity_knowledge_time", "activity_retrieval_aggregate"),
        ("activity_knowledge_unbounded", "activity_retrieval_aggregate"),
        ("activity_knowledge_retry", "activity_supplement_aggregate"),
        ("activity_retrieval_aggregate", "activity_results"),
        ("activity_supplement_aggregate", "activity_results"),
        ("activity_results", "activity_answer"),
    } <= edges
    assert nodes["activity_retrieval_aggregate"]["variables"] == [
        ["activity_knowledge", "result"],
        ["activity_knowledge_time", "result"],
        ["activity_knowledge_unbounded", "result"],
    ]
    assert nodes["activity_supplement_aggregate"]["variables"] == [
        ["activity_knowledge_retry", "result"],
        ["activity_supplement_skip", "result"],
    ]


def test_activity_list_topology_has_a_bounded_two_hundred_raw_candidate_budget() -> None:
    """The 200-result contract needs at least 200 raw list candidates before dedupe."""

    nodes = _nodes()
    edges = _edges()
    primary_routes = {
        "activity_knowledge",
        "activity_knowledge_time",
        "activity_knowledge_unbounded",
    }
    assert all(("activity_filter_route", route) in edges for route in primary_routes)
    assert ("activity_retry_route", "activity_knowledge_retry") in edges
    # Filter routes are mutually exclusive, so only one primary query can run
    # for a list request.  The one allowed bounded supplement must therefore
    # make the raw candidate budget 100 + 100, not the former 50 + 50 = 100.
    primary_limit = {nodes[route]["multiple_retrieval_config"]["top_k"] for route in primary_routes}
    supplement_limit = nodes["activity_knowledge_retry"]["multiple_retrieval_config"]["top_k"]
    assert primary_limit == {100}
    assert supplement_limit == 100
    assert next(iter(primary_limit)) + supplement_limit >= 200


def test_chatflow_compiles_activity_filters_from_structured_plan() -> None:
    nodes = _nodes()
    text = DSL.read_text(encoding="utf-8")
    for marker in ("event", "event_status", "event_start_ts", "doc_metadata", "registration_url"):
        assert marker in text
    assert [
        condition["name"]
        for condition in nodes["activity_knowledge"]["metadata_filtering_conditions"]["conditions"]
    ] == [
        "record_type",
        "event_start_ts",
        "event_start_ts",
        "event_status",
    ]
    assert [
        condition["name"]
        for condition in nodes["activity_knowledge_time"]["metadata_filtering_conditions"][
            "conditions"
        ]
    ] == [
        "record_type",
        "event_start_ts",
        "event_start_ts",
    ]
    assert nodes["activity_knowledge_unbounded"]["metadata_filtering_conditions"]["conditions"] == [
        {"comparison_operator": "is", "name": "record_type", "value": "event"}
    ]
    assert nodes["activity_knowledge"]["query_variable_selector"] == [
        "intent_parse",
        "activity_retrieval_query",
    ]
    assert "ACTIVITY_MAX_RESULTS" in text
    assert "BAILIAN_API_KEY" not in text
    assert "DASHSCOPE_API_KEY" not in text


def test_activity_plan_is_parsed_after_intent_understanding_without_quick_hijack() -> None:
    quick_intent = _main("quick_intent")
    assert quick_intent("你好")["should_direct"] == "true"
    assert quick_intent("如何提高活动报名转化率？")["should_direct"] == "false"

    intent_parse = _main("intent_parse")
    model_contract = {
        "primary_intent": "activity",
        "interaction": "generate",
        "task_relation": "none",
        "task_kind": "factual",
        "task_brief": "列出近期活动",
        "retrieval_query": "近期活动",
        "needs": [],
        "activity": {
            "mode": "recent_list",
            "signup_rule": "none",
            "time": {},
            "time_target": "event_start",
            "name": "",
            "city": "",
            "delivery_mode": "",
            "keywords": [],
        },
    }
    result = intent_parse(
        json.dumps(model_contract, ensure_ascii=False),
        "最近有哪些活动？",
        4076000000,
    )
    plan = json.loads(result["activity_plan"])
    assert result["parse_status"] == "ok"
    assert result["primary_intent"] == "activity"
    assert result["activity_filter_kind"] == "active"
    assert plan["statuses"] == ["normal", "postponed"]
    assert plan["time_target"] == "event_start"
    assert result["activity_retrieval_query"]


def test_activity_intent_parse_recovers_from_invalid_task_state_and_clamps_configured_limit() -> (
    None
):
    intent_parse = _main("intent_parse")
    model_contract = {
        "primary_intent": "activity",
        "interaction": "generate",
        "task_relation": "none",
        "task_kind": "factual",
        "task_brief": "列出近期活动",
        "retrieval_query": "近期活动",
        "needs": [],
        "activity": {"mode": "recent_list", "signup_rule": "none", "time": {}},
    }

    result = intent_parse(
        json.dumps(model_contract, ensure_ascii=False),
        "最近有哪些活动？",
        4076000000,
        configured_activity_max_results="999",
        state_json="{not-json",
    )
    plan = json.loads(result["activity_plan"])
    assert result["parse_status"] == "ok"
    assert plan["limit"] == 200

    fallback = intent_parse(
        json.dumps(model_contract, ensure_ascii=False),
        "最近有哪些活动？",
        4076000000,
        configured_activity_max_results="not-an-integer",
        state_json="[]",
    )
    assert json.loads(fallback["activity_plan"])["limit"] == 50


def test_activity_code_path_keeps_case_and_analysis_requests_out_of_activity_retrieval() -> None:
    """A supplied LLM contract is routed conservatively; it is not LLM proof."""

    quick_intent = _main("quick_intent")
    assert quick_intent("如何提高活动报名转化率？")["should_direct"] == "false"
    assert quick_intent("案例中的活动安排是否有风险？")["should_direct"] == "false"

    intent_parse = _main("intent_parse")
    for primary_intent, question in (
        ("general", "如何提高活动报名转化率？"),
        ("case", "案例中的活动安排是否有风险？"),
    ):
        model_contract = {
            "primary_intent": primary_intent,
            "interaction": "generate",
            "task_relation": "none",
            "task_kind": "factual",
            "task_brief": question,
            "retrieval_query": question,
            "needs": [],
            "activity": {"mode": "recent_list", "signup_rule": "none", "time": {}},
        }
        result = intent_parse(json.dumps(model_contract, ensure_ascii=False), question, 4076000000)
        assert result["primary_intent"] == primary_intent
        assert result["activity_retrieval_query"] == ""


def test_activity_supplement_is_limited_to_one_enabled_list_query() -> None:
    retry_gate = _main("activity_retry_gate")

    listed = retry_gate(_activity_plan(mode="recent_list"), "近期活动", "true")
    listed_diagnostics = json.loads(listed["diagnostics"])
    assert listed["should_retry"] == "true"
    assert listed["query"]
    assert listed_diagnostics["retrieval_calls"] == 2
    assert listed_diagnostics["raw_candidate_capacity"] == 200
    assert listed_diagnostics["policy"].endswith("original_conditions_postvalidated")

    for plan, enabled, reason in (
        (_activity_plan(mode="detail"), "true", "detail_single_query"),
        (_activity_plan(mode="recent_list"), "false", "disabled"),
    ):
        result = retry_gate(plan, "近期活动", enabled)
        assert result["should_retry"] == "false"
        assert result["query"] == ""
        assert json.loads(result["diagnostics"])["reason"] == reason


def test_activity_results_keep_independent_events_and_bounded_diagnostics() -> None:
    result = _main("activity_results")(
        [
            _event("a", "活动一", "normal", 4076150400),
            _event("b", "活动二", "normal", 4076323200),
        ],
        _activity_plan(),
    )
    assert "活动一" in result["activity_text"]
    assert "活动二" in result["activity_text"]
    diagnostics = json.loads(result["diagnostics"])
    assert diagnostics["displayed"] == 2
    assert diagnostics["coverage_status"] == "bounded_retrieval_not_full_enumeration"
    assert diagnostics["primary_retrieved"] == 2
    assert diagnostics["supplement_retrieved"] == 0


def test_activity_results_default_to_fifty_and_clamp_requested_limit_to_two_hundred() -> None:
    activity_results = _main("activity_results")
    events = [
        _event(f"event-{index}", f"活动{index}", "normal", 4076150400 + index)
        for index in range(205)
    ]

    default = json.loads(activity_results(events, _activity_plan())["diagnostics"])
    assert default["displayed"] == 50

    clamped = json.loads(activity_results(events, _activity_plan(limit=999))["diagnostics"])
    assert clamped["displayed"] == 200

    malformed = json.loads(
        activity_results(events, _activity_plan(limit="not-an-integer"))["diagnostics"]
    )
    assert malformed["displayed"] == 50


def test_activity_results_can_postvalidate_more_than_two_hundred_raw_candidates() -> None:
    """Only the display is capped; all bounded primary/supplement candidates are checked."""

    activity_results = _main("activity_results")
    primary = [
        _event(f"primary-{index}", f"主活动{index}", "normal", 4076150400 + index)
        for index in range(100)
    ]
    supplement = [
        _event(f"supplement-{index}", f"补充活动{index}", "normal", 4076250400 + index)
        for index in range(105)
    ]
    result = activity_results(primary, _activity_plan(limit=999), supplement)
    diagnostics = json.loads(result["diagnostics"])
    assert diagnostics["retrieved"] == 205
    assert diagnostics["primary_retrieved"] == 100
    assert diagnostics["supplement_retrieved"] == 105
    assert diagnostics["displayed"] == 200


def test_activity_detail_and_status_filter_keep_the_requested_scope() -> None:
    detail = _main("activity_results")(
        [
            _event(
                "a",
                "AI增长工作坊",
                "normal",
                4076150400,
                activity_summary="活动详情摘要",
                delivery_mode="online",
                city="上海",
                registration_url="https://example.com/register",
            ),
            _event("b", "商业增长峰会", "normal", 4076323200, activity_summary="另一场活动详情"),
        ],
        _activity_plan(
            mode="detail",
            name="AI增长工作坊",
            start_ts=None,
            end_ts=None,
            need_links=True,
            request_text="AI增长工作坊详情",
            statuses=[],
        ),
    )
    assert "AI增长工作坊" in detail["activity_text"]
    assert "活动详情摘要" in detail["activity_text"]
    assert "商业增长峰会" not in detail["activity_text"]
    assert "https://example.com/register" in detail["activity_text"]

    postponed = _main("activity_results")(
        [
            _event("a", "取消活动", "cancelled", 4076150400),
            _event("b", "延期活动", "postponed", 4076323200),
        ],
        _activity_plan(mode="postponed", statuses=["postponed"], request_text="延期活动"),
    )
    assert "延期活动" in postponed["activity_text"]
    assert "取消活动" not in postponed["activity_text"]
