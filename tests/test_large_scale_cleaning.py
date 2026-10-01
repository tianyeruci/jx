from __future__ import annotations

import json
import tracemalloc
from types import SimpleNamespace

import pytest

from knowledge_cleaner.chunking import DIFY_CHILD_SEPARATOR, DIFY_PARENT_SEPARATOR


def test_universal_sanitizer_is_idempotent_and_preserves_markdown_structure():
    from ai_management.sanitization import sanitize_for_knowledge

    source = (
        "# 方法\n\n"
        "先看[完整教程](https://example.com/path?q=1)，再执行。   \t  \n\n\n"
        "| 步骤 | 结果 |\n| --- | --- |\n| 1 | 有效 |\n\n"
        "联系邮箱 demo@example.com，微信号：demo_12345，手机号 13800138000。\n"
        "扫码加微信领取资料。\n\n"
        f"{DIFY_PARENT_SEPARATOR}\n父块\n{DIFY_CHILD_SEPARATOR}\n子块"
    )

    cleaned = sanitize_for_knowledge(source)

    assert "[完整教程]" not in cleaned
    assert "完整教程" in cleaned
    assert "https://" not in cleaned
    assert "demo@example.com" not in cleaned
    assert "demo_12345" not in cleaned
    assert "13800138000" not in cleaned
    assert "扫码加微信领取资料" not in cleaned
    assert "# 方法" in cleaned
    assert "| 步骤 | 结果 |" in cleaned
    assert DIFY_PARENT_SEPARATOR in cleaned
    assert DIFY_CHILD_SEPARATOR in cleaned
    assert sanitize_for_knowledge(cleaned) == cleaned


def test_universal_sanitizer_removes_urls_adjacent_to_chinese_text():
    from ai_management.sanitization import sanitize_for_knowledge

    cleaned = sanitize_for_knowledge(
        "请看https://example.com/guide，然后访问www.example.org/path。"
    )

    assert "https://" not in cleaned
    assert "www.example.org" not in cleaned


def test_prompt_and_custom_id_do_not_depend_on_filename():
    from ai_management.model_cleaning import (
        PromptRequest,
        build_custom_id,
        build_prompt_messages,
    )

    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="item-1",
        content_sha256="a" * 64,
        title="商业复盘",
        blocks={"b001": "先访谈客户，再调整产品定位。"},
    )
    first = build_prompt_messages(request)
    second = build_prompt_messages(request)

    assert first == second
    serialized = json.dumps(first, ensure_ascii=False)
    assert '"filename"' not in serialized
    assert "原始文档.md" not in serialized
    assert build_custom_id(request, "semantic-v1") == build_custom_id(request, "semantic-v1")


def test_realtime_and_batch_request_body_share_the_same_model_policy():
    from ai_management.model_cleaning import (
        PromptRequest,
        batch_request_line,
        build_model_request_body,
        build_prompt_messages,
    )

    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="item-1",
        content_sha256="a" * 64,
        title="商业复盘",
        blocks={"b001": "先访谈客户，再调整产品定位。"},
    )
    messages = build_prompt_messages(request)
    realtime_body = build_model_request_body(messages, model="qwen-plus")
    batch_body = json.loads(
        batch_request_line(request, prompt_version="semantic-v1", model="qwen-plus")
    )["body"]
    assert batch_body == realtime_body
    assert realtime_body["temperature"] == 0.01
    assert realtime_body["seed"] == 1234
    assert realtime_body["enable_thinking"] is False


def test_post_prompt_declares_the_only_valid_machine_values():
    from ai_management.model_cleaning import PromptRequest, build_prompt_messages

    prompt = build_prompt_messages(
        PromptRequest(
            strategy_code="zsxq_post",
            item_id="item-1",
            content_sha256="a" * 64,
            title="商业复盘",
            blocks={"b001": "先访谈客户，再调整产品定位。"},
        )
    )[0]["content"]

    assert "keep、exclude、review" in prompt
    assert "direct、transferable、none、ambiguous" in prompt
    assert "0 到 1" in prompt
    assert "战略与商业模式" in prompt
    assert "AI与智能体" in prompt
    assert '"business_relevance":"direct"' in prompt


