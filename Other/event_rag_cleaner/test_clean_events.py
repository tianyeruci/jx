import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clean_events import (
    DIFY_METADATA_TYPES,
    AIEnrichment,
    DifyClient,
    EventFacts,
    QwenCallError,
    QwenClient,
    RunLogger,
    apply_source_overrides,
    build_parser,
    finalize_event,
    infer_delivery_mode,
    load_state,
    metadata_for_event,
    normalize_source_status,
    parse_event_time,
    parse_facts,
    parse_optional_deadline,
    render_markdown,
    run,
    split_by_activity_name,
    stable_event_id,
    validate_ai_business,
    validate_required,
)


def test_sample_split_and_required_fields():
    sample = """活动名称：菜籽油·7天私域IP训练营
主要内容：帮助个人IP做私域。
适合人群：个人IP。
时间：2026年8月17日—8月23日。
地点：线上训练营。
状态：正常报名。

活动名称：第二个活动
主要内容：AI写作。
时间：2026年8月10日—8月15日。
地点：线上。
状态：已完成。
"""
    blocks = split_by_activity_name(sample)
    assert len(blocks) == 2
    facts = parse_facts(blocks[0], "sample.md", 1)
    assert facts.delivery_mode == "online"
    missing, pt = validate_required(facts, "Asia/Shanghai")
    assert missing == []
    assert pt is not None
    assert pt.start.strftime("%Y-%m-%d") == "2026-08-17"
    assert pt.end.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-23 23:59:59"


def test_missing_required_skips():
    block = """活动名称：缺时间活动
主要内容：测试。
地点：线上。
状态：正常报名。
"""
    facts = parse_facts(block, "x.md", 1)
    missing, pt = validate_required(facts, "Asia/Shanghai")
    assert "活动时间" in missing
    assert pt is None


def test_mode_from_location_reconciles_template_example():
    assert infer_delivery_mode("", "线上训练营") == "online"
    assert infer_delivery_mode("", "杭州") == "offline"
    assert infer_delivery_mode("线上+线下", "杭州") == "hybrid"


def test_event_id_stable():
    p = parse_event_time("2026年8月28日 09:00—18:00", "Asia/Shanghai")
    a = stable_event_id("AI 老板增长营", p.start)
    b = stable_event_id("AI  老板增长营", p.start)
    assert a == b


def _facts(**overrides):
    base = dict(
        name="AI老板增长营",
        time_raw="2026年8月28日 09:00—18:00",
        delivery_mode_raw="线下",
        delivery_mode="offline",
        location="杭州",
        city="杭州",
        main_content="帮助传统企业老板了解AI怎么进入营销和经营环节。",
        problems_raw="不知道AI怎么落地、团队不会使用AI、获客成本高。",
        target_audience_raw="企业老板和核心管理层",
        source_status_raw="正常报名",
        source_status="active",
        raw_text=(
            "活动名称：AI老板增长营\n主要内容：帮助传统企业老板了解AI怎么进入营销和经营环节。\n"
            "解决问题：不知道AI怎么落地、团队不会使用AI、获客成本高。\n"
            "适合人群：企业老板和核心管理层。\n时间：2026年8月28日 09:00—18:00。\n"
            "地点：杭州。\n状态：正常报名。"
        ),
        source_file="sample.md",
        source_index=1,
    )
    base.update(overrides)
    return EventFacts(**base)


def _ai(**overrides):
    base = dict(
        target_audience="企业老板和核心管理层",
        event_type="训练营",
        problems=["不知道AI怎么落地", "团队不会使用AI"],
        topics=["AI应用", "获客增长"],
        industries=[],
        business_stages=[],
        retrieval_summary="面向企业老板和核心管理层的AI应用训练营，围绕营销经营场景和落地问题展开。",
        search_phrases=["企业AI应用活动", "老板AI训练营", "AI如何用于经营"],
    )
    base.update(overrides)
    return AIEnrichment(**base)


