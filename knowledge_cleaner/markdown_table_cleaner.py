"""Safe Markdown table normalization for knowledge cleaning.

The cleaner operates on Markdown text only.  It deliberately has no file or
logging side effects so every cleaning entry point can use the same parser and
the caller can aggregate one redacted issue per table.
"""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Iterable

_FENCE_RE = re.compile(r"^\s*(?P<marker>`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(?P<title>.+?)\s*$")
_TABLE_TAG_RE = re.compile(r"<\s*table\b", re.IGNORECASE)
_TABLE_END_RE = re.compile(r"</\s*table\s*>", re.IGNORECASE)
_SEPARATOR_CELL_RE = re.compile(r"^:?-{1,}:?$")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_INLINE_MARKER_RE = re.compile(r"(?:\*\*|__|~~|`)")
_SECTION_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百千万零〇0-9]+\s*[章节部分篇回]\s*"
    r"|[一二三四五六七八九十百千万零〇]+\s*[、.．)]\s*"
    r"|[0-9]+(?:\.[0-9]+)*\s*[、.．)]\s*"
    r")"
)


class MarkdownTableError(ValueError):
    """Expected table parsing failure with a stable reason code."""


@dataclass(frozen=True, slots=True)
class TableIssue:
    """One aggregated issue for one table; never contains source text."""

    table_index: int
    start_line: int
    end_line: int
    action: str
    reason: str
    rows_skipped: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarkdownTableCleanResult:
    markdown: str
    tables_seen: int = 0
    tables_converted: int = 0
    tables_demoted: int = 0
    tables_skipped: int = 0
    rows_converted: int = 0
    rows_skipped: int = 0
    issues: tuple[TableIssue, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "tables_seen": self.tables_seen,
            "tables_converted": self.tables_converted,
            "tables_demoted": self.tables_demoted,
            "tables_skipped": self.tables_skipped,
            "rows_converted": self.rows_converted,
            "rows_skipped": self.rows_skipped,
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _is_fence_marker(line: str) -> tuple[str, int] | None:
    match = _FENCE_RE.match(line)
    if not match:
        return None
    marker = match.group("marker")
    return marker[0], len(marker)


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped:
        raise MarkdownTableError("empty_row")

    cells: list[str] = []
    buffer: list[str] = []
    saw_pipe = False
    in_code = False
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped) and stripped[index + 1] == "|":
            buffer.append("|")
            index += 2
            continue
        if char == "`":
            in_code = not in_code
            buffer.append(char)
            index += 1
            continue
        if char == "|" and not in_code:
            saw_pipe = True
            cells.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1

    if in_code:
        raise MarkdownTableError("unclosed_code_span")
    cells.append("".join(buffer).strip())
    if stripped.startswith("|"):
        cells = cells[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        cells = cells[:-1]
    if not saw_pipe or len(cells) < 2:
        raise MarkdownTableError("not_a_table_row")
    return cells


def _normalized_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\\|", "|")
    text = _BR_RE.sub("、", text)
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _header_label(value: str) -> str:
    return _INLINE_MARKER_RE.sub("", _normalized_cell(value)).strip()


def _header_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _is_separator_row(cells: Iterable[str]) -> bool:
    values = [cell.strip() for cell in cells]
    return len(values) >= 2 and all(_SEPARATOR_CELL_RE.fullmatch(cell) for cell in values)


def _looks_like_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    try:
        _split_row(lines[index])
        separator = _split_row(lines[index + 1])
    except MarkdownTableError:
        return False
    return _is_separator_row(separator)


def _looks_like_pipe_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    in_code = False
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped) and stripped[index + 1] == "|":
            index += 2
            continue
        if char == "`":
            in_code = not in_code
        elif char == "|" and not in_code:
            return True
        index += 1
    return False


def _heading_context(line: str) -> str | None:
    match = _HEADING_RE.match(line)
    if not match:
        return None
    title = _SECTION_PREFIX_RE.sub("", match.group("title")).strip()
    title = _INLINE_MARKER_RE.sub("", title).strip()
    return title or None


def _ensure_terminal_period(text: str) -> str:
    return text if text.endswith(("。", "！", "？", ".", "!", "?")) else text + "。"