def test_consultation_prompt_and_validator_normalize_known_fact_state_variants():
    from ai_management.model_cleaning import (
        PromptRequest,
        build_prompt_messages,
        validate_consultation_response,
    )

    prompt = build_prompt_messages(
        PromptRequest(
            strategy_code="personal_business_consultation",
            item_id="item-1",
            content_sha256="a" * 64,
            title="咨询案例",
            blocks={"b001": "客户计划验证产品定位。"},
        )
    )[0]["content"]
    parsed = validate_consultation_response(
        {
            "sections": [
                {
                    "heading": "行动计划",
                    "text": "客户计划验证产品定位。",
                    "source_block_ids": ["b001"],
                    "fact_state": "行动计划",
                }
            ]
        },
        available_block_ids={"b001"},
        source_blocks={"b001": "客户计划验证产品定位。"},
    )

    for value in (
        "client_statement",
        "advisor_judgment",
        "decision",
        "plan",
        "actual_result",
    ):
        assert value in prompt
    assert parsed.sections[0].fact_state == "plan"


def test_consultation_prompt_describes_current_v1_contract():
    from ai_management.model_cleaning import PromptRequest, build_prompt_messages

    prompt = build_prompt_messages(
        PromptRequest(
            strategy_code="personal_business_consultation",
            item_id="item-1",
            content_sha256="a" * 64,
            title="咨询案例",
            blocks={"b001": "客户计划验证产品定位。"},
        )
    )[0]["content"]

    for field in (
        "consultation-rag-v1",
        "primary_problem",
        "relevant_background",
        "problem_signals",
        "retrieval_queries",
        "reference_advice",
        "advice_summary",
        "methodology_judgment",
        "methodology_steps",
        "methodology_limits",
        "evidence",
    ):
        assert field in prompt
    assert "client_statement|advisor_judgment|decision|plan|actual_result" in prompt
    assert "不得产生新的人工复核状态或原文兜底发布" in prompt
    assert "consulting_case_v2" not in prompt


def test_consultation_validator_accepts_equivalent_negation_and_drops_empty_template():
    """Qwen's ``未``/``没有`` variation is not a change of factual polarity."""

    from ai_management.model_cleaning import validate_consultation_response

    payload = {
        "sections": [
            {
                "heading": "行动计划",
                "text": "客户没有确认报价，先继续验证。",
                "source_block_ids": ["b001"],
                "fact_state": "plan",
            },
            {
                "heading": "实际结果",
                "text": "",
                "source_block_ids": [],
                "fact_state": "actual_result",
            },
        ]
    }
    parsed = validate_consultation_response(
        payload,
        available_block_ids={"b001"},
        source_blocks={"b001": "客户未确认报价，先继续验证。"},
    )

    assert len(parsed.sections) == 1
    assert parsed.sections[0].fact_state == "plan"

    with pytest.raises(ValueError, match="否定状态"):
        validate_consultation_response(
            {"sections": [payload["sections"][0]]},
            available_block_ids={"b001"},
            source_blocks={"b001": "客户已确认报价，先继续验证。"},
        )


def test_contract_retry_prompt_is_structural_and_has_no_filename():
    from ai_management.model_cleaning import PromptRequest, build_prompt_messages

    messages = build_prompt_messages(
        PromptRequest(
            strategy_code="zsxq_post",
            item_id="item-1",
            content_sha256="a" * 64,
            title="商业复盘",
            blocks={"b001": "先验证客户问题。"},
        ),
        repair_hint="输入 blocks 的每一个 key 必须且只能出现一次。",
    )

    assert "上一次输出未通过结构契约" in messages[-1]["content"]
    assert "每一个 key 必须且只能出现一次" in messages[-1]["content"]
    assert "filename" not in messages[-1]["content"]


