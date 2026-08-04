from scripts.pdf_markdown_refine import (
    TextLine,
    _footer_key,
    _merge_table_rows,
    _paragraph_markdown,
    _toc_entries_from_text,
)


def test_toc_entries_split_rows_without_dot_leader():
    text = (
        "目录1 第一项............................4 2 第二项............................11 "
        "3 第三项 15 4 第四项............................23"
    )
    entries = _toc_entries_from_text(text)
    assert [entry[0] for entry in entries] == ["1", "2", "3", "4"]
    assert entries[2][2] == "15"


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
