#!/usr/bin/env python3
"""Shared deterministic PDF layout core used by cleaning and the CLI.

The script intentionally stays local and deterministic:
* the first two pages decide whether the document uses pdfplumber or RapidOCR;
* text PDFs keep paragraphs, inferred headings, lists and pdfplumber tables;
* image PDFs use RapidOCR text boxes and preserve readable line order;
* no page markers, images, formulas, decorations or sample正文 are written to logs.

The module is intentionally dependency-light: optional pdfplumber/RapidOCR
imports stay inside the backend adapters.  The historical CLI in
``scripts/pdf_markdown_refine.py`` is now only a thin wrapper around this core.
"""

from __future__ import annotations

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
_FIELD_LABEL_RE = re.compile(r"^[\u3400-\u9fffA-Za-z0-9 _（）()]{2,18}\s*[：:]\s*\S+")
_NO_SPACE_BEFORE = set(",.;:!?，。；：！？、)]}》”’\"")
_NO_SPACE_AFTER = set("([{《“‘")
_TOC_NUMBER_TOKEN_RE = (
    r"(?:第\s*(?:\d{1,4}|[一二三四五六七八九十百千万]+)\s*[章节部分篇回]?|"
    r"\d{1,4}(?:\.\d{1,4}){0,3}|[一二三四五六七八九十百千万]+)"
)
_TOC_CHILD_MARKERS = {
    ".)",
    ")",
    "）",
    "/",
    "／",
}
_TOC_MARKER_RE = r"(?:(?:\.)?\)|[、.）/／]?)"
_SOURCE_HEADING_MARK_RE = re.compile(r"^\s*#{1,6}\s+")


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


@dataclass(frozen=True)
class TocEntry:
    number: str
    title: str
    page: str
    marker: str = ""
    indent: float = 0.0

    @property
    def prefix(self) -> str:
        return self.number + self.marker

    @property
    def depth_hint(self) -> int:
        if self.number.startswith("第"):
            return 1
        if "." in self.number:
            return min(6, self.number.count(".") + 1)
        if self.marker in _TOC_CHILD_MARKERS:
            return 2
        return 1


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _strip_source_heading_marker(text: str) -> str:
    """Remove literal Markdown-like markers embedded in extracted PDF text."""

    return _SOURCE_HEADING_MARK_RE.sub("", _clean_text(text))


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
        text = _strip_source_heading_marker(line.text)
        if not text or len(text) > 100 or _HEADING_END_RE.search(text) or _DATE_ONLY_RE.fullmatch(text):
            continue
        numbered = bool(_NUMBERED_HEADING_RE.match(text))
        # Keep a size gap before allowing a line to participate in the
        # document-relative heading map; bold alone is not sufficient.
        size_signal = body_size > 0 and line.size >= body_size * 1.22
        numbered_signal = numbered and size_signal
        if numbered_signal or size_signal:
            candidates.append(round(line.size, 1))
    if not candidates:
        return {}
    unique: list[float] = []
    for size in sorted(set(candidates), reverse=True):
        if not unique or abs(size - unique[-1]) >= 0.6:
            unique.append(size)
    offset = 1 if body_size <= 0 or unique[0] > body_size * 1.12 else 2
    return {size: min(6, rank + offset) for rank, size in enumerate(unique)}


def _heading_key(text: str) -> str:
    """Normalize a heading for comparison without retaining visual syntax."""

    cleaned = _strip_source_heading_marker(text).lower()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", cleaned)


def _heading_key_without_prefix(text: str) -> str:
    cleaned = _strip_source_heading_marker(text)
    without_prefix = re.sub(
        rf"^\s*{_TOC_NUMBER_TOKEN_RE}\s*{_TOC_MARKER_RE}\s*",
        "",
        cleaned,
    )
    return _heading_key(without_prefix)


def _toc_heading_levels(records: Sequence[TocEntry]) -> dict[str, int]:
    """Map printed TOC titles to relative body heading depths."""

    result: dict[str, int] = {}
    for entry in records:
        depth = max(1, min(5, entry.depth_hint))
        for value in (entry.title, f"{entry.prefix} {entry.title}"):
            key = _heading_key(value)
            if key:
                result.setdefault(key, depth)
            key_without_prefix = _heading_key_without_prefix(value)
            if key_without_prefix:
                result.setdefault(key_without_prefix, depth)
    return result