def test_qwen_client_uses_bailian_api_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("BAILIAN_API_KEY", "bailian-test-key")

    q = QwenClient(mode="realtime", mock=False)

    assert q.api_key == "bailian-test-key"
    assert q._headers()["Authorization"] == "Bearer bailian-test-key"


def test_qwen_client_rejects_dashscope_api_key_without_network(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-key")
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.setattr(
        "clean_events.requests.post", lambda *args, **kwargs: pytest.fail("不应调用网络")
    )

    q = QwenClient(mode="realtime", mock=False)

    assert q.api_key == ""
    with pytest.raises(RuntimeError, match="BAILIAN_API_KEY"):
        q._post_json_with_retry("https://example.invalid", {}, 1)


def test_markdown_heading_split_and_extract():
    text = """# 活动名称：活动A
主要内容：A内容
时间：2026年8月1日
地点：线上
状态：正常报名

## 活动名称：活动B
主要内容：B内容
时间：2026年8月2日
地点：线上
状态：正常报名
"""
    blocks = split_by_activity_name(text)
    assert len(blocks) == 2
    assert parse_facts(blocks[0], "x.md", 1).name == "活动A"
    assert parse_facts(blocks[1], "x.md", 2).name == "活动B"


def test_date_only_registration_deadline_uses_end_of_day():
    ts = parse_optional_deadline("2026年8月18日", "Asia/Shanghai")
    assert ts is not None
    dt = datetime.fromtimestamp(ts, tz=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-18 23:59:59"


def test_unknown_source_status_is_rejected():
    facts = _facts(
        source_status_raw="老板说看情况", source_status=normalize_source_status("老板说看情况")
    )
    missing, _ = validate_required(facts, "Asia/Shanghai")
    assert "活动状态无法标准化" in missing


def test_qwen_payload_uses_strict_json_schema_and_minimal_evidence():
    q = QwenClient(mode="realtime", mock=True)
    facts = _facts(
        registration_link="https://example.com/register",
        price="999元",
        guests="张老师",
    )
    payload = q.build_enrichment_payload(facts)
    assert payload["enable_thinking"] is False
    assert payload["response_format"]["type"] == "json_schema"
    js = payload["response_format"]["json_schema"]
    assert js["strict"] is True
    assert js["schema"]["additionalProperties"] is False
    assert set(js["schema"]["required"]) == set(js["schema"]["properties"])
    user_text = payload["messages"][1]["content"]
    # 这些硬事实不需要交给 AI 做语义增强，避免误改/污染。
    assert "https://example.com/register" not in user_text
    assert "999元" not in user_text
    assert "张老师" not in user_text
    assert "2026年8月28日" not in user_text
    assert "杭州" not in user_text


def test_ai_business_validation_rejects_invented_hard_fact():
    facts = _facts()
    bad = _ai(
        retrieval_summary="面向企业老板的AI训练营，2026年9月1日在北京举办，围绕AI经营落地展开。"
    )
    with pytest.raises(ValueError, match="硬事实"):
        validate_ai_business(bad, facts)


def test_ai_business_validation_accepts_source_hard_fact_if_echoed():
    facts = _facts()
    ok = _ai(retrieval_summary="面向企业老板的AI训练营，原资料中的杭州活动围绕AI经营落地展开。")
    validate_ai_business(ok, facts)


def test_rendered_markdown_excludes_metadata_audit_block():
    facts = _facts()
    pt = parse_event_time(facts.time_raw, "Asia/Shanghai")
    md = render_markdown(facts, _ai(), pt, "evt_test")
    assert "Metadata（审计副本）" not in md
    assert "event_start_ts" not in md
    assert "<<<DIFY_CHILD>>>" in md
    assert "## 活动事实" in md


def test_metadata_deadline_uses_requested_timezone():
    facts = _facts(registration_deadline_raw="2026年8月18日")
    pt = parse_event_time(facts.time_raw, "Asia/Shanghai")
    meta = metadata_for_event(facts, _ai(), pt, "evt_test", "Asia/Shanghai")
    dt = datetime.fromtimestamp(
        meta["registration_deadline_ts"], tz=__import__("zoneinfo").ZoneInfo("Asia/Shanghai")
    )
    assert dt.strftime("%H:%M:%S") == "23:59:59"


def test_dify_metadata_payload_has_event_record_and_numeric_timestamps(monkeypatch):
    q = QwenClient(mode="realtime", mock=True)
    facts = _facts(registration_deadline_raw="2026年8月18日")
    pt = parse_event_time(facts.time_raw, "Asia/Shanghai")
    metadata = metadata_for_event(facts, q.enrich(facts), pt, "evt_test", "Asia/Shanghai")
    captured = {}

    class FakeDify:
        dataset_id = "dataset_x"

        def _request(self, method, path, **kwargs):
            captured.update({"method": method, "path": path, "kwargs": kwargs})
            return {}

    DifyClient.set_document_metadata(
        FakeDify(),
        "doc_1",
        metadata,
        {name: {"id": f"id_{name}"} for name in DIFY_METADATA_TYPES},
    )
    payload = captured["kwargs"]["json"]
    values = {item["name"]: item["value"] for item in payload["operation_data"][0]["metadata_list"]}
    assert values["record_type"] == "event"
    assert isinstance(values["event_start_ts"], int)
    assert isinstance(values["event_end_ts"], int)
    assert isinstance(values["registration_deadline_ts"], int)


def test_batch_file_input_contract():
    q = QwenClient(mode="batch_file", mock=True)
    row = q.build_batch_input_line("evt_123", _facts())
    assert row["custom_id"] == "evt_123"
    assert row["method"] == "POST"
    assert row["url"] == "/v1/chat/completions"
    assert row["body"]["enable_thinking"] is False
    assert row["body"]["response_format"]["type"] == "json_schema"


def test_batch_output_parser_contract():
    q = QwenClient(mode="batch_file", mock=True)
    ai = _ai()
    row = {
        "custom_id": "evt_123",
        "response": {
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(ai.model_dump(), ensure_ascii=False)},
                    }
                ]
            },
        },
        "error": None,
    }
    parsed = q.parse_batch_output_line(row, _facts())
    assert parsed.event_type == "训练营"
    assert parsed.topics == ["AI应用", "获客增长"]