@pytest.mark.parametrize(
    ("strategy", "candidates", "tokens", "batch_enabled", "expected"),
    [
        ("personal_business_consultation", 500, 1_000_000, False, "realtime"),
        ("personal_business_consultation", 100, 1, True, "batch"),
        ("zsxq_post", 99, 199_999, False, "realtime"),
        ("zsxq_post", 100, 1, True, "batch"),
        ("wechat_public_account_cognition", 1, 200_001, True, "batch"),
        ("xiaobot_post", 1, 200_000, True, "batch"),
        ("xiaobot_post", 100, 1, True, "batch"),
        ("recent_activity", 100, 1, True, "realtime"),
    ],
)
def test_model_channel_planner(strategy, candidates, tokens, batch_enabled, expected):
    from ai_management.model_cleaning import choose_call_mode

    assert choose_call_mode(strategy, candidates, tokens, batch_enabled=batch_enabled) == expected


def test_model_channel_planner_defaults_to_realtime():
    from ai_management.model_cleaning import choose_call_mode

    assert choose_call_mode("zsxq_post", 100, 1) == "realtime"


def test_content_review_gate_only_marks_empty_or_short_content():
    from ai_management.cleaning import content_char_count, content_review_reason

    policy = {"enabled": True, "min_chars": 200, "force_review": False}
    assert content_char_count("# 标题\n\n" + ("正文 " * 110)) >= 200
    assert content_review_reason(
        "# 标题\n\n短正文",
        "zsxq_post",
        {
            "_cleaning_review_policy": policy,
        },
    ) == ("CONTENT_TOO_SHORT", "最终正文少于 200 字")
    assert (
        content_review_reason(
            "正文 " * 110,
            "zsxq_post",
            {
                "_cleaning_review_policy": policy,
            },
        )
        is None
    )
    assert (
        content_review_reason(
            "正常正文",
            "standard_document",
            {
                "_cleaning_review_policy": policy,
            },
        )
        is None
    )


def test_content_review_gate_can_be_disabled_by_batch_policy_snapshot():
    from ai_management.cleaning import content_review_reason

    policy = {
        "enabled": True,
        "review_required_enabled": False,
        "min_chars": 200,
        "force_review": True,
    }
    assert (
        content_review_reason(
            "短正文",
            "zsxq_post",
            {
                "_cleaning_review_policy": policy,
            },
        )
        is None
    )
    # Missing the new field is fail-closed for historical batches.
    assert content_review_reason(
        "短正文",
        "zsxq_post",
        {
            "_cleaning_review_policy": {
                "enabled": True,
                "min_chars": 200,
                "force_review": False,
            },
        },
    ) == ("CONTENT_TOO_SHORT", "最终正文少于 200 字")


def test_semantic_response_rejects_unknown_but_allows_uncovered_blocks():
    from ai_management.model_cleaning import validate_semantic_response

    valid = {
        "disposition": "keep",
        "business_relevance": "direct",
        "reason_code": "business_method",
        "keep_block_ids": ["b001"],
        "drop_block_ids": ["b002"],
        "topic": "产品与客户",
        "time_sensitive": False,
        "confidence": 0.92,
    }
    parsed = validate_semantic_response("zsxq_post", valid, available_block_ids={"b001", "b002"})
    assert parsed.keep_block_ids == ["b001"]

    with pytest.raises(ValueError, match="未知来源块"):
        validate_semantic_response(
            "zsxq_post",
            {**valid, "keep_block_ids": ["missing"]},
            available_block_ids={"b001", "b002"},
        )
    partial = validate_semantic_response(
        "zsxq_post",
        {**valid, "drop_block_ids": []},
        available_block_ids={"b001", "b002"},
    )
    assert partial.keep_block_ids == ["b001"]
    assert partial.drop_block_ids == []


