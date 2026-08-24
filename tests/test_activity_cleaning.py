import json
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest

from ai_management.activity_cleaning import (
    EVENT_METADATA_FIELDS,
    EVENT_METADATA_TYPES,
    ActivityCleaningStrategy,
    ActivityFacts,
    default_activity_window,
    filter_recent_activities,
    metadata_for_activity,
    normalize_event_id,
)
from ai_management.cleaning import STRATEGY_REGISTRY, dify_activity_document_name
from knowledge_cleaner.dify import DifyKnowledgeClient


def _source(*blocks: str) -> str:
    return "\n\n".join(blocks)


def test_activity_strategy_is_registered_and_supports_text_formats(tmp_path: Path):
    assert isinstance(STRATEGY_REGISTRY["recent_activity"], ActivityCleaningStrategy)
    assert STRATEGY_REGISTRY["recent_activity"].supported_extensions == {
        ".md",
        ".markdown",
        ".txt",
        ".docx",
    }
    assert set(EVENT_METADATA_TYPES) == set(EVENT_METADATA_FIELDS)
    assert {name for name, kind in EVENT_METADATA_TYPES.items() if kind == "number"} == {
        "event_start_ts",
        "event_end_ts",
        "registration_deadline_ts",
    }

    path = tmp_path / "events.md"
    path.write_text(
        _source(
            """活动名称：AI增长工作坊
活动时间：2099年3月1日 10:00—12:00
活动形式：线上
活动主要讲什么：讨论AI获客实践
活动状态：正常
报名链接：https://example.com/register""",
            """活动名称：已取消的活动
活动时间：2099年3月2日
活动形式：线上
活动主要讲什么：取消通知
活动状态：已取消""",
        ),
        encoding="utf-8",
    )

    documents = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)

    assert len(documents) == 2
    assert documents[0].metadata["record_type"] == "event"
    assert documents[0].metadata["event_status"] == "normal"
    assert documents[1].metadata["event_status"] == "cancelled"
    assert documents[0].metadata["event_id"] == normalize_event_id("AI增长工作坊")
    assert documents[0].metadata["registration_url"] == "https://example.com/register"


def test_missing_one_activity_does_not_block_following_activity(tmp_path: Path):
    path = tmp_path / "events.txt"
    path.write_text(
        _source(
            """活动名称：缺少时间
活动形式：线上
活动主要讲什么：无法发布
活动状态：正常""",
            """活动名称：后续活动
活动时间：2099-03-03
活动形式：线上
活动主要讲什么：继续发布
活动状态：正常""",
        ),
        encoding="utf-8",
    )

    documents = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)

    assert [item.title for item in documents] == ["后续活动"]


def test_postponed_unknown_never_reuses_old_date_and_keeps_source_status(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：延期活动
活动时间：延期，原定2099年3月4日
活动形式：线上
活动主要讲什么：等待通知
活动状态：延期""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]

    assert document.metadata["event_status"] == "postponed"
    assert document.metadata["source_status"] == "延期"
    assert "延期未定" in document.markdown
    assert "event_start_ts" not in document.metadata
    assert "event_end_ts" not in document.metadata


def test_postponed_change_uses_new_date_only(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：改期活动
活动时间：原定2099年3月4日，延期至2099年3月14日
活动形式：线上
活动主要讲什么：新的安排
活动状态：延期""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]
    start, _ = default_activity_window(now="2099-03-14T00:00:00+08:00")

    assert document.metadata["event_start_ts"] == int(start.timestamp())


def test_postponed_new_date_before_old_date_ignores_old_date(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：新日期在前
活动时间：延期至2099年3月14日，原定2099年3月4日
活动形式：线上
活动主要讲什么：新的安排
活动状态：延期""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]
    start, _ = default_activity_window(now="2099-03-14T00:00:00+08:00")

    assert document.metadata["event_start_ts"] == int(start.timestamp())
    assert document.metadata["event_end_ts"] > document.metadata["event_start_ts"]