def test_permanent_http_error_does_not_retry(monkeypatch):
    q = QwenClient(mode="realtime", mock=False)
    q.api_key = "test-key"
    q.max_retries = 3
    calls = {"n": 0}

    class Resp:
        ok = False
        status_code = 400
        text = "bad request"
        headers = {}

        def json(self):
            return {}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return Resp()

    monkeypatch.setattr("clean_events.requests.post", fake_post)
    with pytest.raises(QwenCallError) as ei:
        q._post_json_with_retry("https://example.invalid", {}, 1)
    assert ei.value.status_code == 400
    assert ei.value.retryable is False
    assert calls["n"] == 1


def test_429_retries_then_succeeds(monkeypatch):
    q = QwenClient(mode="realtime", mock=False)
    q.api_key = "test-key"
    q.max_retries = 3
    calls = {"n": 0}

    class Resp:
        def __init__(self, status):
            self.status_code = status
            self.ok = status == 200
            self.text = "rate limited" if status != 200 else "{}"
            self.headers = {"Retry-After": "0"}

        def json(self):
            return {"ok": True}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return Resp(429 if calls["n"] == 1 else 200)

    monkeypatch.setattr("clean_events.requests.post", fake_post)
    monkeypatch.setattr("clean_events.time.sleep", lambda *_: None)
    assert q._post_json_with_retry("https://example.invalid", {}, 1) == {"ok": True}
    assert calls["n"] == 2


def test_delivery_mode_does_not_guess_uncertain_location():
    assert infer_delivery_mode("", "待公布") == ""
    assert infer_delivery_mode("", "另行通知") == ""
    assert infer_delivery_mode("", "上海+线上") == "hybrid"


