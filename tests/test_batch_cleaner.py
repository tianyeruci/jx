import json

from knowledge_cleaner.batch_pipeline import BatchCleaningPipeline, BatchOptions
from knowledge_cleaner.chunking import prepare_chunks, render_dify_text
from knowledge_cleaner.classifier import parse_classification
from knowledge_cleaner.pdf_convert import scan_residual_pii, table_to_markdown
from knowledge_cleaner.semantics import classify_text
from knowledge_cleaner.sources import parse_markdown_posts, parse_wechat_public_account


def test_wechat_parser_restores_literal_newlines_and_strict_metadata(tmp_path):
    source = tmp_path / "account.txt"
    text = (
        "【文章1】定位复盘\\n我先访谈客户，再调整产品定位，最终成交率提高。\n"
        "2026-01-02 03:04:05  测试账号\n"
        "定位复盘原创作者测试账号2026-01-02 03:04:05北京不应覆盖正文\n"
        "阅读原文\n"
    )
    source.write_bytes(text.encode("gb18030"))
    units = parse_wechat_public_account(source)
    assert len(units) == 1
    assert units[0].title == "定位复盘"
    assert "成交率提高" in units[0].body
    assert "阅读原文" not in units[0].body
    assert units[0].published_at == "2026-01-02 03:04:05"


def test_markdown_course_uses_lesson_h2_not_part_h1(tmp_path):
    source = tmp_path / "course.md"
    source.write_text(
        "# 课程合集\n# 第一部分\n## 第1讲：定位\n正文一。\n## 第2讲：产品\n正文二。\n",
        encoding="utf-8",
    )
    units = parse_markdown_posts(source)
    assert [unit.sequence for unit in units] == ["1", "2"]
    assert all(unit.source_type == "markdown_course" for unit in units)


def test_chunker_never_exposes_frontmatter_and_repeats_context():
    body = "。".join(["定位要结合客户问题和产品价值"] * 100) + "。"
    chunks = prepare_chunks(body, title="定位方法", collection="商业课", topics=("定位",))
    assert len(chunks) > 1
    rendered = render_dify_text(chunks)
    assert rendered.count("【上下文】商业课｜定位方法") == len(chunks)
    assert "---" not in rendered
    assert all(chunk.char_count <= 1200 for chunk in chunks)


def test_semantic_rules_keep_transferable_experience_and_exclude_securities():
    useful = classify_text("定位复盘", "我访谈了二十位客户，调整产品以后成交率提高，并总结了三个步骤。")
    finance = classify_text("今日策略", "建议买入某个股票代码，设置仓位、止盈和止损。")
    assert useful.disposition == "keep"
    assert finance.disposition == "no_index"


def test_classifier_rejects_hallucinated_evidence():
    payload = {
        "domain_class": "business_core",
        "content_type": "article",
        "knowledge_nature": "method",
        "topics": ["定位"],
        "time_sensitive": False,
        "quality_class": "useful",
        "evidence_spans": ["原文不存在"],
        "confidence": 0.95,
    }
    try:
        parse_classification(payload, "正文只讨论产品。")
    except ValueError:
        pass
    else:
        raise AssertionError("hallucinated evidence must be rejected")


def test_pdf_table_and_pii_gate_are_conservative():
    markdown, reliable = table_to_markdown([["问题", "本质"], ["定位", "缺少客户验证"]])
    assert "| 问题 | 本质 |" in markdown
    assert reliable is True
    findings = scan_residual_pii("联系电话13800138000，邮箱a@example.com")
    assert {item.category for item in findings} >= {"phone", "email"}


def test_batch_pipeline_builds_fresh_snapshot_and_exact_deduplicates(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    body = "# 合集\n# 定位方法\n先访谈客户，再验证产品和成交路径。\n"
    (source / "合集.md").write_text(body, encoding="utf-8")
    (source / "合集 (1).md").write_text(body, encoding="utf-8")
    output = tmp_path / "output"
    result = BatchCleaningPipeline(BatchOptions(source, output)).run()
    records = [json.loads(line) for line in (output / "manifest.jsonl").read_text("utf-8").splitlines()]
    assert result.parsed_documents == 2
    assert sum(item["disposition"] == "duplicate" for item in records) == 1
    assert (output / "quality_report.json").is_file()
    assert (output / "review.csv").is_file()