def test_postponed_new_date_with_omitted_year_uses_old_date_year(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：省略年份延期活动
活动时间：原定2099年3月4日，延期至3月14日
活动形式：线上
活动主要讲什么：新的安排
活动状态：延期""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]
    start, _ = default_activity_window(now="2099-03-14T00:00:00+08:00")

    assert document.metadata["event_start_ts"] == int(start.timestamp())


def test_postponed_new_date_with_omitted_year_can_roll_over_to_next_year(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：跨年延期活动
活动时间：原定2099年12月31日，延期至1月2日
活动形式：线上
活动主要讲什么：跨年后的新安排
活动状态：延期""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]
    end = datetime.fromtimestamp(document.metadata["event_end_ts"], tz=ZoneInfo("Asia/Shanghai"))

    assert end.year == 2100
    assert (end.month, end.day, end.hour, end.minute, end.second) == (1, 2, 23, 59, 59)


def test_mixed_invalid_activity_blocks_are_redacted_in_audit(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        _source(
            """活动名称：有效活动
活动时间：2099年3月20日
活动形式：线上
活动主要讲什么：可发布
活动状态：正常""",
            """活动名称：缺时间活动
活动形式：线上
活动主要讲什么：跳过
活动状态：正常""",
        ),
        encoding="utf-8",
    )

    documents = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)

    assert len(documents) == 1
    assert documents[0].audit["activity_skips"] == [
        {
            "block_index": 2,
            "code": "REQUIRED_FACT_MISSING",
            "reason": "活动名称、时间、主要内容和状态为必需事实",
        }
    ]
    assert "缺时间活动" not in str(documents[0].audit)


def test_postponed_to_pending_does_not_reuse_old_date(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：待定延期活动
活动时间：延期至待定，原定2099年3月4日
活动形式：线上
活动主要讲什么：等待通知
活动状态：延期""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]

    assert document.metadata["event_status"] == "postponed"
    assert "event_start_ts" not in document.metadata
    assert "event_end_ts" not in document.metadata


def test_iso_single_day_activity_ends_at_end_of_day(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：当天活动
活动时间：2099-03-03
活动形式：线上
活动主要讲什么：当天进行
活动状态：正常""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]
    window_start, _ = default_activity_window(now="2099-03-03T12:00:00+08:00")

    assert document.metadata["event_start_ts"] == 4076150400
    assert document.metadata["event_end_ts"] == 4076236799
    assert document.metadata["event_start_ts"] < int(window_start.timestamp())
    assert document.metadata["event_end_ts"] > int(window_start.timestamp())
    assert filter_recent_activities([document.metadata], now=window_start)


def test_chinese_range_with_omitted_end_year_and_full_month_keeps_end_date(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：中文范围活动
活动时间：2099年3月1日—3月3日
活动形式：线上
活动主要讲什么：覆盖中文日期范围
活动状态：正常""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]

    assert document.metadata["event_end_ts"] > document.metadata["event_start_ts"]
    assert document.metadata["event_end_ts"] == 4076236799


def test_chinese_range_with_omitted_end_month_keeps_end_date(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：省略月份范围活动
活动时间：2099年3月1日—23日
活动形式：线上
活动主要讲什么：覆盖省略月份的中文日期范围
活动状态：正常""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]

    assert document.metadata["event_end_ts"] == 4077964799


def test_chinese_range_with_omitted_end_year_can_roll_over_to_next_year(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：跨年范围活动
活动时间：2099年12月31日—1月2日
活动形式：线上
活动主要讲什么：覆盖跨年日期范围
活动状态：正常""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]
    end = datetime.fromtimestamp(document.metadata["event_end_ts"], tz=ZoneInfo("Asia/Shanghai"))

    assert end.year == 2100
    assert (end.month, end.day, end.hour, end.minute, end.second) == (1, 2, 23, 59, 59)