def test_true_batch_file_orchestration_without_network(monkeypatch, tmp_path):
    q = QwenClient(mode="batch_file", mock=False)
    q.api_key = "test-key"
    facts = _facts()
    ai = _ai()
    output_line = json.dumps(
        {
            "custom_id": "evt_123",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(ai.model_dump(), ensure_ascii=False)},
                        }
                    ]
                },
            },
            "error": None,
        },
        ensure_ascii=False,
    )
    calls = []

    class FakeResp:
        def __init__(self, payload=None, text=""):
            self._payload = payload or {}
            self.text = text
            self.ok = True
            self.status_code = 200
            self.headers = {}

        def json(self):
            return self._payload

    def fake_req(method, path, **kwargs):
        calls.append((method, path))
        if path == "/files" and method == "POST":
            return FakeResp({"id": "file_in"})
        if path == "/batches" and method == "POST":
            return FakeResp({"id": "batch_1", "status": "validating"})
        if path == "/batches/batch_1" and method == "GET":
            return FakeResp({"id": "batch_1", "status": "completed", "output_file_id": "file_out"})
        if path == "/files/file_out/content" and method == "GET":
            return FakeResp(text=output_line + "\n")
        raise AssertionError((method, path, kwargs))

    monkeypatch.setattr(q, "_batch_api_request", fake_req)
    result = q.enrich_many_batch_file([("evt_123", facts)], tmp_path)
    assert result["evt_123"].event_type == "训练营"
    assert (tmp_path / "logs" / "batch_jobs").exists()
    assert calls == [
        ("POST", "/files"),
        ("POST", "/batches"),
        ("GET", "/batches/batch_1"),
        ("GET", "/files/file_out/content"),
    ]


def test_dify_document_id_persisted_before_metadata_side_effect(monkeypatch, tmp_path):
    facts = _facts()
    pt = parse_event_time(facts.time_raw, "Asia/Shanghai")
    state = {}
    state_path = tmp_path / "output" / "dify_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    runlog = RunLogger(tmp_path)

    class FakeDify:
        dataset_id = "dataset_x"
        creates = 0
        updates = 0

        def create_document(self, name, text):
            self.creates += 1
            return {"document": {"id": "doc_123"}}

        def update_document(self, document_id, name, text):
            self.updates += 1
            return {"document": {"id": document_id}}

        def set_document_metadata(self, document_id, metadata, fields):
            raise RuntimeError("metadata temporary failure")

    dify = FakeDify()
    with pytest.raises(RuntimeError, match="metadata temporary failure"):
        finalize_event(
            facts=facts,
            ai=_ai(),
            pt=pt,
            event_id="evt_test",
            base_dir=tmp_path,
            timezone="Asia/Shanghai",
            runlog=runlog,
            dify=dify,
            dify_fields={"record_type": {"id": "m1"}},
            state=state,
            state_path=state_path,
            qwen_mode="realtime",
        )
    saved = load_state(state_path)
    assert saved["evt_test"]["document_id"] == "doc_123"
    assert saved["evt_test"]["metadata_synced"] is False
    assert dify.creates == 1


def test_non_stop_finish_reason_is_classified(monkeypatch):
    q = QwenClient(mode="realtime", mock=False)
    q.api_key = "test-key"
    monkeypatch.setattr(
        q,
        "_post_json_with_retry",
        lambda *args, **kwargs: {
            "choices": [{"finish_reason": "content_filter", "message": {"content": "{}"}}]
        },
    )
    with pytest.raises(QwenCallError, match="非正常结束"):
        q._chat_structured(
            "s",
            "u",
            "x",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        )


def _run_mock(base_dir, source_text):
    source = base_dir / "source.md"
    source.write_text(source_text, encoding="utf-8")
    args = build_parser().parse_args(
        [
            "--base-dir",
            str(base_dir),
            "--qwen-mode",
            "realtime",
            "--mock-ai",
        ]
    )
    assert run(args) == 0
    return json.loads((base_dir / "logs" / "run_summary.json").read_text(encoding="utf-8"))


def _valid_online_event(name, date="2026年8月20日"):
    return (
        f"活动名称：{name}\n"
        "主要内容：围绕人工智能应用和内容营销，帮助参与者把方法落到实际工作。\n"
        f"时间：{date}\n"
        "地点：线上训练营\n"
        "状态：正常报名\n"
    )


