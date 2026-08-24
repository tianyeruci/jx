from pathlib import Path

import yaml

DSL = Path("dify/商业顾问（当前使用版本）.yml")


def test_chatflow_has_activity_branch_and_closed_edges():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    graph = data["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = graph["edges"]
    edge_pairs = {(edge["source"], edge["target"]) for edge in edges}

    assert "activity_intent" in nodes
    assert "activity_window" in nodes
    assert "activity_status_route" in nodes
    assert "activity_knowledge" in nodes
    assert "activity_status_knowledge" in nodes
    assert "activity_results" in nodes
    assert "activity_answer" in nodes
    assert ("activity_intent", "activity_route") in edge_pairs
    assert ("activity_route", "activity_window") in edge_pairs
    assert ("activity_window", "activity_status_route") in edge_pairs
    assert ("activity_status_route", "activity_knowledge") in edge_pairs
    assert ("activity_status_route", "activity_status_knowledge") in edge_pairs
    assert ("activity_route", "case_route") in edge_pairs
    assert ("activity_knowledge", "activity_retrieval_aggregate") in edge_pairs
    assert ("activity_status_knowledge", "activity_retrieval_aggregate") in edge_pairs
    assert ("activity_retrieval_aggregate", "activity_results") in edge_pairs
    assert ("activity_results", "activity_answer") in edge_pairs
    aggregate = nodes["activity_retrieval_aggregate"]["data"]
    assert aggregate["variables"] == [
        ["activity_knowledge", "result"],
        ["activity_status_knowledge", "result"],
    ]


def test_chatflow_activity_contract_contains_dynamic_window_and_safe_sorting():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    nodes = {node["id"]: node["data"] for node in data["workflow"]["graph"]["nodes"]}
    text = DSL.read_text(encoding="utf-8")
    for marker in ("event", "event_status", "event_start_ts", "doc_metadata", "registration_url"):
        assert marker in text
    conditions = nodes["activity_knowledge"]["metadata_filtering_conditions"]["conditions"]
    assert {item["name"] for item in conditions} == {
        "record_type",
        "event_status",
        "event_start_ts",
        "event_end_ts",
    }
    assert "{{#activity_window.start_ts#}}" in text
    assert "{{#activity_window.end_ts#}}" in text
    assert (
        "event_status"
        in nodes["activity_status_knowledge"]["metadata_filtering_conditions"]["conditions"][1][
            "name"
        ]
    )
    assert "BAILIAN_API_KEY" not in text
    assert "sort" in text
    assert "activity_max_results" in text
    assert "[:limit]" in text
    assert "ACTIVITY_MAX_RESULTS" in text
    assert "- env" in text
    assert "DASHSCOPE_API_KEY" not in text


def test_activity_intent_does_not_hijack_business_questions():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "quick_intent"
    )
    scope = {}
    exec(code, scope)
    for query in (
        "最近有哪些活动？",
        "最新活动有哪些？",
        "最近活动有哪些？",
        "活动有哪些？",
        "活动",
        "所有活动",
        "最新所有活动",
        "这个活动的详情",
        "这个活动怎么报名",
        "第一个的详情",
        "Workbuddy无痛日更写作实战营详情",
        "近期有什么线下沙龙",
        "近期有哪些聚会",
        "最近有什么日程",
        "近期有什么安排",
        "本月排期",
        "近期有哪些联谊",
        "活动安排",
        "活动日程",
        "活动排期",
    ):
        assert scope["main"](query)["is_activity_request"] == "true"
    assert scope["main"]("最近有哪些面向客户的活动？")["is_activity_request"] == "true"
    assert scope["main"]("有哪些延期活动？")["is_activity_request"] == "true"
    assert scope["main"]("如何提高活动报名转化率？")["is_activity_request"] == "false"
    assert scope["main"]("活动取消后如何挽回客户？")["is_activity_request"] == "false"
    assert scope["main"]("会议延期后的沟通策略")["is_activity_request"] == "false"
    assert scope["main"]("如何取消订单？")["is_activity_request"] == "false"
    assert scope["main"]("AI增长营详情")["is_activity_request"] == "true"
    assert scope["main"]("觉醒大会详情")["is_activity_request"] == "true"
    for query in ("最近有哪些会议真实案例？", "近期活动案例参考"):
        result = scope["main"](query)
        assert result["is_activity_request"] == "false"
        assert result["is_case_request"] == "true"


