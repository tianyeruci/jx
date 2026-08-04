#!/usr/bin/env python3
"""Convert local text/image PDFs to cleaner Markdown on the jx OCR image.

The script intentionally stays local and deterministic:
* the first two pages decide whether the document uses pdfplumber or RapidOCR;
* text PDFs keep paragraphs, inferred headings, lists and pdfplumber tables;
* image PDFs use RapidOCR text boxes and preserve readable line order;
* no page markers, images, formulas, decorations or sample正文 are written to logs.

Run this script inside the existing jx OCR image.  It does not start a service
and does not change the production cleaning pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PAGE_MARK_RE = re.compile(
    r"^(?:第\s*\d+\s*页(?:\s*/\s*\d+)?|\d+\s*/\s*\d+|\d+)$",
    re.IGNORECASE,
)
_FOOTER_PAGE_RE = re.compile(r"第\s*\d+\s*页(?:\s*/\s*\d+)?\s*$", re.IGNORECASE)
_TOC_LEADER_RE = re.compile(r"(?:\.{2,}|…{2,}|·{2,}|‧{2,})")
_LIST_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>(?:[-*•●▪◦]|\d+[.)、/／]|[一二三四五六七八九十百]+[、.]))\s*(?P<body>.+)$"
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百]+[章节部分篇]|[一二三四五六七八九十百]+[、.]|\d+(?:\.\d+){0,3}[.)、/／]?)\s*\S+"
)
_HEADING_END_RE = re.compile(r"[。！？!?；;：:]$")
_DATE_ONLY_RE = re.compile(r"^(?:20\d{2}[年./-]\s*\d{1,2}[月./-]\s*\d{1,2}日?|\d{4}[-/]\d{1,2}[-/]\d{1,2})$")
_NO_SPACE_BEFORE = set(",.;:!?，。；：！？、)]}》”’\"")
_NO_SPACE_AFTER = set("([{《“‘")


@dataclass
class TextLine:
    text: str
    x0: float
    top: float
    bottom: float
    size: float
    fontname: str


@dataclass
class Block:
    kind: str
    text: str = ""
    top: float = 0.0
    bottom: float = 0.0
    x0: float = 0.0
    rows: list[list[str | None]] | None = None


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _join_tokens(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if _CJK_RE.search(left[-1]) or _CJK_RE.match(right[0]):
        return left + right
    if right[0] in _NO_SPACE_BEFORE and left[-1] not in _NO_SPACE_AFTER:
        return left + right
    return left + " " + right


def _join_lines(lines: Sequence[str]) -> str:
    result = ""
    for line in lines:
        result = _join_tokens(result, _clean_text(line))
    return result.strip()


def _normal_key(text: str) -> str:
    return re.sub(r"\s+", "", text).strip("-—_|")


def _is_page_marker(text: str) -> bool:
    return bool(_PAGE_MARK_RE.fullmatch(_clean_text(text)))


def _footer_key(text: str) -> str | None:
    """Return a page-number-independent key for a repeated footer."""

    cleaned = _clean_text(text)
    match = _FOOTER_PAGE_RE.search(cleaned)
    if not match:
        return None
    prefix = cleaned[: match.start()].strip(" -—_|｜·•")
    if len(_normal_key(prefix)) < 3:
        return None
    return _normal_key(prefix) + "第#页"


def _is_footer_text(text: str) -> bool:
    cleaned = _clean_text(text)
    return bool(_footer_key(cleaned) or _FOOTER_PAGE_RE.fullmatch(cleaned))


def _extract_words(page: Any) -> list[dict[str, Any]]:
    kwargs = {
        "use_text_flow": True,
        "keep_blank_chars": False,
        "x_tolerance": 2,
        "y_tolerance": 3,
        "extra_attrs": ["fontname", "size"],
    }
    try:
        return list(page.extract_words(**kwargs) or [])
    except TypeError:
        kwargs.pop("extra_attrs", None)
        return list(page.extract_words(**kwargs) or [])


def _group_words(words: Iterable[dict[str, Any]]) -> list[TextLine]:
    clean_words = []
    for word in words:
        text = _clean_text(word.get("text"))
        if not text:
            continue
        clean_words.append(
            {
                "text": text,
                "x0": float(word.get("x0", 0.0)),
                "top": float(word.get("top", 0.0)),
                "bottom": float(word.get("bottom", word.get("top", 0.0))),
                "size": float(word.get("size", 0.0) or 0.0),
                "fontname": str(word.get("fontname", "")),
            }
        )
    if not clean_words:
        return []
    heights = [max(1.0, w["bottom"] - w["top"]) for w in clean_words]
    tolerance = max(2.0, min(8.0, statistics.median(heights) * 0.55))
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(clean_words, key=lambda item: (item["top"], item["x0"])):
        if not lines:
            lines.append([word])
            continue
        current = lines[-1]
        current_top = statistics.median(item["top"] for item in current)
        if abs(word["top"] - current_top) <= tolerance:
            current.append(word)
        else:
            lines.append([word])
    result = []
    for line in lines:
        ordered = sorted(line, key=lambda item: item["x0"])
        text = ""
        for item in ordered:
            text = _join_tokens(text, item["text"])
        result.append(
            TextLine(
                text=text,
                x0=min(item["x0"] for item in ordered),
                top=min(item["top"] for item in ordered),
                bottom=max(item["bottom"] for item in ordered),
                size=statistics.median(item["size"] for item in ordered),
                fontname=" ".join(item["fontname"] for item in ordered),
            )
        )
    return result


def _extract_table_rows(page: Any) -> tuple[list[tuple[float, float, list[list[str | None]]]], list[str]]:
    # Use line/rectangle geometry by default.  pdfplumber's text strategy can
    # turn ordinary two-column prose or bullet lists into false tables; it is
    # intentionally not enabled for this deterministic document workflow.
    settings_list = [
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "intersection_tolerance": 3,
        }
    ]
    tables: list[tuple[float, float, list[list[str | None]]]] = []
    warnings: list[str] = []
    had_error = False
    for strategy_index, settings in enumerate(settings_list):
        try:
            candidates = []
            for table in page.find_tables(table_settings=settings):
                rows = table.extract() or []
                width = max((len(row) for row in rows), default=0)
                nonempty = sum(bool(_clean_text(cell)) for row in rows for cell in row)
                if len(rows) >= 2 and width >= 2 and nonempty >= 4:
                    candidates.append((float(table.bbox[1]), float(table.bbox[3]), rows))
            if candidates:
                tables = candidates
                break
        except Exception:
            had_error = True
            continue
    if not tables and had_error:
        warnings.append("pdfplumber_table_extract_failed")
    return tables, warnings


def _inside_table(line: TextLine, tables: Sequence[tuple[float, float, list[list[str | None]]]]) -> bool:
    center = (line.top + line.bottom) / 2.0
    return any(top - 2 <= center <= bottom + 2 for top, bottom, _ in tables)


def _table_markdown(rows: Sequence[Sequence[str | None]]) -> str:
    cleaned: list[list[str]] = []
    width = max((len(row) for row in rows), default=0)
    for row in rows:
        values = [_clean_text(cell).replace("|", "\\|").replace("\n", "<br>") for cell in row]
        values.extend([""] * (width - len(values)))
        if any(values):
            cleaned.append(values)
    if not cleaned or width < 2:
        return "\n".join(_join_tokens("", cell) for row in cleaned for cell in row if cell).strip()
    header = cleaned[0]
    separator = ["---"] * width
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in cleaned[1:])
    return "\n".join(lines)


def _merge_table_rows(
    previous: Sequence[Sequence[str | None]],
    following: Sequence[Sequence[str | None]],
) -> list[list[str | None]]:
    """Merge a table continued on the next page.

    pdfplumber represents a vertically split row as a row whose first cell is
    empty.  Such cells are appended to the last row with ``<br>``.  A non-empty
    first cell starts a new row.  Repeated header rows are discarded.
    """

    width = max(
        (len(row) for row in list(previous) + list(following)),
        default=0,
    )

    def padded(row: Sequence[str | None]) -> list[str | None]:
        return list(row) + [None] * max(0, width - len(row))

    merged = [padded(row) for row in previous]
    if not merged:
        return [padded(row) for row in following]
    header = [_normal_key(_clean_text(cell)) for cell in merged[0]]

    for row_index, raw_row in enumerate(following):
        row = padded(raw_row)
        values = [_clean_text(value) for value in row]
        if not any(values):
            continue
        normalized = [_normal_key(value) for value in values]
        if row_index == 0 and normalized == header:
            continue
        if values[0]:
            merged.append(row)
            continue
        target = merged[-1]
        for index, value in enumerate(values):
            if not value:
                continue
            existing = _clean_text(target[index])
            target[index] = f"{existing}<br>{value}" if existing else value
    return merged


def _table_can_continue(
    previous: tuple[float, float, list[list[str | None]]],
    following: tuple[float, float, list[list[str | None]]],
    page_height: float,
) -> bool:
    if page_height <= 0 or not previous[2] or not following[2]:
        return False
    if previous[1] < page_height * 0.72 or following[0] > page_height * 0.28:
        return False
    previous_width = max((len(row) for row in previous[2]), default=0)
    following_width = max((len(row) for row in following[2]), default=0)
    return previous_width >= 2 and previous_width == following_width


def _build_heading_levels(lines: Sequence[TextLine], body_size: float) -> dict[float, int]:
    """Build a document-relative size→heading-level map.

    Absolute PDF point sizes are not comparable across files.  Ranking the
    sizes observed in heading-like lines keeps a document's own hierarchy.
    """

    candidates: list[float] = []
    for line in lines:
        text = _clean_text(line.text)
        if not text or len(text) > 100 or _HEADING_END_RE.search(text) or _DATE_ONLY_RE.fullmatch(text):
            continue
        font = line.fontname.lower()
        bold = any(token in font for token in ("bold", "heavy", "black", "demi"))
        numbered = bool(_NUMBERED_HEADING_RE.match(text))
        if bold or numbered or (body_size > 0 and line.size >= body_size * 1.6):
            candidates.append(round(line.size, 1))
    if not candidates:
        return {}
    unique: list[float] = []
    for size in sorted(set(candidates), reverse=True):
        if not unique or abs(size - unique[-1]) >= 0.6:
            unique.append(size)
    offset = 1 if body_size <= 0 or unique[0] > body_size * 1.12 else 2
    return {size: min(6, rank + offset) for rank, size in enumerate(unique)}


def _heading_level(
    line: TextLine, body_size: float, heading_levels: dict[float, int] | None = None
) -> int | None:
    text = _clean_text(line.text)
    if not text or len(text) > 100 or _HEADING_END_RE.search(text) or _DATE_ONLY_RE.fullmatch(text):
        return None
    font = line.fontname.lower()
    bold = any(token in font for token in ("bold", "heavy", "black", "demi"))
    if _NUMBERED_HEADING_RE.match(text):
        # A normal numbered list item is not a heading.  Require a visible
        # typographic signal before treating a numbered line as a heading.
        slash_number = bool(re.match(r"^\d+[/／]", text))
        if slash_number and len(text) <= 60 and not _HEADING_END_RE.search(text):
            # PDFs exported from WPS/Word often use ``1/小标题`` for a
            # third-level heading without a bold font.  Its short, isolated
            # shape is a stronger signal than the missing font metadata.
            return 3
        if not bold and not (body_size > 0 and line.size >= body_size * 1.15):
            return None
        mapped = (heading_levels or {}).get(round(line.size, 1))
        if mapped is not None:
            depth = 1
            prefix = re.match(r"^\d+(?:\.\d+){0,3}", text)
            if prefix:
                depth = min(4, prefix.group(0).count(".") + 1)
            return min(6, depth + 1)
        if re.match(r"^\d+(?:\.\d+){2}", text):
            return 4
        if re.match(r"^\d+(?:\.\d+)?", text):
            return 2 if "." not in text[:8] else 3
        return 2
    mapped = (heading_levels or {}).get(round(line.size, 1))
    if mapped is not None and (bold or line.size > body_size * 1.1):
        if not bold and mapped >= 4:
            return None
        if bold and mapped >= 3 and body_size > 0 and line.size >= body_size * 1.6:
            return 2
        return min(3, mapped) if bold else mapped
    if bold or (body_size > 0 and line.size >= body_size * 1.28):
        if body_size > 0 and line.size >= body_size * 1.55:
            return 1
        if body_size > 0 and line.size >= body_size * 1.38:
            return 2
        return 3
    return None


def _list_item(text: str) -> tuple[int, str] | None:
    match = _LIST_RE.match(text)
    if not match:
        return None
    indent = len(match.group("indent"))
    level = min(4, max(0, round(indent / 2)))
    return level, match.group("marker") + " " + match.group("body").strip()


def _toc_entry(text: str) -> tuple[str, str, str] | None:
    """Parse one table-of-contents row into number, title and page."""

    cleaned = _clean_text(text)
    if not cleaned or not _TOC_LEADER_RE.search(cleaned):
        return None
    without_leader = _TOC_LEADER_RE.sub(" ", cleaned)
    match = re.match(r"^\s*(\d{1,4})\s*(.*?)\s+(\d{1,4})\s*$", without_leader)
    if not match:
        return None
    title = _clean_text(match.group(2)).strip(" .·…")
    if not title:
        return None
    return match.group(1), title, match.group(3)


def _toc_entries_from_text(text: str) -> list[tuple[str, str, str]]:
    """Split a printed TOC even when pdfplumber returns the whole page as one line."""

    cleaned = _clean_text(text)
    if not cleaned:
        return []
    leader_matches = list(re.finditer(r"(?:\.{2,}|…{2,}|·{2,}|‧{2,})\s*\d{1,4}", cleaned))
    entries: list[tuple[str, str, str]] = []
    previous_end = 0
    for index, leader_match in enumerate(leader_matches):
        segment = cleaned[previous_end : leader_match.end()]
        if index == 0:
            first_number = re.search(r"\d{1,4}\s*(?:[、.)/／])?", segment)
            if not first_number:
                previous_end = leader_match.end()
                continue
            segment = segment[first_number.start() :]
        match = re.match(
            r"\s*(\d{1,4})\s*(?:[、.)/／])?\s*(.*?)\s*"
            r"(?:\.{2,}|…{2,}|·{2,}|‧{2,})\s*(\d{1,4})\s*$",
            segment,
        )
        if match:
            title = _clean_text(match.group(2)).strip(" .·…‧")
            if title:
                entries.append((match.group(1), title, match.group(3)))
        previous_end = leader_match.end()
    if entries:
        # Some PDF producers omit the dot leader for one row and concatenate
        # ``<page> <next-number>``.  Use the expected sequential number to
        # split that specific, otherwise ambiguous case.
        repaired: list[tuple[str, str, str]] = []
        for number, title, page in entries:
            try:
                expected_next = int(number) + 1
            except ValueError:
                expected_next = -1
            marker = (
                re.search(rf"\s+(\d{{1,4}})\s+{expected_next}(?=\D)", title)
                if expected_next > 0
                else None
            )
            if marker:
                left = _clean_text(title[: marker.start()])
                right = _clean_text(title[marker.end() :])
                if left and right:
                    repaired.append((number, left, marker.group(1)))
                    repaired.append((str(expected_next), right, page))
                    continue
            repaired.append((number, title, page))
        return repaired
    single = _toc_entry(cleaned)
    return [single] if single else []


def _is_toc_page(lines: Sequence[TextLine]) -> bool:
    heading = any(_clean_text(line.text).lower() in {"目录", "contents"} for line in lines)
    combined = " ".join(_clean_text(line.text) for line in lines)
    entries = len(_toc_entries_from_text(combined))
    if entries < 2:
        entries = sum(1 for line in lines if _toc_entry(line.text))
    return entries >= 3 or (heading and entries >= 1)


def _toc_markdown(lines: Sequence[TextLine]) -> list[str]:
    output: list[str] = []
    heading = any(_clean_text(line.text).lower() in {"目录", "contents"} for line in lines)
    content = " ".join(
        _clean_text(line.text)
        for line in lines
        if _clean_text(line.text).lower() not in {"目录", "contents"}
        and not _is_page_marker(line.text)
        and not _is_footer_text(line.text)
    )
    entries = _toc_entries_from_text(content)
    if heading:
        output.append("# 目录")
    if entries:
        output.extend(f"- {number}. {title}（第 {page} 页）" for number, title, page in entries)
        return output
    for line in lines:
        text = _clean_text(line.text)
        if text and text.lower() not in {"目录", "contents"} and not _is_page_marker(text):
            output.append(text)
    return output


def _paragraph_markdown(
    lines: Sequence[TextLine],
    body_size: float,
    base_x0: float | None = None,
    *,
    preserve_source_paragraphs: bool = False,
    heading_levels: dict[float, int] | None = None,
) -> list[str]:
    output: list[str] = []
    current: list[TextLine] = []
    page_left = min((line.x0 for line in lines), default=0.0) if base_x0 is None else base_x0
    gaps = [max(0.0, line.top - previous.bottom) for previous, line in zip(lines, lines[1:])]
    if gaps:
        # Paragraph gaps are usually the larger half of the observations.
        # Estimate normal line spacing from the lower half so one large gap
        # does not make every paragraph appear continuous.
        sorted_gaps = sorted(gaps)
        typical_gap = statistics.median(sorted_gaps[: max(1, len(sorted_gaps) // 3)])
    else:
        typical_gap = 0.0

    def flush() -> None:
        if not current:
            return
        text = _join_lines([line.text for line in current])
        if text:
            indent = "  " * min(4, max(0, round((current[0].x0 - page_left) / 24)))
            output.append(indent + text)
        current.clear()

    for line in lines:
        text = _clean_text(line.text)
        if not text or _is_page_marker(text):
            continue
        heading = _heading_level(line, body_size, heading_levels)
        if heading is not None:
            flush()
            output.append("#" * heading + " " + text)
            continue
        item = _list_item(text)
        if item is not None:
            flush()
            _, value = item
            level = min(4, max(0, round((line.x0 - page_left) / 24)))
            output.append("  " * level + value)
            continue
        if current:
            previous = current[-1]
            gap = line.top - previous.bottom
            height = max(6.0, previous.bottom - previous.top)
            indentation_changed = abs(line.x0 - previous.x0) > 28
            if preserve_source_paragraphs:
                gap_threshold = max(height * 0.8, typical_gap * 2.5, 8.0)
                punctuation_break = bool(re.search(r"[。！？!?；;]$", previous.text)) and gap > max(
                    typical_gap * 1.1, height * 0.45, 8.0
                )
                metadata_break = bool(re.search(r"[：:]", previous.text)) and gap > max(
                    typical_gap * 0.95, 8.0
                )
                should_flush = gap > gap_threshold or punctuation_break or metadata_break
                continuation = (
                    bool(_CJK_RE.match(text))
                    and not re.search(r"[。！？!?；;：:]$", previous.text)
                    and gap <= max(typical_gap * 2.2, height * 0.9, 12.0)
                )
                if continuation:
                    # OCR often shifts a wrapped line slightly to the right.
                    # An unfinished sentence is stronger evidence of the same
                    # paragraph than that small coordinate change.
                    should_flush = False
                    indentation_changed = False
            else:
                should_flush = gap > height * 1.8
            if should_flush or indentation_changed:
                flush()
        current.append(line)
    flush()
    return output


def _repeat_edge_lines(page_lines: Sequence[Sequence[TextLine]]) -> set[str]:
    edge_counts: Counter[str] = Counter()
    page_count = len(page_lines)
    for lines in page_lines:
        candidates = list(lines[:3]) + list(lines[-3:])
        page_keys: set[str] = set()
        for line in candidates:
            key = _footer_key(line.text) or _normal_key(line.text)
            if key and len(key) >= 3 and not _is_page_marker(key):
                page_keys.add(key)
        for key in page_keys:
            edge_counts[key] += 1
    threshold = max(2, math.ceil(page_count / 2))
    return {key for key, count in edge_counts.items() if count >= threshold}


def _text_pdf_to_markdown(pdf: Any) -> tuple[str, dict[str, Any]]:
    page_lines: list[list[TextLine]] = []
    page_tables: list[list[tuple[float, float, list[list[str | None]]]]] = []
    page_heights: list[float] = []
    warnings: list[str] = []
    sizes: list[float] = []
    for page in pdf.pages:
        tables, table_warnings = _extract_table_rows(page)
        warnings.extend(table_warnings)
        lines = _group_words(_extract_words(page))
        lines = [line for line in lines if not _inside_table(line, tables)]
        page_lines.append(lines)
        page_tables.append(tables)
        page_heights.append(float(getattr(page, "height", 0.0) or 0.0))
        sizes.extend(line.size for line in lines if line.size > 0)

    # Join a table split at the page boundary.  Only the last table on one
    # page and the first table on the next page are candidates, preventing
    # unrelated tables elsewhere in the document from being concatenated.
    continued_tables: set[tuple[int, int]] = set()
    for page_index in range(1, len(page_tables)):
        previous_tables = page_tables[page_index - 1]
        following_tables = page_tables[page_index]
        if not previous_tables or not following_tables:
            continue
        previous = previous_tables[-1]
        following = following_tables[0]
        if not _table_can_continue(previous, following, page_heights[page_index - 1]):
            continue
        merged = _merge_table_rows(previous[2], following[2])
        previous_tables[-1] = (previous[0], previous[1], merged)
        # Keep the merged rows as the continuation anchor so a table split
        # across three or more pages can be joined repeatedly.  Suppress only
        # the continuation rendering for this page.
        following_tables[0] = (following[0], following[1], merged)
        continued_tables.add((page_index, 0))

    repeated = _repeat_edge_lines(page_lines)
    body_size = statistics.median(sizes) if sizes else 0.0
    heading_levels = _build_heading_levels([line for page in page_lines for line in page], body_size)
    output: list[str] = []
    table_count = 0
    for page_index, (lines, tables) in enumerate(zip(page_lines, page_tables)):
        page_height = page_heights[page_index] if page_index < len(page_heights) else 0.0
        filtered_lines = [
            line
            for line in lines
            if (_footer_key(line.text) or _normal_key(line.text)) not in repeated
            and not (
                page_height > 0
                and line.bottom >= page_height * 0.80
                and _is_footer_text(line.text)
            )
        ]
        page_left = min((line.x0 for line in filtered_lines), default=0.0)
        if _is_toc_page(filtered_lines):
            output.extend(_toc_markdown(filtered_lines))
            continue
        table_blocks = [
            Block(kind="table", top=top, bottom=bottom, rows=rows)
            for table_index, (top, bottom, rows) in enumerate(tables)
            if (page_index, table_index) not in continued_tables
        ]
        text_blocks = [
            Block(kind="line", top=line.top, bottom=line.bottom, x0=line.x0, text=line.text)
            for line in filtered_lines
        ]
        blocks = sorted(table_blocks + text_blocks, key=lambda block: (block.top, block.x0))
        page_text_lines: list[TextLine] = []
        for block in blocks:
            if block.kind == "table":
                if page_text_lines:
                    output.extend(
                        _paragraph_markdown(
                            page_text_lines,
                            body_size,
                            page_left,
                            heading_levels=heading_levels,
                        )
                    )
                    page_text_lines = []
                table = _table_markdown(block.rows or [])
                if table:
                    output.append(table)
                    table_count += 1
            else:
                source_line = next(
                    (line for line in filtered_lines if abs(line.top - block.top) < 0.01 and line.text == block.text),
                    None,
                )
                if source_line is not None:
                    page_text_lines.append(source_line)
        if page_text_lines:
            output.extend(
                _paragraph_markdown(
                    page_text_lines,
                    body_size,
                    page_left,
                    heading_levels=heading_levels,
                )
            )
    markdown = re.sub(r"\n{3,}", "\n\n", "\n\n".join(item for item in output if item.strip())).strip()
    return markdown + ("\n" if markdown else ""), {
        "table_count": table_count,
        "warnings": sorted(set(warnings)),
        "text_chars": len(markdown),
    }


def _rapid_fields(result: Any) -> tuple[list[str], list[float], list[Any]]:
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = getattr(result, "boxes", None)
    if texts is None and isinstance(result, (tuple, list)):
        if len(result) >= 3:
            boxes, texts, scores = result[:3]
        elif len(result) == 2:
            texts, scores = result
    def as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        try:
            return list(value)
        except TypeError:
            return [value]

    text_values = [_clean_text(value) for value in as_list(texts)]
    score_values = [float(value) for value in as_list(scores)]
    box_values = as_list(boxes)
    if len(score_values) < len(text_values):
        score_values.extend([0.0] * (len(text_values) - len(score_values)))
    return text_values, score_values[: len(text_values)], box_values[: len(text_values)]


def _ocr_lines(result: Any) -> tuple[list[TextLine], float | None]:
    texts, scores, boxes = _rapid_fields(result)
    items: list[TextLine] = []
    for index, text in enumerate(texts):
        if not text:
            continue
        x0 = float(index)
        top = float(index)
        bottom = top + 12
        if index < len(boxes):
            try:
                points = boxes[index]
                x0 = min(float(point[0]) for point in points)
                top = min(float(point[1]) for point in points)
                bottom = max(float(point[1]) for point in points)
            except (TypeError, ValueError, IndexError):
                pass
        items.append(TextLine(text=text, x0=x0, top=top, bottom=bottom, size=bottom - top, fontname=""))
    items.sort(key=lambda item: (item.top, item.x0))
    if not items:
        return [], None
    tolerance = max(8.0, statistics.median(max(4.0, item.bottom - item.top) for item in items) * 0.6)
    grouped: list[TextLine] = []
    for item in items:
        item_center = (item.top + item.bottom) / 2.0
        if grouped:
            previous = grouped[-1]
            previous_center = (previous.top + previous.bottom) / 2.0
        else:
            previous = None
            previous_center = 0.0
        if previous is not None and abs(item_center - previous_center) <= tolerance:
            previous.text = _join_tokens(previous.text, item.text)
            previous.bottom = max(previous.bottom, item.bottom)
        else:
            grouped.append(item)
    confidence = sum(scores) / len(scores) if scores else None
    return grouped, confidence


def _ocr_pdf_to_markdown(path: Path, dpi: int) -> tuple[str, dict[str, Any]]:
    import numpy as np
    import pypdfium2 as pdfium

    try:
        from rapidocr import RapidOCR
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR
    try:
        engine = RapidOCR(params={"Global": {"text_score": 0.5, "log_level": "critical"}})
    except (TypeError, KeyError, ValueError):
        # Older rapidocr releases accept no constructor parameters.  Keep the
        # same offline engine and rely on its installed defaults there.
        engine = RapidOCR()
    document = pdfium.PdfDocument(str(path))
    page_lines: list[list[TextLine]] = []
    page_heights: list[float] = []
    confidences: list[float] = []
    for index in range(len(document)):
        page = document.get_page(index)
        try:
            bitmap = page.render(scale=float(dpi) / 72.0)
            image = np.asarray(bitmap.to_pil())
            lines, confidence = _ocr_lines(engine(image))
            if confidence is not None:
                confidences.append(confidence)
            page_lines.append(lines)
            page_heights.append(float(image.shape[0]) if getattr(image, "shape", None) is not None else 0.0)
        finally:
            page.close()
    repeated = _repeat_edge_lines(page_lines)
    output: list[str] = []
    for page_index, lines in enumerate(page_lines):
        page_height = page_heights[page_index] if page_index < len(page_heights) else 0.0
        filtered = [
            line
            for line in lines
            if not (
                page_height > 0
                and line.bottom >= page_height * 0.80
                and _is_footer_text(line.text)
            )
            and (_footer_key(line.text) or _normal_key(line.text)) not in repeated
        ]
        if not filtered:
            continue
        line_sizes = [line.size for line in filtered]
        body_size = statistics.median(line_sizes) if line_sizes else 0.0
        page_left = min((line.x0 for line in filtered), default=0.0)
        output.extend(
            _paragraph_markdown(
                filtered,
                body_size,
                page_left,
                preserve_source_paragraphs=True,
            )
        )
    markdown = re.sub(r"\n{3,}", "\n\n", "\n\n".join(output)).strip()
    warnings = ["image_table_structure_not_guaranteed"]
    return markdown + ("\n" if markdown else ""), {
        "table_count": 0,
        "warnings": warnings,
        "text_chars": len(markdown),
        "average_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
    }


def _classify_pdf(pdf: Any, probe_pages: int = 2) -> str:
    for page in pdf.pages[:probe_pages]:
        text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
        if len(re.sub(r"\s+", "", text)) >= 20:
            return "pdfplumber"
    return "rapidocr"


def convert_file(path: Path, output_path: Path, dpi: int) -> dict[str, Any]:
    import pdfplumber

    result: dict[str, Any] = {
        "input": str(path),
        "output": str(output_path),
        "status": "failed",
        "backend": "",
        "page_count": 0,
        "warnings": [],
    }
    try:
        with pdfplumber.open(str(path)) as pdf:
            result["page_count"] = len(pdf.pages)
            backend = _classify_pdf(pdf)
            if backend == "pdfplumber":
                markdown, details = _text_pdf_to_markdown(pdf)
            else:
                markdown, details = _ocr_pdf_to_markdown(path, dpi)
            if not markdown.strip():
                raise ValueError("no_effective_text_or_table")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8", newline="\n")
            try:
                output_path.chmod(0o600)
            except OSError:
                pass
            result.update(details)
            result["backend"] = backend
            result["status"] = "succeeded"
    except Exception as exc:
        result["warnings"] = [str(exc)[:240]]
    return result


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将本地 PDF 转为版式更稳定的 Markdown")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="单个 PDF 文件")
    source.add_argument("--input-dir", type=Path, help="批量处理目录中的 PDF")
    parser.add_argument("--output-dir", type=Path, required=True, help="Markdown 输出目录")
    parser.add_argument("--ocr-dpi", type=int, default=200, help="图片 PDF OCR DPI，默认 200")
    parser.add_argument("--force", action="store_true", help="覆盖已经存在的 Markdown")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if not 120 <= args.ocr_dpi <= 300:
        print(json.dumps({"status": "failed", "error": "--ocr-dpi 必须在 120-300 之间"}, ensure_ascii=False))
        return 2
    if args.input is not None:
        paths = [args.input]
    else:
        paths = sorted(args.input_dir.glob("*.pdf")) if args.input_dir else []
    if not paths:
        print(json.dumps({"status": "failed", "error": "没有找到 PDF 文件"}, ensure_ascii=False))
        return 2
    summaries = []
    for path in paths:
        output_path = args.output_dir / (path.stem + ".md")
        if output_path.exists() and not args.force:
            summaries.append(
                {"input": str(path), "output": str(output_path), "status": "skipped", "warnings": ["output_exists"]}
            )
            continue
        summaries.append(convert_file(path, output_path, args.ocr_dpi))
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0 if all(item["status"] in {"succeeded", "skipped"} for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