def _toc_records_for_pages(
    page_lines: Sequence[Sequence[TextLine]],
    toc_page_flags: Sequence[bool],
) -> list[TocEntry]:
    """Collect line-level and concatenated TOC candidates for title matching."""

    result: list[TocEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for lines, is_toc in zip(page_lines, toc_page_flags):
        if not is_toc:
            continue
        candidates = [
            entry
            for line in lines
            for entry in _toc_entry_records_from_text(line.text, indent=line.x0)
        ]
        combined = _join_lines([line.text for line in lines])
        candidates.extend(_toc_entry_records_from_text(combined))
        for entry in candidates:
            key = (entry.number, entry.title, entry.page)
            if key not in seen:
                seen.add(key)
                result.append(entry)
    return result


def _is_bold(line: TextLine) -> bool:
    font = line.fontname.lower()
    return any(token in font for token in ("bold", "heavy", "black", "demi"))


def _merge_wrapped_heading_lines(
    lines: Sequence[TextLine],
    body_size: float,
    heading_levels: dict[float, int] | None = None,
    toc_heading_levels: dict[str, int] | None = None,
) -> list[TextLine]:
    """Join visual line wraps before heading classification.

    Only a heading-like first line can trigger a merge.  This avoids joining
    ordinary bold prose merely because two lines share a font.
    """

    if not lines:
        return []
    result: list[TextLine] = []
    def heading_signal(line: TextLine) -> bool:
        text = _strip_source_heading_marker(line.text)
        key = _heading_key(text)
        if key and key in (toc_heading_levels or {}):
            return True
        numbered = bool(_NUMBERED_HEADING_RE.match(text))
        size_signal = body_size > 0 and line.size >= body_size * 1.18
        return numbered and (_is_bold(line) or size_signal) or (
            size_signal and len(text) <= 120 and not _HEADING_END_RE.search(text)
        )

    index = 0
    while index < len(lines):
        current = lines[index]
        if index + 1 < len(lines) and heading_signal(current):
            following = lines[index + 1]
            current_text = _strip_source_heading_marker(current.text)
            following_text = _strip_source_heading_marker(following.text)
            gap = following.top - current.bottom
            same_size = abs(following.size - current.size) <= max(1.5, current.size * 0.12)
            close = gap <= max(current.size * 1.35, 24.0)
            aligned = abs(following.x0 - current.x0) <= max(48.0, current.size * 2.5)
            next_is_new_item = bool(_NUMBERED_HEADING_RE.match(following_text) or _LIST_RE.match(following_text))
            if (
                current_text
                and following_text
                and same_size
                and close
                and aligned
                and heading_signal(following)
                and not next_is_new_item
            ):
                result.append(
                    TextLine(
                        text=_join_tokens(current_text, following_text),
                        x0=min(current.x0, following.x0),
                        top=current.top,
                        bottom=following.bottom,
                        size=max(current.size, following.size),
                        fontname=current.fontname or following.fontname,
                    )
                )
                index += 2
                continue
        result.append(current)
        index += 1
    return result


def _heading_level(
    line: TextLine,
    body_size: float,
    heading_levels: dict[float, int] | None = None,
    toc_heading_levels: dict[str, int] | None = None,
    heading_floor: int = 1,
    isolated: bool = False,
) -> int | None:
    text = _strip_source_heading_marker(line.text)
    if not text or len(text) > 140 or _DATE_ONLY_RE.fullmatch(text):
        return None
    key = _heading_key(text)
    toc_depth = (toc_heading_levels or {}).get(key)
    if toc_depth is None:
        toc_depth = (toc_heading_levels or {}).get(_heading_key_without_prefix(text))
    if toc_depth is not None:
        return min(6, max(heading_floor, heading_floor + toc_depth - 1))

    bold = _is_bold(line)
    size_signal = body_size > 0 and line.size >= body_size * 1.22
    strong_size_signal = body_size > 0 and line.size >= body_size * 1.38
    # A sentence-like line is body text even when its font is bold.
    sentence_like = bool(_HEADING_END_RE.search(text)) or len(text) > 90
    if _NUMBERED_HEADING_RE.match(text):
        slash_number = bool(re.match(r"^\d+[/／]", text))
        marker_match = re.match(
            r"^\s*(?:\d+(?:\.\d+){0,3}|[一二三四五六七八九十百千万]+)(?P<marker>[、.）)/／]?)",
            text,
        )
        marker = marker_match.group("marker") if marker_match else ""
        same_size_title = (
            isolated
            and bold
            and len(text) <= 60
            and marker in {"、", ".", "/", "／", "）", ")"}
        )
        if (not size_signal and not same_size_title) or sentence_like:
            return None
        mapped = (heading_levels or {}).get(round(line.size, 1))
        if mapped is not None:
            depth = 1
            prefix = re.match(r"^\d+(?:\.\d+){0,3}", text)
            if prefix:
                depth = min(4, prefix.group(0).count(".") + 1)
            if slash_number:
                depth = max(depth, 2)
            return min(6, max(heading_floor, heading_floor + depth - 1))
        if re.match(r"^\d+(?:\.\d+){2}", text):
            depth = 4
        elif re.match(r"^\d+(?:\.\d+)?", text):
            depth = 2 if "." not in text[:8] else 3
        else:
            depth = 2
        return min(6, max(heading_floor, heading_floor + depth - 1))
    # Without a TOC, a non-numbered heading needs a significant size jump.
    # Bold at body size is deliberately insufficient.
    if strong_size_signal and not sentence_like:
        mapped = (heading_levels or {}).get(round(line.size, 1))
        depth = mapped or 1
        return min(6, max(heading_floor, heading_floor + depth - 1))
    return None


def _list_item(text: str) -> tuple[int, str] | None:
    match = _LIST_RE.match(text)
    if not match:
        return None
    indent = len(match.group("indent"))
    level = min(4, max(0, round(indent / 2)))
    return level, match.group("marker") + " " + match.group("body").strip()


def _toc_entry_record(text: str, indent: float = 0.0) -> TocEntry | None:
    """Parse one printed TOC row, retaining the original number marker."""

    cleaned = _clean_text(text)
    if not cleaned or not _TOC_LEADER_RE.search(cleaned):
        return None
    without_leader = _TOC_LEADER_RE.sub(" ", cleaned)
    match = re.match(
        rf"^\s*(?P<number>{_TOC_NUMBER_TOKEN_RE})\s*"
        rf"(?P<marker>{_TOC_MARKER_RE})\s*(?P<title>.*?)\s+"
        r"(?:[（(]\s*)?(?:第\s*)?(?P<page>\d{1,4})\s*页?\s*[）)]?\s*$",
        without_leader,
    )
    if not match:
        return None
    if re.fullmatch(r"[一二三四五六七八九十百千万]+", match.group("number")) and not match.group("marker"):
        return None
    title = _clean_text(match.group("title")).strip(" .·…‧")
    if not title:
        return None
    return TocEntry(
        number=match.group("number"),
        title=title,
        page=match.group("page"),
        marker=match.group("marker"),
        indent=indent,
    )


def _toc_entry(text: str) -> tuple[str, str, str] | None:
    entry = _toc_entry_record(text)
    return (entry.number, entry.title, entry.page) if entry else None


def _sequence_number(number: str) -> int | None:
    if number.startswith("第"):
        number = re.sub(r"^第\s*", "", number)
        number = re.sub(r"[章节部分篇回]\s*$", "", number)
    if number.isdigit():
        return int(number)
    if not re.fullmatch(r"[一二三四五六七八九十百千万]+", number):
        return None
    values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    section = 0
    digit = 0
    for char in number:
        value = values[char]
        if value < 10:
            digit = value
        else:
            section += (digit or 1) * value
            digit = 0
    return total + section + digit


def _is_bare_chinese_number(number: str) -> bool:
    return bool(re.fullmatch(r"[一二三四五六七八九十百千万]+", number))


def _toc_entry_records_from_text(text: str, indent: float = 0.0) -> list[TocEntry]:
    """Split a printed TOC even when pdfplumber returns the whole page as one line."""

    cleaned = _clean_text(text)
    if not cleaned:
        return []
    leader_matches = list(
        re.finditer(
            r"(?:\.{2,}|…{2,}|·{2,}|‧{2,})\s*"
            r"(?:[（(]\s*)?(?:第\s*)?\d{1,4}\s*页?\s*[）)]?",
            cleaned,
        )
    )
    entries: list[TocEntry] = []
    previous_end = 0
    for index, leader_match in enumerate(leader_matches):
        segment = cleaned[previous_end : leader_match.end()]
        if index == 0:
            first_number = re.search(rf"{_TOC_NUMBER_TOKEN_RE}\s*{_TOC_MARKER_RE}", segment)
            if not first_number:
                previous_end = leader_match.end()
                continue
            segment = segment[first_number.start() :]
        match = re.match(
            rf"\s*(?P<number>{_TOC_NUMBER_TOKEN_RE})\s*"
            rf"(?P<marker>{_TOC_MARKER_RE})\s*(?P<title>.*?)\s*"
            r"(?:\.{2,}|…{2,}|·{2,}|‧{2,})\s*"
            r"(?:[（(]\s*)?(?:第\s*)?(?P<page>\d{1,4})\s*页?\s*[）)]?\s*$",
            segment,
        )
        if match:
            title = _clean_text(match.group("title")).strip(" .·…‧")
            if title and not (_is_bare_chinese_number(match.group("number")) and not match.group("marker")):
                entries.append(
                    TocEntry(
                        number=match.group("number"),
                        title=title,
                        page=match.group("page"),
                        marker=match.group("marker"),
                        indent=indent,
                    )
                )
        previous_end = leader_match.end()
    if not entries:
        single = _toc_entry_record(cleaned, indent)
        return [single] if single else []

    # Some PDF producers omit one dot leader and concatenate ``<page>
    # <next-number>``.  Split only the expected next number, avoiding title
    # digits such as years or prices.
    repaired: list[TocEntry] = []
    for entry in entries:
        current = _sequence_number(entry.number)
        expected_next = current + 1 if current is not None else -1
        marker = (
            re.search(rf"\s+(\d{{1,4}})\s+{expected_next}(?=\D)", entry.title)
            if expected_next > 0
            else None
        )
        if marker:
            left = _clean_text(entry.title[: marker.start()])
            right = _clean_text(entry.title[marker.end() :])
            if left and right:
                repaired.append(TocEntry(entry.number, left, marker.group(1), entry.marker, entry.indent))
                repaired.append(TocEntry(str(expected_next), right, entry.page, "", entry.indent))
                continue
        repaired.append(entry)
    return repaired


def _toc_entries_from_text(text: str) -> list[tuple[str, str, str]]:
    """Compatibility view of parsed TOC entries without marker metadata."""

    return [(entry.number, entry.title, entry.page) for entry in _toc_entry_records_from_text(text)]


def _select_top_level_toc_entries(entries: Sequence[TocEntry]) -> list[TocEntry]:
    """Keep the document's sequential top-level TOC and drop child resets."""

    selected: list[TocEntry] = []
    expected: int | None = None
    base_indent = min((entry.indent for entry in entries if entry.depth_hint == 1), default=0.0)
    for entry in entries:
        value = _sequence_number(entry.number)
        if entry.depth_hint > 1:
            continue
        if entry.indent > base_indent + 18:
            continue
        if value is None:
            if expected is None:
                selected.append(entry)
            continue
        if expected is None:
            selected.append(entry)
            expected = value + 1
            continue
        if value == expected:
            selected.append(entry)
            expected += 1
        elif value > expected:
            # A missing top-level entry is safer than treating a later chapter
            # as a child; resume the observed sequence.
            selected.append(entry)
            expected = value + 1
        # A lower number is a numbering reset and is treated as a child.
    return selected


def _clean_toc_title(text: str) -> str:
    """Remove leaders/page suffixes from a fallback, unnumbered TOC row."""

    cleaned = _clean_text(text)
    cleaned = _TOC_LEADER_RE.sub(" ", cleaned)
    cleaned = re.sub(
        r"\s*(?:[（(]\s*)?(?:第\s*)?\d{1,4}\s*页?\s*[）)]?\s*$",
        "",
        cleaned,
    )
    return _clean_text(cleaned).strip(" .·…‧")


def _is_toc_page(lines: Sequence[TextLine]) -> bool:
    heading = any(_clean_text(line.text).lower() in {"目录", "contents"} for line in lines)
    combined = " ".join(_clean_text(line.text) for line in lines)
    entries = len(_select_top_level_toc_entries(_toc_entry_records_from_text(combined)))
    if entries < 2:
        entries = sum(1 for line in lines if _toc_entry(line.text))
    return entries >= 3 or (heading and entries >= 1)


def _toc_markdown(lines: Sequence[TextLine]) -> list[str]:
    output: list[str] = []
    heading = any(_clean_text(line.text).lower() in {"目录", "contents"} for line in lines)
    usable_lines = [
        line
        for line in lines
        if _clean_text(line.text).lower() not in {"目录", "contents"}
        and not _is_page_marker(line.text)
        and not _is_footer_text(line.text)
    ]
    line_records = [
        entry
        for line in usable_lines
        for entry in _toc_entry_records_from_text(line.text, indent=line.x0)
    ]
    content = " ".join(_clean_text(line.text) for line in usable_lines)
    combined_records = _toc_entry_records_from_text(content)
    # Line-level parsing preserves indentation, while the combined pass can
    # recover rows whose dot leader was split across extracted lines.  Compare
    # the selected top-level candidates rather than raw record counts: a
    # line-level pass can contain a child row while missing a concatenated
    # top-level row, producing the same raw count as the combined pass.
    line_entries = _select_top_level_toc_entries(line_records)
    combined_entries = _select_top_level_toc_entries(combined_records)
    if len(combined_entries) > len(line_entries):
        records, entries = combined_records, combined_entries
    else:
        records, entries = line_records, line_entries
    if heading:
        output.append("# 目录")
    if entries:
        output.extend(f"- {entry.prefix} {entry.title}" for entry in entries)
        return output
    if records:
        # Parsed entries existed but were all child resets; never fall back to
        # emitting them as if they were top-level titles.
        return output
    for line in lines:
        text = _strip_source_heading_marker(line.text)
        if text and text.lower() not in {"目录", "contents"} and not _is_page_marker(text):
            cleaned = _clean_toc_title(text)
            if cleaned:
                output.append(f"- {cleaned}")
    return output


def _paragraph_markdown(
    lines: Sequence[TextLine],
    body_size: float,
    base_x0: float | None = None,
    *,
    preserve_source_paragraphs: bool = False,
    heading_levels: dict[float, int] | None = None,
    toc_heading_levels: dict[str, int] | None = None,
    heading_floor: int = 1,
) -> list[str]:
    output: list[str] = []
    lines = _merge_wrapped_heading_lines(lines, body_size, heading_levels, toc_heading_levels)
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
        text = _join_lines([_strip_source_heading_marker(line.text) for line in current])
        if text:
            indent = "  " * min(4, max(0, round((current[0].x0 - page_left) / 24)))
            output.append(indent + text)
        current.clear()

    for line_index, line in enumerate(lines):
        text = _strip_source_heading_marker(line.text)
        if not text or _is_page_marker(text):
            continue
        previous_gap = (
            line.top - lines[line_index - 1].bottom if line_index > 0 else float("inf")
        )
        next_gap = (
            lines[line_index + 1].top - line.bottom
            if line_index + 1 < len(lines)
            else float("inf")
        )
        isolation_gap = max(typical_gap * 1.5, max(6.0, line.size) * 1.2, 8.0)
        isolated_heading = previous_gap >= isolation_gap and next_gap >= isolation_gap
        heading = _heading_level(
            line,
            body_size,
            heading_levels,
            toc_heading_levels,
            heading_floor,
            isolated_heading,
        )
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
        if preserve_source_paragraphs and current and _FIELD_LABEL_RE.match(text):
            # Consecutive ``label:value`` metadata fields are separate source
            # lines, not one paragraph; the label text is not document-specific.
            flush()
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
                should_flush = (
                    gap > gap_threshold
                    or punctuation_break
                    or metadata_break
                    or indentation_changed
                )
            else:
                gap_threshold = max(height * 1.8, typical_gap * 2.5, 8.0)
                should_flush = gap > gap_threshold or indentation_changed
            continuation = (
                bool(_CJK_RE.match(text))
                and not re.search(r"[。！？!?；;：:]$", previous.text)
                and gap <= max(typical_gap * 2.2, height * 0.95, 12.0)
            )
            if continuation:
                # A wrapped line is stronger evidence of the same paragraph
                # than a punctuation mark or a small OCR x-shift.
                should_flush = False
                indentation_changed = False
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
    filtered_page_lines: list[list[TextLine]] = []
    for page_index, lines in enumerate(page_lines):
        page_height = page_heights[page_index] if page_index < len(page_heights) else 0.0
        filtered_page_lines.append(
            [
                line
                for line in lines
                if (_footer_key(line.text) or _normal_key(line.text)) not in repeated
                and not (
                    page_height > 0
                    and line.bottom >= page_height * 0.80
                    and _is_footer_text(line.text)
                )
            ]
        )
    toc_page_flags = [_is_toc_page(lines) for lines in filtered_page_lines]
    toc_records = _toc_records_for_pages(filtered_page_lines, toc_page_flags)
    toc_heading_levels = _toc_heading_levels(toc_records)
    heading_floor = 2 if any(toc_page_flags) else 1
    output: list[str] = []
    table_count = 0
    for page_index, (lines, tables) in enumerate(zip(page_lines, page_tables)):
        page_height = page_heights[page_index] if page_index < len(page_heights) else 0.0
        filtered_lines = filtered_page_lines[page_index]
        page_left = min((line.x0 for line in filtered_lines), default=0.0)
        if toc_page_flags[page_index]:
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
                            toc_heading_levels=toc_heading_levels,
                            heading_floor=heading_floor,
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
                    toc_heading_levels=toc_heading_levels,
                    heading_floor=heading_floor,
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
    filtered_page_lines: list[list[TextLine]] = []
    for page_index, lines in enumerate(page_lines):
        page_height = page_heights[page_index] if page_index < len(page_heights) else 0.0
        filtered_page_lines.append(
            [
                line
                for line in lines
                if not (
                    page_height > 0
                    and line.bottom >= page_height * 0.80
                    and _is_footer_text(line.text)
                )
                and (_footer_key(line.text) or _normal_key(line.text)) not in repeated
            ]
        )
    toc_page_flags = [_is_toc_page(lines) for lines in filtered_page_lines]
    toc_records = _toc_records_for_pages(filtered_page_lines, toc_page_flags)
    toc_heading_levels = _toc_heading_levels(toc_records)
    heading_floor = 2 if any(toc_page_flags) else 1
    all_ocr_lines = [line for page in filtered_page_lines for line in page]
    all_sizes = [line.size for line in all_ocr_lines if line.size > 0]
    global_body_size = statistics.median(all_sizes) if all_sizes else 0.0
    global_heading_levels = _build_heading_levels(all_ocr_lines, global_body_size)
    for page_index, lines in enumerate(page_lines):
        page_height = page_heights[page_index] if page_index < len(page_heights) else 0.0
        filtered = filtered_page_lines[page_index]
        if not filtered:
            continue
        if toc_page_flags[page_index]:
            output.extend(_toc_markdown(filtered))
            continue
        body_size = global_body_size
        page_left = min((line.x0 for line in filtered), default=0.0)
        output.extend(
            _paragraph_markdown(
                filtered,
                body_size,
                page_left,
                preserve_source_paragraphs=True,
                heading_levels=global_heading_levels,
                toc_heading_levels=toc_heading_levels,
                heading_floor=heading_floor,
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


__all__ = [
    "Block",
    "TextLine",
    "TocEntry",
    "_classify_pdf",
    "_clean_text",
    "_footer_key",
    "_group_words",
    "_is_footer_text",
    "_merge_table_rows",
    "_merge_wrapped_heading_lines",
    "_paragraph_markdown",
    "_select_top_level_toc_entries",
    "_table_markdown",
    "_toc_entries_from_text",
    "_toc_entry_records_from_text",
    "_toc_markdown",
    "_text_pdf_to_markdown",
    "_ocr_pdf_to_markdown",
]
