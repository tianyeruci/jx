from types import SimpleNamespace

from ai_management.cleaning import PersonalBusinessConsultationStrategy, ZsxqPostStrategy


def test_markdown_source_strategy_cleans_tables_before_source_split(tmp_path):
    source = tmp_path / "posts.md"
    source.write_text(
        """# 合集

# 案例一

| 项目 | 结果 |
| --- | --- |
| A | 通过 |
""",
        encoding="utf-8",
    )

    documents = ZsxqPostStrategy().clean(source, "posts.md")

    assert len(documents) == 1
    assert "| 项目 | 结果 |" not in documents[0].markdown
    assert "项目：A；结果：通过。" in documents[0].markdown
    assert documents[0].audit["markdown_table_cleaning"]["tables_converted"] == 1


def test_consultation_pdf_result_cleans_markdown_after_conversion(monkeypatch, tmp_path):
    source = tmp_path / "case.pdf"
    source.write_bytes(b"pdf fixture")
    conversion = SimpleNamespace(
        markdown="# 案例\n\n| 项目 | 结果 |\n| --- | --- |\n| A | 通过 |\n",
        review_reasons=["table_review:表格结构无法可靠对齐，已保留线性单元格文本:table=1"],
        redactions=[],
        residual_pii=[],
        source_checksum="a" * 64,
        document_id="case_aaaaaaaaaaaaaaaa",
        to_dict=lambda include_page_markdown=False: {
            "source_checksum": "a" * 64,
            "document_id": "case_aaaaaaaaaaaaaaaa",
            "review_reasons": [
                "table_review:表格结构无法可靠对齐，已保留线性单元格文本:table=1"
            ],
        },
    )

    monkeypatch.setattr(
        "knowledge_cleaner.pdf_convert.convert_consultation_pdf",
        lambda *args, **kwargs: conversion,
    )

    documents = PersonalBusinessConsultationStrategy().clean(source, "case.pdf")

    assert len(documents) == 1
    assert "| 项目 | 结果 |" not in documents[0].markdown
    assert "项目：A；结果：通过。" in documents[0].markdown
    assert documents[0].disposition == "awaiting_model"
    audit = documents[0].audit["pdf_conversion"]
    assert audit["markdown_table_cleaning"]["tables_converted"] == 1

