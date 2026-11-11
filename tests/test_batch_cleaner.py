import json

import httpx
import pytest

from knowledge_cleaner.batch_pipeline import BatchCleaningPipeline, BatchOptions
from knowledge_cleaner.chunking import (
    DIFY_CHILD_SEPARATOR,
    DIFY_PARENT_SEPARATOR,
    prepare_chunks,
    render_dify_text,
    render_hierarchical_dify_text,
)
from knowledge_cleaner.classifier import parse_classification
from knowledge_cleaner.dify import DifyKnowledgeClient
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


def test_wechat_parser_prefers_longer_near_duplicate_body(tmp_path):
    source = tmp_path / "account.txt"
    shared = "先访谈客户，再调整产品定位，最后复盘成交结果。" * 8
    text = (
        f"【文章1】定位复盘\\n{shared}\n"
        "2026-01-02 03:04:05  测试账号\n"
        f"定位复盘原创作者测试账号2026-01-02 03:04:05北京{shared}补充适用条件。\n"
    )
    source.write_bytes(text.encode("gb18030"))
    unit = parse_wechat_public_account(source)[0]
    assert unit.body.endswith("补充适用条件。")
    assert unit.metadata["body_origin"] == "deduplicated_longer_after"


def test_wechat_parser_splits_multiple_articles_in_one_source_file(tmp_path):
    source = tmp_path / "account.txt"
    text = (
        "【文章1】第一篇\\n先访谈客户，再验证定位。\n"
        "2026-01-02 03:04:05  测试账号\n"
        "第一篇正文。\n"
        "【文章2】第二篇\\n第二篇讨论定价和交付。\n"
        "2026-01-03 03:04:05  测试账号\n"
        "第二篇正文。\n"
    )
    source.write_bytes(text.encode("gb18030"))

    units = parse_wechat_public_account(source)

    assert [unit.title for unit in units] == ["第一篇", "第二篇"]
    assert [unit.sequence for unit in units] == ["1", "2"]
    assert all(unit.body for unit in units)


def test_markdown_post_parser_splits_multiple_posts_in_one_source_file(tmp_path):
    source = tmp_path / "posts.md"
    source.write_text(
        "# 帖子合集\n\n"
        "# 第一篇\n先访谈客户，再验证定位。\n\n"
        "# 第二篇\n定价要和交付边界一起设计。\n",
        encoding="utf-8",
    )

    units = parse_markdown_posts(source)

    assert [unit.title for unit in units] == ["第一篇", "第二篇"]
    assert [unit.sequence for unit in units] == ["1", "2"]
    assert [unit.source_type for unit in units] == ["markdown_post", "markdown_post"]


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


def test_hierarchical_renderer_uses_explicit_separators_without_overlap():
    body = "\n\n".join(f"第{i}段讨论客户、产品与成交方法。" for i in range(1, 10))
    chunks = prepare_chunks(
        body,
        title="定位方法",
        topics=("产品与客户",),
        target=55,
        hard_max=80,
        overlap=0,
    )
    rendered = render_hierarchical_dify_text(chunks)
    assert DIFY_PARENT_SEPARATOR in rendered
    assert DIFY_CHILD_SEPARATOR not in {
        rendered.strip().splitlines()[0],
        rendered.strip().splitlines()[-1],
    }
    for index in range(1, 10):
        assert rendered.count(f"第{index}段讨论") == 1


def test_dify_hierarchical_contract_and_child_verification():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/document/create-by-text"):
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"document": {"id": "doc-1"}, "batch": "batch-1"},
            )
        if path.endswith("/segments/segment-1/child_chunks"):
            return httpx.Response(200, json={"data": [{"id": "child-1"}], "total": 1})
        if path.endswith("/segments"):
            return httpx.Response(200, json={"data": [{"id": "segment-1"}], "total": 1})
        raise AssertionError(path)

    client = DifyKnowledgeClient(
        "https://dify.example/v1",
        "secret",
        "dataset-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.create_document(
            "doc.md",
            "正文",
            parent_separator=DIFY_PARENT_SEPARATOR,
            child_separator=DIFY_CHILD_SEPARATOR,
            child_max_tokens=350,
        )
        assert client.ensure_document_has_child_chunks("doc-1") == 1
    finally:
        client.close()
    rules = captured["payload"]["process_rule"]["rules"]
    assert captured["payload"]["doc_form"] == "hierarchical_model"
    assert rules["parent_mode"] == "paragraph"
    assert rules["pre_processing_rules"] == [
        {"id": "remove_extra_spaces", "enabled": True},
        {"id": "remove_urls_emails", "enabled": True},
    ]
    assert rules["segmentation"]["separator"] == DIFY_PARENT_SEPARATOR
    assert rules["subchunk_segmentation"] == {
        "separator": DIFY_CHILD_SEPARATOR,
        "max_tokens": 350,
    }


def test_dify_wait_for_indexing_prefers_created_document_over_transient_batch():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/documents/document-uuid"):
            return httpx.Response(200, json={"id": "document-uuid", "indexing_status": "completed"})
        if path.endswith("/indexing-status"):
            raise AssertionError("document id is available; batch endpoint must not be used")
        raise AssertionError(path)

    client = DifyKnowledgeClient(
        "https://dify.example/v1",
        "secret",
        "dataset-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.wait_for_indexing(
            "20260731135936553731",
            document_id="document-uuid",
            timeout_seconds=1,
            interval=0,
        )
    finally:
        client.close()
    assert calls == ["/v1/datasets/dataset-1/documents/document-uuid"]


def test_dify_wait_for_indexing_falls_back_to_batch_on_document_lookup_404():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/documents/document-uuid"):
            return httpx.Response(404, json={"message": "not found"})
        if path.endswith("/documents/legacy-batch/indexing-status"):
            return httpx.Response(
                200,
                json={"data": [{"id": "document-uuid", "indexing_status": "completed"}]},
            )
        raise AssertionError(path)

    client = DifyKnowledgeClient(
        "https://dify.example/v1",
        "secret",
        "dataset-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.wait_for_indexing(
            "legacy-batch",
            document_id="document-uuid",
            timeout_seconds=1,
            interval=0,
        )
    finally:
        client.close()
    assert calls == [
        "/v1/datasets/dataset-1/documents/document-uuid",
        "/v1/datasets/dataset-1/documents/legacy-batch/indexing-status",
    ]


def test_dify_wait_for_indexing_keeps_terminal_error_semantics():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/documents/document-uuid")
        return httpx.Response(
            200,
            json={
                "id": "document-uuid",
                "indexing_status": "error",
                "error": "controlled indexing error",
            },
        )

    client = DifyKnowledgeClient(
        "https://dify.example/v1",
        "secret",
        "dataset-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(RuntimeError, match="Dify索引失败"):
            client.wait_for_indexing(
                "legacy-batch",
                document_id="document-uuid",
                timeout_seconds=1,
                interval=0,
            )
    finally:
        client.close()


def test_semantic_rules_keep_transferable_experience_and_exclude_securities():
    useful = classify_text(
        "定位复盘", "我访谈了二十位客户，调整产品以后成交率提高，并总结了三个步骤。"
    )
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
    records = [
        json.loads(line) for line in (output / "manifest.jsonl").read_text("utf-8").splitlines()
    ]
    assert result.parsed_documents == 2
    assert sum(item["disposition"] == "duplicate" for item in records) == 1
    assert (output / "quality_report.json").is_file()
    assert (output / "review.csv").is_file()
