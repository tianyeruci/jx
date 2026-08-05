import scripts.pdf_markdown_refine as cli
from scripts.pdf_markdown_refine import (
    TextLine,
    _footer_key,
    _merge_table_rows,
    _merge_wrapped_heading_lines,
    _paragraph_markdown,
    _select_top_level_toc_entries,
    _toc_entries_from_text,
    _toc_entry_records_from_text,
    _toc_markdown,
)


def test_toc_entries_split_rows_without_dot_leader():
    text = (
        "目录1 第一项............................4 2 第二项............................11 "
        "3 第三项 15 4 第四项............................23"
    )
    entries = _toc_entries_from_text(text)
    assert [entry[0] for entry in entries] == ["1", "2", "3", "4"]
    assert entries[2][2] == "15"


def test_toc_keeps_original_top_level_title_and_drops_children_and_pages():
    text = (
        "1 作者甲《文章一》............................4 "
        "2 作者乙《文章二》............................11 "
        "1) 事情太多，忙不过来的。............................12 "
        "2) 有很多固定的流程性工作要做的。............................13 "
        "3 作者丙《文章三》............................23"
    )
    records = _toc_entry_records_from_text(text)
    selected = _select_top_level_toc_entries(records)
    assert [entry.title for entry in selected] == [
        "作者甲《文章一》",
        "作者乙《文章二》",
        "作者丙《文章三》",
    ]
    lines = _toc_markdown([TextLine("目录", 0, 0, 10, 10, ""), TextLine(text, 0, 20, 30, 10, "")])
    assert lines == [
        "# 目录",
        "- 1 作者甲《文章一》",
        "- 2 作者乙《文章二》",
        "- 3 作者丙《文章三》",
    ]


def test_cross_page_table_uses_first_cell_rule():
    merged = _merge_table_rows(
        [["表头", "内容"], ["甲", "第一段"]],
        [["", "第二段"], ["乙", "新行"]],
    )
    assert merged[-2] == ["甲", "第一段<br>第二段"]
    assert merged[-1] == ["乙", "新行"]


def test_footer_key_ignores_page_number_and_paragraph_gap_breaks():
    assert _footer_key("文档标题·第1页") == _footer_key("文档标题·第2页")
    lines = [
        TextLine("第一行", 0, 0, 10, 10, ""),
        TextLine("续行", 0, 13, 23, 10, ""),
        TextLine("新段", 0, 45, 55, 10, ""),
    ]
    assert _paragraph_markdown(lines, 10, preserve_source_paragraphs=True) == [
        "第一行续行",
        "新段",
    ]


def test_ocr_metadata_fields_stay_on_separate_lines():
    lines = [
        TextLine("会议时间：2026-06-03", 0, 0, 10, 10, ""),
        TextLine("咨询顾问：顾问甲", 0, 12, 22, 10, ""),
        TextLine("咨询对象：Sheldon", 0, 24, 34, 10, ""),
    ]
    assert _paragraph_markdown(lines, 10, preserve_source_paragraphs=True) == [
        "会议时间：2026-06-03",
        "咨询顾问：顾问甲",
        "咨询对象：Sheldon",
    ]


def test_cli_helpers_are_loaded_from_shared_package_core():
    """The diagnostic CLI must not carry a second layout implementation."""

    assert cli._paragraph_markdown is cli._core._paragraph_markdown
    assert cli._merge_table_rows is cli._core._merge_table_rows