def test_activity_window_defaults_to_ten_and_supports_all_status_language():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "activity_window"
    )
    scope = {}
    exec(code, scope)
    result = scope["main"]("最新所有活动有哪些？")
    assert result["activity_max_results"] == 10
    assert result["requested_status"] == "all"
    assert scope["main"]("最近活动", "3")["activity_max_results"] == 3
    assert scope["main"]("最近活动", "99")["activity_max_results"] == 20
    assert scope["main"]("最近活动", "not-a-number")["activity_max_results"] == 10
    assert scope["main"]("最近活动")["requested_status"] == "normal"
    assert scope["main"]("旧活动详情")["requested_status"] == "detail"
    assert scope["main"]("最新活动详情")["requested_status"] == "normal"
    assert scope["main"]("最近的AI增长工作坊详情")["requested_status"] == "detail"
    assert scope["main"]("第六个活动详情")["requested_status"] == "detail"
    for query in ("活动报名详情", "最近活动报名详情", "有哪些活动报名详情"):
        assert scope["main"](query)["requested_status"] == "normal"
    for query, expected in (
        ("延期活动详情", "postponed"),
        ("取消活动详情", "cancelled"),
        ("已结束活动详情", "completed"),
        ("所有延期活动详情", "postponed"),
        ("最近延期活动详情", "postponed"),
        ("最新活动具体有哪些", "normal"),
        ("最新训练活动详情", "normal"),
    ):
        assert scope["main"](query)["requested_status"] == expected
    assert scope["main"]("已结束活动")["requested_status"] == "completed"


def test_activity_results_formats_multiple_activities_on_separate_lines():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "activity_results"
    )
    scope = {}
    exec(code, scope)
    results = [
        {
            "title": "活动一",
            "metadata": {
                "doc_metadata": {
                    "record_type": "event",
                    "event_id": "a",
                    "event_status": "normal",
                    "event_start_ts": 4076150400,
                    "event_end_ts": 4076236799,
                    "source_title": "活动一",
                }
            },
        },
        {
            "title": "活动二",
            "metadata": {
                "doc_metadata": {
                    "record_type": "event",
                    "event_id": "b",
                    "event_status": "normal",
                    "event_start_ts": 4076323200,
                    "event_end_ts": 4076409599,
                    "source_title": "活动二",
                }
            },
        },
    ]

    output = scope["main"](results, "最近有哪些活动？", 4076000000, 4077000000, "normal")

    assert output["activity_text"].count("\n") == 1
    assert "活动一" in output["activity_text"]
    assert "活动二" in output["activity_text"]


def test_activity_list_is_compact_but_named_activity_returns_detail():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "activity_results"
    )
    scope = {}
    exec(code, scope)
    results = [
        {
            "title": "AI增长工作坊",
            "metadata": {
                "doc_metadata": {
                    "record_type": "event",
                    "event_id": "a",
                    "event_status": "normal",
                    "event_start_ts": 4076150400,
                    "event_end_ts": 4076236799,
                    "source_title": "AI增长工作坊",
                    "activity_summary": "活动详情摘要",
                    "delivery_mode": "线上",
                    "city": "上海",
                    "registration_url": "https://example.com/register",
                }
            },
        }
    ]
    listing = scope["main"](results, "最近活动有哪些？", 4076000000, 4077000000, "recent")
    assert listing["activity_text"] == "AI增长工作坊｜2099年3月3日｜正常"
    assert "活动详情摘要" not in listing["activity_text"]
    detail = scope["main"](results, "AI增长工作坊详情", 4076000000, 4077000000, "recent")
    assert "活动详情摘要" in detail["activity_text"]
    assert "报名链接：https://example.com/register" in detail["activity_text"]


