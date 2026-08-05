from knowledge_cleaner.markdown_table_cleaner import clean_markdown_tables


def test_reliable_gfm_table_becomes_contextual_sentences():
    result = clean_markdown_tables(
        """# 目标

| 项目 | 结果 |
| --- | --- |
| A | 通过 |
| B | 待复核 |
"""
    )

    assert result.markdown == (
        "# 目标\n\n"
        "目标；项目：A；结果：通过。\n"
        "目标；项目：B；结果：待复核。\n"
    )
    assert result.tables_converted == 1
    assert result.issues == ()


def test_bad_rows_are_skipped_and_aggregated_into_one_issue():
    result = clean_markdown_tables(
        """| 项目 | 结果 |
| --- | --- |
| A | 通过 |
| B | 失败 | 多余单元格 |
| C | 继续 |
| D | 失败 | 额外 | 字段 |
"""
    )

    assert "项目：A；结果：通过。" in result.markdown
    assert "项目：C；结果：继续。" in result.markdown
    assert "项目：B" not in result.markdown
    assert "项目：D" not in result.markdown
    assert result.rows_skipped == 2
    assert len(result.issues) == 1
    assert result.issues[0].action == "rows_skipped"
    assert result.issues[0].rows_skipped == 2


def test_invalid_table_is_demoted_to_plain_text_and_has_one_issue():
    source = """| 项目 | 项目 |
| --- | --- |
| A | 通过 |

后续正文。
"""

    result = clean_markdown_tables(source)

    assert "| 项目 | 项目 |" not in result.markdown
    assert "A；通过。" in result.markdown
    assert "后续正文。" in result.markdown
    assert result.tables_demoted == 1
    assert len(result.issues) == 1
    assert result.issues[0].action == "demoted_plain_text"
    assert result.issues[0].reason == "duplicate_header"


def test_fenced_pipe_text_is_untouched():
    source = """```markdown
| 项目 | 结果 |
| --- | --- |
| A | 通过 |
```
"""

    result = clean_markdown_tables(source)

    assert result.markdown == source
    assert result.tables_seen == 0
    assert result.issues == ()


def test_simple_html_table_is_converted():
    result = clean_markdown_tables(
        """<table>
<tr><th>项目</th><th>结果</th></tr>
<tr><td>A</td><td>通过</td></tr>
</table>
"""
    )

    assert result.markdown == "项目：A；结果：通过。\n"
    assert result.tables_converted == 1
    assert result.issues == ()


def test_complex_html_table_is_demoted_without_losing_text():
    source = """<table>
<tr><th>项目</th><th>结果</th></tr>
<tr><td rowspan="2">A</td><td>通过</td></tr>
<tr><td>继续</td></tr>
</table>
"""

    result = clean_markdown_tables(source)

    assert "项目" in result.markdown
    assert "通过" in result.markdown
    assert "继续" in result.markdown
    assert result.tables_demoted == 1
    assert len(result.issues) == 1
    assert result.issues[0].action == "demoted_plain_text"