def test_semantic_response_normalizes_known_qwen_format_variants_only():
    from ai_management.model_cleaning import validate_semantic_response

    parsed = validate_semantic_response(
        "zsxq_post",
        {
            "disposition": "keep",
            "business_relevance": "直接相关",
            "reason_code": "这是一段超出机器字段长度限制的中文说明，需要由本地契约稳定映射为固定原因代码。",
            "keep_block_ids": ["b001"],
            "drop_block_ids": ["b002"],
            "topic": "产品与客户",
            "time_sensitive": False,
            "confidence": "较高",
        },
        available_block_ids={"b001", "b002"},
    )

    assert parsed.business_relevance == "direct"
    assert parsed.reason_code == "business_method"
    assert parsed.confidence == 0.8


@pytest.mark.parametrize(
    ("disposition", "business_relevance", "expected_disposition", "expected_relevance"),
    [
        ("partial_keep", 0.7, "keep", "direct"),
        ("keep_partial", "medium", "keep", "transferable"),
        ("drop", False, "exclude", "none"),
        ("keep", True, "keep", "direct"),
    ],
)
def test_semantic_response_normalizes_observed_qwen_relevance_encodings(
    disposition,
    business_relevance,
    expected_disposition,
    expected_relevance,
):
    from ai_management.model_cleaning import validate_semantic_response

    parsed = validate_semantic_response(
        "zsxq_post",
        {
            "disposition": disposition,
            "business_relevance": business_relevance,
            "reason_code": 1,
            "keep_block_ids": (["b001"] if expected_disposition == "keep" else []),
            "drop_block_ids": ([] if expected_disposition == "keep" else ["b001"]),
            "topic": "产品与客户",
            "time_sensitive": False,
            "confidence": 0.8,
        },
        available_block_ids={"b001"},
    )

    assert parsed.disposition == expected_disposition
    assert parsed.business_relevance == expected_relevance


def test_consultation_response_rejects_unsupported_numbers_and_fact_state():
    from ai_management.model_cleaning import validate_consultation_response

    payload = {
        "sections": [
            {
                "heading": "实际结果",
                "text": "成交率提高了30%。",
                "source_block_ids": ["b001"],
                "fact_state": "actual_result",
            }
        ]
    }
    with pytest.raises(ValueError, match="数字或日期"):
        validate_consultation_response(
            payload,
            available_block_ids={"b001"},
            source_blocks={"b001": "客户计划调整产品定位。"},
        )
    with pytest.raises(ValueError, match="事实状态"):
        validate_consultation_response(
            {
                "sections": [
                    {
                        "heading": "实际结果",
                        "text": "客户调整了产品定位。",
                        "source_block_ids": ["b001"],
                        "fact_state": "plan",
                    }
                ]
            },
            available_block_ids={"b001"},
            source_blocks={"b001": "客户调整了产品定位。"},
        )


def test_post_shards_honor_item_and_utf8_byte_limits():
    from ai_management.publication_shards import ShardItem, plan_post_shards

    items = [
        ShardItem(item_id=f"item-{index}", title=f"标题{index}", markdown="中" * 3000)
        for index in range(205)
    ]
    shards = plan_post_shards(items, max_items=200, max_bytes=100_000)

    assert len(shards) > 2
    assert all(len(shard.items) <= 200 for shard in shards)
    assert all(len(shard.markdown.encode("utf-8")) <= 100_000 for shard in shards)
    assert [item.item_id for shard in shards for item in shard.items] == [
        item.item_id for item in items
    ]


def test_post_shard_keeps_explicit_parent_boundaries_and_titles():
    from ai_management.publication_shards import ShardItem, plan_post_shards

    plans = plan_post_shards(
        [
            ShardItem(
                item_id="one", title="短标题", markdown="【上下文】短标题｜article｜" + "中" * 140
            ),
            ShardItem(
                item_id="two",
                title="另一个标题",
                markdown="【上下文】另一个标题｜article｜" + "文" * 140,
            ),
        ],
        max_items=10,
        max_bytes=10_000,
    )

    assert len(plans) == 2
    assert all(len(plan.items) == 1 for plan in plans)
    assert f"\n{DIFY_PARENT_SEPARATOR}\n" not in plans[0].markdown
    assert "# 短标题" in plans[0].markdown
    assert "# 另一个标题" in plans[1].markdown