def test_named_activity_detail_does_not_dump_other_candidates():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "activity_results"
    )
    scope = {}
    exec(code, scope)
    results = []
    for event_id, title in (("a", "AI增长工作坊"), ("b", "商业增长峰会")):
        results.append(
            {
                "title": title,
                "metadata": {
                    "doc_metadata": {
                        "record_type": "event",
                        "event_id": event_id,
                        "event_status": "normal",
                        "event_start_ts": 4076150400,
                        "event_end_ts": 4076236799,
                        "source_title": title,
                        "activity_summary": f"{title}详情",
                    }
                },
            }
        )
    detail = scope["main"](results, "AI增长工作坊详情", 4076000000, 4077000000, "normal")
    assert "AI增长工作坊详情" in detail["activity_text"]
    assert "商业增长峰会详情" not in detail["activity_text"]


def test_detail_query_bypasses_recent_window_and_supports_second_ordinal():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "activity_results"
    )
    scope = {}
    exec(code, scope)
    results = []
    for event_id, title, start in (
        ("old", "旧活动", 1000),
        ("new", "新活动", 4076150400),
    ):
        results.append(
            {
                "title": title,
                "metadata": {
                    "doc_metadata": {
                        "record_type": "event",
                        "event_id": event_id,
                        "event_status": "normal",
                        "event_start_ts": start,
                        "event_end_ts": start + 3600,
                        "source_title": title,
                        "activity_summary": f"{title}详情",
                    }
                },
            }
        )
    detail = scope["main"](
        results,
        "旧活动详情",
        4076000000,
        4077000000,
        "detail",
    )
    assert "旧活动详情" in detail["activity_text"]
    assert "新活动详情" not in detail["activity_text"]

    ordinal = scope["main"](
        results,
        "第二个活动详情",
        0,
        4077000000,
        "detail",
    )
    assert "新活动详情" in ordinal["activity_text"]
    assert "旧活动详情" not in ordinal["activity_text"]

    out_of_range = scope["main"](
        results,
        "第六个活动详情",
        0,
        4077000000,
        "detail",
    )
    assert out_of_range["activity_text"] == "当前没有符合条件的活动。"

    empty = scope["main"]([], "旧活动详情", 4076000000, 4077000000, "detail")
    assert empty["activity_text"] == "当前没有符合条件的活动。"


def test_recent_specific_activity_detail_does_not_fall_back_to_listing():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "activity_results"
    )
    scope = {}
    exec(code, scope)
    results = []
    for event_id, title in (("a", "AI增长工作坊"), ("b", "商业增长峰会")):
        results.append(
            {
                "title": title,
                "metadata": {
                    "doc_metadata": {
                        "record_type": "event",
                        "event_id": event_id,
                        "event_status": "normal",
                        "event_start_ts": 4076150400,
                        "event_end_ts": 4076236799,
                        "source_title": title,
                        "activity_summary": f"{title}详情",
                    }
                },
            }
        )
    detail = scope["main"](
        results,
        "最近的AI增长工作坊详情",
        4076000000,
        4077000000,
        "detail",
    )
    assert "AI增长工作坊详情" in detail["activity_text"]
    assert "商业增长峰会详情" not in detail["activity_text"]


def test_all_status_activity_listing_keeps_requested_status_filter():
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    code = next(
        node["data"]["code"]
        for node in data["workflow"]["graph"]["nodes"]
        if node["id"] == "activity_results"
    )
    scope = {}
    exec(code, scope)
    results = []
    for event_id, title, status in (
        ("cancelled", "取消活动", "cancelled"),
        ("postponed", "延期活动", "postponed"),
        ("normal", "正常活动", "normal"),
    ):
        results.append(
            {
                "title": title,
                "metadata": {
                    "doc_metadata": {
                        "record_type": "event",
                        "event_id": event_id,
                        "event_status": status,
                        "event_start_ts": 4076150400,
                        "event_end_ts": 4076236799,
                        "source_title": title,
                    }
                },
            }
        )
    output = scope["main"](
        results,
        "所有延期活动详情",
        4076000000,
        4077000000,
        "postponed",
    )
    assert "延期活动" in output["activity_text"]
    assert "取消活动" not in output["activity_text"]
    assert "正常活动" not in output["activity_text"]
    empty = scope["main"](
        [],
        "所有延期活动详情",
        4076000000,
        4077000000,
        "postponed",
    )
    assert empty["activity_text"] == "当前没有符合条件的延期活动。"