def test_cli_can_clean_tables_after_pdf_conversion(monkeypatch, tmp_path):
    import pdfplumber

    class FakePDF:
        pages = [object()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    source = tmp_path / "case.pdf"
    source.write_bytes(b"fixture")
    output = tmp_path / "case.md"
    monkeypatch.setattr(pdfplumber, "open", lambda path: FakePDF())
    monkeypatch.setattr(cli, "_classify_pdf", lambda pdf: "pdfplumber")
    monkeypatch.setattr(
        cli,
        "_text_pdf_to_markdown",
        lambda pdf: (
            "| 项目 | 结果 |\n| --- | --- |\n| A | 通过 |\n",
            {"text_characters": 10},
        ),
    )

    result = cli.convert_file(source, output, 200, clean_tables=True)

    assert result["status"] == "succeeded"
    assert result["table_cleaning"]["tables_converted"] == 1
    assert output.read_text(encoding="utf-8") == "项目：A；结果：通过。\n"


def test_formal_text_pdf_adapter_uses_the_shared_layout_core(monkeypatch, tmp_path):
    from knowledge_cleaner import pdf_convert

    class FakePDF:
        pages = [object(), object()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakePdfPlumber:
        @staticmethod
        def open(path):
            return FakePDF()

    source = tmp_path / "formal.pdf"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(
        pdf_convert.importlib,
        "import_module",
        lambda name: FakePdfPlumber() if name == "pdfplumber" else __import__(name),
    )
    monkeypatch.setattr(
        pdf_convert._layout_core,
        "_text_pdf_to_markdown",
        lambda pdf: (
            "# 统一核心\n\n| 项目 | 结果 |\n| --- | --- |\n| A | 通过 |\n",
            {"table_count": 1, "warnings": []},
        ),
    )

    pages = pdf_convert._extract_pdfplumber_pages(source)

    assert len(pages) == 1
    assert pages[0].backend == "pdfplumber_shared_core"
    assert pages[0].table_count == 1
    assert "| 项目 | 结果 |" in pages[0].markdown


def test_toc_indent_can_drop_same_numbered_children():
    lines = [
        TextLine("1 主标题............................1", 40, 0, 10, 10, ""),
        TextLine("1 子标题............................2", 90, 12, 22, 10, ""),
        TextLine("2 第二个主标题............................3", 40, 24, 34, 10, ""),
    ]
    assert _toc_markdown(lines) == [
        "- 1 主标题",
        "- 2 第二个主标题",
    ]


def test_bold_body_is_not_promoted_to_heading():
    lines = [
        TextLine("又或者我在正文中搜索关键词。", 0, 0, 12, 12, "MicrosoftYaHei-Bold"),
        TextLine("这仍然是普通段落。", 0, 24, 36, 12, "MicrosoftYaHei"),
    ]
    assert _paragraph_markdown(lines, 12) == [
        "又或者我在正文中搜索关键词。这仍然是普通段落。",
    ]


def test_wrapped_numbered_heading_does_not_absorb_body_after_large_gap():
    lines = [
        TextLine("3、框架解析", 0, 0, 12, 12, "MicrosoftYaHei-Bold"),
        TextLine("通过刚刚的案例，相信大家已经知道。", 0, 62, 74, 12, "MicrosoftYaHei"),
    ]
    merged = _merge_wrapped_heading_lines(lines, 12)
    assert [line.text for line in merged] == [line.text for line in lines]


def test_bold_body_at_body_size_is_not_a_heading():
    lines = [TextLine("普通加粗句。", 0, 0, 10, 10, "Noto-Bold")]
    assert _paragraph_markdown(lines, 10) == ["普通加粗句。"]


def test_literal_source_hash_is_not_emitted_as_markdown_heading():
    lines = [TextLine("# 普通正文", 0, 0, 10, 10, "Noto-Bold")]
    assert _paragraph_markdown(lines, 10) == ["普通正文"]


def test_same_size_numbered_body_is_not_a_heading_without_toc_match():
    lines = [TextLine("1、这是正文里的编号句。", 0, 0, 10, 10, "Noto-Bold")]
    assert _paragraph_markdown(lines, 10) == ["1、 这是正文里的编号句。"]


def test_toc_forces_body_titles_below_unique_h1():
    lines = [
        TextLine("3 主标题", 0, 0, 20, 20, "Noto-Bold"),
        TextLine("正文内容。", 0, 28, 38, 10, "Noto-Bold"),
    ]
    assert _paragraph_markdown(
        lines,
        10,
        toc_heading_levels={"3主标题": 1, "主标题": 1},
        heading_floor=2,
    ) == ["## 3 主标题", "正文内容。"]


def test_wrapped_heading_is_merged_before_heading_classification():
    lines = [
        TextLine("3 做自媒体如何善用‘搜索’？|从第", 0, 0, 22, 22, "Noto-Bold"),
        TextLine("一稿到第四稿》", 30, 27, 49, 22, "Noto-Bold"),
    ]
    result = _paragraph_markdown(
        lines,
        12,
        heading_levels={22.0: 1},
        toc_heading_levels={"3做自媒体如何善用搜索从第一稿到第四稿": 1},
        heading_floor=2,
    )
    assert result == ["## 3 做自媒体如何善用‘搜索’？|从第一稿到第四稿》"]


def test_wrapped_body_lines_stay_in_one_paragraph():
    lines = [
        TextLine("这是一个未完成的句子", 0, 0, 10, 10, "Noto-Regular"),
        TextLine("继续内容。", 0, 16, 26, 10, "Noto-Regular"),
        TextLine("这是新段。", 0, 45, 55, 10, "Noto-Regular"),
    ]
    assert _paragraph_markdown(lines, 10) == ["这是一个未完成的句子继续内容。", "这是新段。"]