def test_post_shards_keep_each_source_item_in_its_own_remote_document():
    from ai_management.publication_shards import ShardItem, plan_post_shards

    items = [
        ShardItem(item_id="post-1", title="第一篇", markdown="正文一"),
        ShardItem(item_id="post-2", title="第二篇", markdown="正文二"),
    ]

    plans = plan_post_shards(items, max_items=200, max_bytes=10_000)

    assert [plan.index for plan in plans] == [1, 2]
    assert [[item.item_id for item in plan.items] for plan in plans] == [
        ["post-1"],
        ["post-2"],
    ]
    assert [plan.markdown for plan in plans] == ["# 第一篇\n\n正文一", "# 第二篇\n\n正文二"]


def test_post_shard_structure_allows_dify_to_split_large_source_items():
    from ai_management.workers.tasks.model_cleaning import _validate_shard_structure

    _validate_shard_structure({"parent_count": 100, "child_count": 175}, item_count=75)
    with pytest.raises(ValueError, match="父块数量不足"):
        _validate_shard_structure({"parent_count": 74, "child_count": 120}, item_count=75)


def test_short_post_child_uses_only_structural_context_or_requires_review():
    from ai_management.workers.tasks.model_cleaning import _post_child_for_dify

    framed = _post_child_for_dify(
        "先访谈客户，再验证产品定位和最小交付。",
        title="定位验证方法",
    )

    assert framed is not None
    assert len(framed) >= 50
    assert "定位验证方法" in framed
    assert "先访谈客户，再验证产品定位和最小交付。" in framed
    near_limit = "中" * 80
    near_limit_framed = _post_child_for_dify(near_limit, title="边界帖子")
    assert near_limit_framed is not None
    assert "【主题】边界帖子" in near_limit_framed
    assert near_limit in near_limit_framed
    assert _post_child_for_dify("中" * 120, title="充分长度") == "中" * 120
    assert _post_child_for_dify("短", title="短") is None