def _render_row(context: str | None, headers: list[str], values: list[str]) -> str:
    fields = [f"{header}：{value}" for header, value in zip(headers, values) if value]
    if not fields:
        return ""
    prefix = [context] if context else []
    return _ensure_terminal_period("；".join([*prefix, *fields]))


def _table_end(lines: list[str], start: int) -> int:
    index = start + 2
    while index < len(lines):
        if not lines[index].strip() or _is_fence_marker(lines[index]):
            break
        if not _looks_like_pipe_row(lines[index]):
            break
        index += 1
    return index


def _linearize_pipe_rows(rows: Iterable[str]) -> list[str]:
    """Keep cell text when a pipe-table cannot be aligned safely."""

    result: list[str] = []
    for line in rows:
        try:
            cells = [_normalized_cell(cell) for cell in _split_row(line)]
        except MarkdownTableError:
            cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if cells and _is_separator_row(cells):
            continue
        text = "；".join(cell for cell in cells if cell)
        if text:
            result.append(text + ("。" if not text.endswith(("。", "！", "？", ".", "!", "?")) else ""))
    return result


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.complex_layout = False
        self.invalid = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value for key, value in attrs}
        if any(
            key in attributes and str(attributes[key] or "1").strip() not in {"", "1"}
            for key in ("rowspan", "colspan")
        ):
            self.complex_layout = True
        if tag == "tr":
            if self._row is not None:
                self.invalid = True
            self._row = []
        elif tag in {"td", "th"}:
            if self._row is None or self._cell is not None:
                self.invalid = True
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            if self._row is None or self._cell is None:
                self.invalid = True
                return
            self._row.append(html.unescape("".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr":
            if self._row is None or self._cell is not None:
                self.invalid = True
                return
            self.rows.append(self._row)
            self._row = None


def _html_text_fallback(source: str) -> str:
    value = re.sub(r"<\s*br\s*/?>", "\n", source, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _replace_html_table(source: str) -> tuple[str, str | None, int, int]:
    parser = _HTMLTableParser()
    try:
        parser.feed(source)
        parser.close()
    except Exception:
        parser.invalid = True
    if parser.complex_layout or parser.invalid or not parser.rows:
        return _html_text_fallback(source), "html_structure_not_reliably_aligned", 0, 0

    rows = [[_normalized_cell(cell) for cell in row] for row in parser.rows]
    width = max((len(row) for row in rows), default=0)
    if len(rows) < 2 or width < 2 or any(len(row) != width for row in rows):
        return _html_text_fallback(source), "html_structure_not_reliably_aligned", 0, 0
    rows = [row + [""] * (width - len(row)) for row in rows]
    headers = [_header_label(cell) for cell in rows[0]]
    if any(not value for value in headers):
        return _html_text_fallback(source), "empty_header", 0, 0
    keys = [_header_key(value) for value in headers]
    if len(set(keys)) != len(keys):
        return _html_text_fallback(source), "duplicate_header", 0, 0

    rendered = [_render_row(None, headers, row) for row in rows[1:]]
    rendered = [row for row in rendered if row]
    if not rendered:
        return "", "no_valid_data_rows", 0, len(rows) - 1
    return "\n".join(rendered), None, len(rendered), 0


def clean_markdown_tables(markdown: str) -> MarkdownTableCleanResult:
    """Convert reliable Markdown tables and preserve uncertain text safely."""

    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    issues: list[TableIssue] = []
    tables_seen = tables_converted = tables_demoted = tables_skipped = 0
    rows_converted = rows_skipped = 0
    context: str | None = None
    content_since_heading = False
    fence: tuple[str, int] | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        marker = _is_fence_marker(line)
        if fence is not None:
            output.append(line)
            if marker and marker[0] == fence[0] and marker[1] >= fence[1]:
                fence = None
            index += 1
            continue
        if marker is not None:
            fence = marker
            output.append(line)
            index += 1
            continue

        heading = _heading_context(line)
        if heading is not None:
            context = heading
            content_since_heading = False
            output.append(line)
            index += 1
            continue

        if _looks_like_table_start(lines, index):
            table_index = tables_seen + 1
            tables_seen += 1
            end = _table_end(lines, index)
            start_line = index + 1
            table_context = context if not content_since_heading else None
            table_rows_skipped = 0
            try:
                header_cells = _split_row(lines[index])
                separator_cells = _split_row(lines[index + 1])
                if len(header_cells) < 2 or len(header_cells) != len(separator_cells):
                    raise MarkdownTableError("header_separator_width")
                headers = [_header_label(cell) for cell in header_cells]
                if any(not header for header in headers):
                    raise MarkdownTableError("empty_header")
                keys = [_header_key(header) for header in headers]
                if len(set(keys)) != len(keys):
                    raise MarkdownTableError("duplicate_header")

                rendered_rows: list[str] = []
                for row_index in range(index + 2, end):
                    try:
                        values = [_normalized_cell(cell) for cell in _split_row(lines[row_index])]
                    except MarkdownTableError:
                        table_rows_skipped += 1
                        continue
                    if len(values) != len(headers):
                        table_rows_skipped += 1
                        continue
                    rendered = _render_row(table_context, headers, values)
                    if not rendered:
                        table_rows_skipped += 1
                        continue
                    rendered_rows.append(rendered)

                if not rendered_rows:
                    raise MarkdownTableError("no_valid_data_rows")
            except MarkdownTableError as exc:
                tables_demoted += 1
                rows_skipped += table_rows_skipped
                # A structurally invalid table cannot be safely aligned, but
                # dropping it would lose source facts.  Keep cell text in
                # linear form and record one table-level issue.
                output.extend(_linearize_pipe_rows(lines[index:end]))
                issues.append(
                    TableIssue(
                        table_index=table_index,
                        start_line=start_line,
                        end_line=end,
                        action="demoted_plain_text",
                        reason=str(exc),
                        rows_skipped=table_rows_skipped,
                    )
                )
                index = end
                continue

            output.extend(rendered_rows)
            tables_converted += 1
            rows_converted += len(rendered_rows)
            if table_rows_skipped:
                rows_skipped += table_rows_skipped
                issues.append(
                    TableIssue(
                        table_index=table_index,
                        start_line=start_line,
                        end_line=end,
                        action="rows_skipped",
                        reason="row_parse_error_or_column_count",
                        rows_skipped=table_rows_skipped,
                    )
                )
            index = end
            continue

        if _looks_like_pipe_row(line) and index + 1 < len(lines) and _looks_like_pipe_row(lines[index + 1]):
            table_index = tables_seen + 1
            tables_seen += 1
            end = index + 2
            while end < len(lines) and _looks_like_pipe_row(lines[end]):
                end += 1
            output.extend(_linearize_pipe_rows(lines[index:end]))
            tables_demoted += 1
            issues.append(
                TableIssue(
                    table_index=table_index,
                    start_line=index + 1,
                    end_line=end,
                    action="demoted_plain_text",
                    reason="table_header_or_separator_unreliable",
                )
            )
            index = end
            continue

        if _TABLE_TAG_RE.search(line):
            table_index = tables_seen + 1
            end = index + 1
            while end < len(lines) and not _TABLE_END_RE.search("\n".join(lines[index:end])):
                end += 1
            if end < len(lines):
                end += 1
            source = "\n".join(lines[index:end])
            tables_seen += 1
            replacement, reason, converted, html_rows_skipped = _replace_html_table(source)
            if reason is None:
                tables_converted += 1
                rows_converted += converted
                output.extend(replacement.splitlines())
            else:
                tables_demoted += 1
                rows_skipped += html_rows_skipped
                output.extend(replacement.splitlines())
                issues.append(
                    TableIssue(
                        table_index=table_index,
                        start_line=index + 1,
                        end_line=end,
                        action="demoted_plain_text",
                        reason=reason,
                        rows_skipped=html_rows_skipped,
                    )
                )
            index = end
            continue

        output.append(line)
        if line.strip() and context is not None:
            content_since_heading = True
        index += 1

    rendered_text = re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()
    if rendered_text:
        rendered_text += "\n"
    return MarkdownTableCleanResult(
        markdown=rendered_text,
        tables_seen=tables_seen,
        tables_converted=tables_converted,
        tables_demoted=tables_demoted,
        tables_skipped=tables_skipped,
        rows_converted=rows_converted,
        rows_skipped=rows_skipped,
        issues=tuple(issues),
    )


__all__ = ["MarkdownTableCleanResult", "TableIssue", "clean_markdown_tables"]