def test_three_events_all_succeed_as_independent_outputs(tmp_path):
    source = "\n".join(_valid_online_event(f"活动{i}") for i in range(1, 4))
    summary = _run_mock(tmp_path, source)
    assert summary["events_seen"] == 3
    assert summary["events_cleaned"] == 3
    assert summary["events_skipped"] == 0
    assert summary["events_failed"] == 0
    assert len(list((tmp_path / "output" / "cleaned").glob("*.md"))) == 3
    assert len(list((tmp_path / "output" / "metadata").glob("*.metadata.json"))) == 3


def test_audit_metadata_omits_raw_text_and_source_filename(tmp_path):
    _run_mock(tmp_path, _valid_online_event("不写入原文的活动"))
    meta_path = next((tmp_path / "output" / "metadata").glob("*.metadata.json"))
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "raw_text" not in metadata["facts"]
    assert "source_file" not in metadata["facts"]
    assert "source_ref" in metadata
    manifest = json.loads((tmp_path / "output" / "manifest.jsonl").read_text(encoding="utf-8"))
    assert "source_file" not in manifest
    assert manifest["source_ref"].startswith("source_")


def test_middle_missing_time_does_not_block_following_event(tmp_path):
    source = "\n".join(
        [
            _valid_online_event("前一个活动"),
            "活动名称：缺时间活动\n主要内容：这条资料故意缺少日期。\n地点：线上\n状态：正常报名\n",
            _valid_online_event("后一个活动", "2026年8月21日"),
        ]
    )
    summary = _run_mock(tmp_path, source)
    assert summary["events_seen"] == 3
    assert summary["events_cleaned"] == 2
    assert summary["events_skipped"] == 1
    assert summary["events_failed"] == 0
    skipped = (tmp_path / "logs" / "skipped_events.jsonl").read_text(encoding="utf-8")
    assert "活动时间" in skipped
    assert len(list((tmp_path / "output" / "cleaned").glob("*.md"))) == 2


@pytest.mark.parametrize(
    "block, reason",
    [
        (
            "活动名称：\n时间：2026年8月20日\n地点：线上\n主要内容：内容\n状态：正常报名\n",
            "活动名称",
        ),
        ("活动名称：缺内容\n时间：2026年8月20日\n地点：线上\n状态：正常报名\n", "主要内容"),
    ],
)
def test_missing_name_or_main_content_is_skipped(block, reason):
    facts = parse_facts(block, "x.md", 1)
    missing, _ = validate_required(facts, "Asia/Shanghai")
    assert reason in missing


def test_online_without_specific_address_is_valid():
    facts = parse_facts(
        "活动名称：线上活动\n时间：2026年8月20日\n活动形式：线上\n主要内容：线上内容\n状态：正常报名\n",
        "x.md",
        1,
    )
    missing, _ = validate_required(facts, "Asia/Shanghai")
    assert missing == []
    assert facts.delivery_mode == "online"


def test_offline_without_location_or_with_placeholder_is_skipped():
    for location in ("", "待公布"):
        block = (
            f"活动名称：线下活动\n时间：2026年8月20日\n活动形式：线下\n"
            f"地点：{location}\n主要内容：现场内容\n状态：正常报名\n"
        )
        facts = parse_facts(block, "x.md", 1)
        missing, _ = validate_required(facts, "Asia/Shanghai")
        assert "线下/混合活动地点" in missing


def test_source_event_type_has_priority_over_name_keyword():
    facts = _facts(event_type_raw="沙龙", name="AI训练营特别活动")
    q = QwenClient(mode="realtime", mock=True)
    assert q.enrich(facts).event_type == "沙龙"


def test_missing_event_type_is_classified_and_source_audience_is_preserved():
    facts = _facts(event_type_raw="", target_audience_raw="只给原文的人群")
    q = QwenClient(mode="realtime", mock=True)
    ai = q.enrich(facts)
    final = apply_source_overrides(facts, ai)
    assert ai.event_type == "训练营"
    assert final.target_audience == "只给原文的人群"