def test_one_hundred_thousand_batch_requests_stream_into_three_files(tmp_path):
    from ai_management.workers.tasks import model_cleaning as worker

    task = SimpleNamespace(prompt_version="scale-v1", model="qwen-plus")
    batch = SimpleNamespace(strategy_code="zsxq_post")

    def documents():
        for index in range(100_000):
            yield SimpleNamespace(
                id=f"item-{index}",
                content_sha256=f"{index:064x}",
                title="商业方法",
                audit_json={"model_blocks": {"b0001": "先验证客户问题。"}},
            )

    tracemalloc.start()
    paths = worker._write_batch_files(
        tmp_path,
        task,
        batch,
        documents(),
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    custom_ids: set[str] = set()
    line_count = 0
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line_count += 1
                custom_ids.add(json.loads(line)["custom_id"])
    assert len(paths) == 3
    assert line_count == 100_000
    assert len(custom_ids) == 100_000
    assert peak < 512 * 1024 * 1024


def test_toc_is_used_for_hierarchy_but_removed_from_stored_text():
    from ai_management.document_structure import remove_reliable_toc

    source = (
        "# 目录\n"
        "第一章 定位方法 ........ 3\n"
        "第二章 产品设计 ........ 8\n\n"
        "# 第一章 定位方法\n"
        "定位要从客户问题开始。\n\n"
        "# 第二章 产品设计\n"
        "产品设计需要验证付费意愿。"
    )
    result = remove_reliable_toc(source)

    assert result.detected is True
    assert result.needs_review is False
    assert "........ 3" not in result.markdown
    assert "# 第一章 定位方法" in result.markdown
    assert result.headings == ["第一章 定位方法", "第二章 产品设计"]


def test_toc_requires_dense_early_entries_and_majority_heading_match():
    from ai_management.document_structure import remove_reliable_toc

    later_reference_list = (
        "# 正文\n\n定位方法。\n\n"
        + "\n".join(f"普通段落{index}" for index in range(100))
        + "\n附录A ........ 3\n附录B ........ 8\n附录C ........ 12"
    )
    assert remove_reliable_toc(later_reference_list).detected is False

    weak_match = (
        "# 目录\n"
        "第一章 定位 ........ 3\n"
        "第二章 产品 ........ 8\n"
        "第三章 销售 ........ 12\n"
        "第四章 组织 ........ 18\n\n"
        "# 第一章 定位\n正文\n\n"
        "# 第二章 产品\n正文"
    )
    result = remove_reliable_toc(weak_match)
    assert result.detected is True
    assert result.needs_review is True
    assert "第一章 定位 ........ 3" in result.markdown


def test_explicit_dense_toc_without_recovered_body_headings_is_removed():
    """Text-only PDFs may lose headings while retaining an unmistakable TOC."""
    from ai_management.document_structure import remove_reliable_toc

    source = (
        "目录\n"
        "第一章 定位 ........ 3\n"
        "第二章 产品 ........ 8\n"
        "第三章 内容 ........ 12\n"
        "第四章 销售 ........ 18\n"
        "第五章 交付 ........ 24\n"
        "第六章 复盘 ........ 30\n\n"
        "# 正文\n"
        "这是没有被 PDF 转换器标成章节标题的正文。"
    )

    result = remove_reliable_toc(source)

    assert result.detected is True
    assert result.needs_review is False
    assert "第一章 定位 ........ 3" not in result.markdown
    assert "这是没有被 PDF 转换器标成章节标题的正文。" in result.markdown


def test_nonblocking_pdf_table_warning_keeps_readable_linear_text(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ai_management.cleaning import STRATEGY_REGISTRY
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "readable.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(
        pdf_convert,
        "convert_pdf",
        lambda *_args, **_kwargs: SimpleNamespace(
            markdown="# 产品验证\n\n先访谈客户，再验证付费意愿。",
            needs_review=True,
            review_reasons=["table_review:表格结构无法可靠对齐，已保留线性单元格文本"],
        ),
    )

    result = STRATEGY_REGISTRY["lead_magnet"].clean(source, "任意名字.pdf")

    assert len(result) == 1
    assert result[0].disposition == "pending"
    assert "付费意愿" in result[0].markdown
    assert "<<<DIFY_PARENT>>>" not in result[0].markdown
    assert "<<<DIFY_CHILD>>>" not in result[0].markdown


def test_consultation_pdf_table_warning_still_enters_model_cleaning(tmp_path, monkeypatch):
    """A readable consultation PDF must not become review-only for table layout."""

    from types import SimpleNamespace

    from ai_management.cleaning import STRATEGY_REGISTRY
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "consultation.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(
        pdf_convert,
        "convert_consultation_pdf",
        lambda *_args, **_kwargs: SimpleNamespace(
            markdown="# 客户验证\n\n客户未确认报价，下一步继续验证。",
            source_checksum="a" * 64,
            document_id="case_aaaaaaaaaaaaaaaa",
            review_reasons=["table_review:表格结构无法可靠对齐，已保留线性单元格文本"],
            redactions=[],
            residual_pii=[],
        ),
    )

    result = STRATEGY_REGISTRY["personal_business_consultation"].clean(source, "任意名字.pdf")

    assert len(result) == 1
    assert result[0].disposition == "awaiting_model"
    assert result[0].audit["model_blocks"]
    assert result[0].warnings == []
    assert result[0].audit["pdf_conversion"]["review_reasons"] == [
        "table_review:表格结构无法可靠对齐，已保留线性单元格文本"
    ]


def test_same_content_with_different_filenames_has_same_cleaning_result(tmp_path):
    from ai_management.cleaning import STRATEGY_REGISTRY

    first = tmp_path / "promotion-title.md"
    second = tmp_path / "neutral-title.md"
    content = "# 客户访谈方法\n\n先验证客户问题，再设计产品。"
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")

    strategy = STRATEGY_REGISTRY["lead_magnet"]
    first_result = strategy.clean(first, first.name)[0]
    second_result = strategy.clean(second, second.name)[0]

    assert first_result.title == second_result.title
    assert first_result.markdown == second_result.markdown