def test_quick_deploy_zip_carries_current_activity_parser():
    archive = Path(__file__).parents[1] / "ai_management.zip"
    if not archive.exists():
        pytest.skip("本地未生成快速部署 ZIP")

    with ZipFile(archive) as source:
        entry = next(
            item
            for item in source.namelist()
            if item.rstrip("/").endswith("ai_management/activity_cleaning.py")
        )
        packaged = source.read(entry)

    assert (
        packaged == (Path(__file__).parents[1] / "ai_management/activity_cleaning.py").read_bytes()
    )


def test_qwen_activity_enricher_uses_canonical_stable_request(monkeypatch):
    from ai_management.activity_cleaning import _QwenEnricher

    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": '{"event_type":"沙龙"}'}}]}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("BAILIAN_API_KEY", "test-key")
    monkeypatch.setattr("ai_management.activity_cleaning.urlopen", fake_urlopen)
    facts = ActivityFacts(
        name="活动",
        time_raw="2099-01-01",
        mode_raw="线上",
        location="",
        content="内容",
        status_raw="正常",
        deadline_raw="",
        registration_url="",
        event_type_raw="",
    )

    assert _QwenEnricher()(facts)["event_type"] == "沙龙"
    assert captured["body"]["temperature"] == 0.01
    assert captured["body"]["seed"] == 1234
    assert captured["body"]["enable_thinking"] is False
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_invalid_model_labels_fall_back_to_controlled_defaults(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：受控分类活动
活动时间：2099年3月15日
活动形式：线上
活动主要讲什么：人工智能应用
活动状态：正常""",
        encoding="utf-8",
    )

    document = ActivityCleaningStrategy(
        enricher=lambda _facts: {
            "event_type": "模型幻觉正文",
            "topic": "<script>alert(1)</script>",
            "industry": "制造业",
            "business_stage": "成长期",
        }
    ).clean(path, path.name)[0]

    assert document.metadata["event_type"] == "其他"
    assert document.metadata["topic"] == "AI应用"
    assert document.metadata["industry"] == "制造业"
    assert document.metadata["business_stage"] == "成长期"


def test_metadata_allowlist_excludes_source_and_batch_fields(tmp_path: Path):
    path = tmp_path / "events.md"
    path.write_text(
        """活动名称：白名单活动
活动时间：2099年3月5日
活动形式：线上
活动主要讲什么：测试
活动状态：正常""",
        encoding="utf-8",
    )
    document = ActivityCleaningStrategy(enricher=lambda _facts: {}).clean(path, path.name)[0]
    metadata = metadata_for_activity(document.metadata)

    assert set(metadata) <= ActivityCleaningStrategy.dify_metadata_fields
    assert not {"source_filename", "source_file_id", "source_sha256", "upload_batch_id"} & set(
        metadata
    )


def test_dashscope_key_is_not_a_credential_fallback(monkeypatch):
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "forbidden-fallback")

    with pytest.raises(RuntimeError, match="BAILIAN_API_KEY"):
        ActivityCleaningStrategy().enricher_for_runtime()


def test_recent_window_is_china_time_and_normal_postponed_strict_end_boundary():
    start, end = default_activity_window(now="2099-03-01T12:00:00+08:00")
    rows = [
        {"event_id": "a", "event_status": "normal", "event_start_ts": int(start.timestamp())},
        {
            "event_id": "b",
            "event_status": "cancelled",
            "event_start_ts": int(start.timestamp()) + 1,
        },
        {"event_id": "c", "event_status": "normal", "event_start_ts": int(end.timestamp())},
        {"event_id": "d", "event_status": "normal", "event_start_ts": int(end.timestamp()) + 1},
        {
            "event_id": "ongoing",
            "event_status": "normal",
            "event_start_ts": int(start.timestamp()) - 86400,
            "event_end_ts": int(start.timestamp()) + 86400,
        },
    ]
    assert [row["event_id"] for row in filter_recent_activities(rows, now=start)] == [
        "ongoing",
        "a",
        "c",
    ]


def test_recent_activity_default_result_cap_is_ten():
    now = datetime(2099, 3, 1, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    start_ts = int(now.timestamp()) + 3600
    rows = [
        {
            "event_id": f"event-{index}",
            "event_status": "normal",
            "event_start_ts": start_ts + index * 3600,
            "event_end_ts": start_ts + index * 3600 + 1800,
        }
        for index in range(12)
    ]
    assert len(filter_recent_activities(rows, now=now)) == 10


def test_all_recent_window_includes_four_statuses_and_sorts_ended_nearest_first():
    now, future = default_activity_window(now="2099-03-01T12:00:00+08:00")
    rows = [
        {
            "event_id": "old",
            "event_status": "completed",
            "event_start_ts": int(now.timestamp()) - 86400 * 2,
            "event_end_ts": int(now.timestamp()) - 3600,
        },
        {
            "event_id": "cancelled",
            "event_status": "cancelled",
            "event_start_ts": int(now.timestamp()) + 3600,
            "event_end_ts": int(now.timestamp()) + 7200,
        },
        {
            "event_id": "ongoing",
            "event_status": "postponed",
            "event_start_ts": int(now.timestamp()) - 3600,
            "event_end_ts": int(now.timestamp()) + 3600,
        },
    ]
    assert [
        row["event_id"] for row in filter_recent_activities(rows, now=now, include_status="all")
    ] == ["ongoing", "cancelled", "old"]


def test_activity_metadata_fields_are_numeric_and_type_conflicts_fail_closed():
    client = object.__new__(DifyKnowledgeClient)
    client.dataset_id = "dataset-test"
    requests = []

    def request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        if method == "GET":
            return {"doc_metadata": []}
        return {"id": path.rsplit("/", 1)[-1] + "-id"}

    client._request = request
    field_ids = client.ensure_metadata_names(
        ["event_start_ts"], field_types={"event_start_ts": "number"}
    )
    client.set_document_metadata(
        "doc-1",
        {"event_start_ts": 123, "source_title": "活动"},
        {**field_ids, "source_title": "title-id"},
        field_types={"event_start_ts": "number"},
    )
    payload = requests[-1][2]["json"]["operation_data"][0]["metadata_list"]
    assert next(item["value"] for item in payload if item["name"] == "event_start_ts") == 123

    conflict = object.__new__(DifyKnowledgeClient)
    conflict.dataset_id = "dataset-test"
    conflict._request = lambda *_args, **_kwargs: {
        "doc_metadata": [{"name": "event_start_ts", "id": "field-1", "type": "string"}]
    }
    with pytest.raises(RuntimeError, match="类型冲突"):
        conflict.ensure_metadata_names(["event_start_ts"], field_types={"event_start_ts": "number"})

    string_conflict = object.__new__(DifyKnowledgeClient)
    string_conflict.dataset_id = "dataset-test"
    string_conflict._request = lambda *_args, **_kwargs: {
        "doc_metadata": [{"name": "event_status", "id": "field-2", "type": "number"}]
    }
    with pytest.raises(RuntimeError, match="类型冲突"):
        string_conflict.ensure_metadata_names(
            ["event_status"], field_types={"event_status": "string"}
        )


def test_same_name_update_keeps_event_identity_while_changed_date_is_new_content(tmp_path: Path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    template = """活动名称：稳定活动
活动时间：{date}
活动形式：线上
活动主要讲什么：更新内容
活动状态：正常"""
    first.write_text(template.format(date="2099年3月6日"), encoding="utf-8")
    second.write_text(template.format(date="2099年3月7日"), encoding="utf-8")

    strategy = ActivityCleaningStrategy(enricher=lambda _facts: {})
    old = strategy.clean(first, first.name)[0].metadata
    new = strategy.clean(second, second.name)[0].metadata

    assert old["event_id"] == new["event_id"]
    assert old["event_start_ts"] != new["event_start_ts"]


def test_activity_dify_name_uses_activity_prefix_and_revision_suffix():
    first = dify_activity_document_name("AI增长工作坊", "evt_abc123", "a" * 64)
    second = dify_activity_document_name("AI增长工作坊", "evt_abc123", "b" * 64)

    assert first.startswith("AI增长工作坊__evt_abc123__")
    assert first.endswith("a" * 16 + ".md")
    assert first != second