@pytest.mark.parametrize(
    "raw",
    [
        "2026年8月20日—8月22日",
        "2026年8月20日 09:00",
        "2026年8月20日 23:00—2026年8月21日 01:00",
    ],
)
def test_supported_single_day_and_cross_day_time_forms(raw):
    parsed = parse_event_time(raw, "Asia/Shanghai")
    assert parsed.end >= parsed.start


def test_end_before_start_is_rejected():
    with pytest.raises(ValueError, match="早于开始"):
        parse_event_time("2026年8月20日 18:00—09:00", "Asia/Shanghai")


def test_ended_cancelled_and_postponed_statuses_are_source_facts():
    assert normalize_source_status("已结束") == "completed"
    assert normalize_source_status("活动取消") == "cancelled"
    assert normalize_source_status("延期，时间待定") == "postponed_unknown"


def test_schema_extra_and_invalid_enum_are_rejected():
    base = _ai().model_dump()
    with pytest.raises(ValidationError):
        AIEnrichment.model_validate({**base, "unexpected": "x"})
    with pytest.raises(ValidationError):
        AIEnrichment.model_validate({**base, "event_type": "不存在的类型"})


@pytest.mark.parametrize(
    "summary",
    [
        "面向企业老板的活动安排在8月20日举行，帮助解决经营问题。",
        "面向企业老板的活动收费999元，帮助解决经营问题。",
        "面向企业老板的活动报名地址是https://evil.example，帮助解决经营问题。",
    ],
)
def test_ai_cannot_invent_month_day_price_or_link(summary):
    with pytest.raises(ValueError, match="硬事实"):
        validate_ai_business(_ai(retrieval_summary=summary), _facts())


def test_single_model_failure_does_not_block_following_events(monkeypatch, tmp_path):
    original = QwenClient.enrich

    def fail_one(self, facts):
        if facts.name == "中间失败活动":
            raise RuntimeError("模拟单活动模型失败")
        return original(self, facts)

    monkeypatch.setattr(QwenClient, "enrich", fail_one)
    source = "\n".join(
        [
            _valid_online_event("前一个活动"),
            _valid_online_event("中间失败活动", "2026年8月21日"),
            _valid_online_event("后一个活动", "2026年8月22日"),
        ]
    )
    summary = _run_mock(tmp_path, source)
    assert summary["events_cleaned"] == 2
    assert summary["events_failed"] == 1


def test_repeat_run_keeps_manifest_and_event_id_stable(tmp_path):
    source = _valid_online_event("可重复活动")
    first = _run_mock(tmp_path, source)
    manifest = (tmp_path / "output" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    first_id = json.loads(manifest[0])["event_id"]
    second = _run_mock(tmp_path, source)
    manifest2 = (tmp_path / "output" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert first["events_cleaned"] == second["events_cleaned"] == 1
    assert len(manifest2) == 1
    assert json.loads(manifest2[0])["event_id"] == first_id


def test_qwen_call_and_retry_counters_are_recorded(monkeypatch):
    q = QwenClient(mode="realtime", mock=False)
    q.api_key = "test-key"
    q.max_retries = 3

    class Resp:
        ok = True
        status_code = 200
        text = "{}"
        headers = {}

        def json(self):
            return {"ok": True}

    monkeypatch.setattr("clean_events.requests.post", lambda *args, **kwargs: Resp())
    q._post_json_with_retry("https://example.invalid", {}, 1)
    assert q.call_count == 1
    assert q.retry_count == 0


def test_401_does_not_retry(monkeypatch):
    q = QwenClient(mode="realtime", mock=False)
    q.api_key = "test-key"
    q.max_retries = 3
    calls = {"n": 0}

    class Resp:
        ok = False
        status_code = 401
        text = "unauthorized"
        headers = {}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return Resp()

    monkeypatch.setattr("clean_events.requests.post", fake_post)
    with pytest.raises(QwenCallError) as exc_info:
        q._post_json_with_retry("https://example.invalid", {}, 1)
    assert exc_info.value.retryable is False
    assert calls["n"] == 1
