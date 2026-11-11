from pathlib import Path
from types import SimpleNamespace

from ai_management.cleaning import (
    PersonalBusinessConsultationStrategy,
    dify_document_name,
    finalize_knowledge_markdown,
    user_visible_warnings,
)
from knowledge_cleaner.markdown_table_cleaner import clean_markdown_tables


def test_unreliable_markdown_table_is_demoted_to_linear_text():
    result = clean_markdown_tables(
        """前文

| 项目 | 项目 |
| --- | --- |
| A | 通过 |

后文
"""
    )

    assert "| 项目 | 项目 |" not in result.markdown
    assert "A；通过。" in result.markdown
    assert "前文" in result.markdown and "后文" in result.markdown
    assert result.tables_demoted == 1
    assert result.issues[0].action == "demoted_plain_text"


def test_final_markdown_invariant_removes_tables_but_keeps_headings_and_lists():
    audit: dict[str, object] = {}
    result = finalize_knowledge_markdown(
        """# 标题

- 保留列表

| 项目 | 结果 |
| --- | --- |
| A | 通过 |
""",
        audit=audit,
    )

    assert "# 标题" in result
    assert "- 保留列表" in result
    assert "| 项目 | 结果 |" not in result
    assert "项目：A；结果：通过。" in result
    assert "final_markdown_table_cleaning" in audit


def test_dify_names_are_markdown_and_split_names_are_sequenced():
    assert dify_document_name("访谈记录.docx", "a" * 64, 1) == "访谈记录.md"
    assert dify_document_name("另一个名字.pdf", "a" * 64, 12, split=True) == "另一个名字__012.md"


def test_table_review_is_internal_only_for_consultation_documents(monkeypatch, tmp_path):
    source = tmp_path / "case.pdf"
    source.write_bytes(b"pdf fixture")
    conversion = SimpleNamespace(
        markdown="# 案例\n\n正文",
        review_reasons=["table_review:表格结构无法可靠对齐:table=1"],
        redactions=[],
        residual_pii=[],
        source_checksum="a" * 64,
        document_id="case_aaaaaaaaaaaaaaaa",
        to_dict=lambda include_page_markdown=False: {
            "source_checksum": "a" * 64,
            "document_id": "case_aaaaaaaaaaaaaaaa",
            "review_reasons": ["table_review:表格结构无法可靠对齐:table=1"],
        },
    )
    monkeypatch.setattr(
        "knowledge_cleaner.pdf_convert.convert_consultation_pdf",
        lambda *args, **kwargs: conversion,
    )

    document = PersonalBusinessConsultationStrategy().clean(source, source.name)[0]

    assert document.warnings == []
    assert document.disposition == "awaiting_model"
    assert (
        "table_review:表格结构无法可靠对齐:table=1"
        in document.audit["pdf_conversion"]["review_reasons"]
    )


def test_user_visible_warning_filter_keeps_non_table_warnings():
    assert user_visible_warnings(["table_review:表格结构无法可靠对齐", "目录需要复核"]) == [
        "目录需要复核"
    ]


def test_raw_worker_path_does_not_disable_table_cleaning():
    source = Path("ai_management/workers/tasks/cleaning.py").read_text(encoding="utf-8")
    raw_branch = source[
        source.index('if batch.upload_mode == "raw":') : source.index(
            "else:\n                assert strategy is not None",
            source.index('if batch.upload_mode == "raw":'),
        )
    ]
    assert "clean_tables=True" in raw_branch
    assert "clean_tables=False" not in raw_branch


def test_remote_metadata_exposes_normalized_format_but_not_private_source_fields():
    from ai_management.workers.tasks.cleaning import _project_dify_metadata

    metadata = {
        "document_type": "standard_document",
        "content_format": "markdown",
        "source_extension": ".pdf",
        "source_filename": "secret.pdf",
        "cleaning_strategy": "standard_document",
    }

    remote = _project_dify_metadata(
        metadata,
        strategy=SimpleNamespace(dify_metadata_fields={"document_type"}),
    )

    assert remote == {
        "document_type": "standard_document",
        "content_format": "markdown",
        "cleaning_strategy": "standard_document",
    }
