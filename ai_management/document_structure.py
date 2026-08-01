from __future__ import annotations

import math
import re
from dataclasses import dataclass

_TOC_LINE = re.compile(
    r"^\s*(?P<title>(?:第[一二三四五六七八九十百0-9]+[章节篇部卷]\s*)?.{2,80}?)"
    r"\s*(?:[.．·…_—-]{2,}|\s{2,})\s*(?P<page>\d{1,5})\s*$"
)
_HEADING = re.compile(r"^\s*#{1,6}\s+(?P<title>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class TocRemovalResult:
    markdown: str
    detected: bool
    needs_review: bool
    headings: list[str]
    warnings: list[str]


def _normalized_heading(value: str) -> str:
    value = re.sub(r"^\s*#{1,6}\s*", "", value)
    return re.sub(r"[\s　:：.。·…_—-]+", "", value).casefold()


def _candidate_toc_entries(
    lines: list[str],
) -> tuple[int | None, list[tuple[int, str, int]]]:
    explicit_index: int | None = None
    all_entries: list[tuple[int, str, int]] = []
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        marker = (
            _normalized_heading(heading.group("title"))
            if heading
            else _normalized_heading(line)
        )
        # PDF text extraction often preserves a plain ``目录`` line but not a
        # Markdown heading.  Treat it as an explicit marker only near the
        # beginning; a later body mention of the word must not authorize a
        # deletion.
        if (
            explicit_index is None
            and marker in {"目录", "contents"}
            and index < 80
        ):
            explicit_index = index
        if match := _TOC_LINE.match(line):
            all_entries.append(
                (index, match.group("title").strip(), int(match.group("page")))
            )
    if explicit_index is not None:
        entries = [
            entry
            for entry in all_entries
            if explicit_index < entry[0] <= explicit_index + 2_000
        ]
        dense: list[tuple[int, str, int]] = []
        last_index = explicit_index
        for entry in entries:
            gap_lines = lines[last_index + 1 : entry[0]]
            nonblank_gap = sum(bool(value.strip()) for value in gap_lines)
            if dense and nonblank_gap > 3:
                break
            if not dense and nonblank_gap > 6:
                break
            dense.append(entry)
            last_index = entry[0]
        return explicit_index, dense

    early_limit = min(len(lines), max(80, math.ceil(len(lines) * 0.25)))
    early_entries = [entry for entry in all_entries if entry[0] < early_limit]
    groups: list[list[tuple[int, str, int]]] = []
    for entry in early_entries:
        if not groups:
            groups.append([entry])
            continue
        previous = groups[-1][-1][0]
        nonblank_gap = sum(
            bool(value.strip()) for value in lines[previous + 1 : entry[0]]
        )
        if nonblank_gap <= 2:
            groups[-1].append(entry)
        else:
            groups.append([entry])
    dense = max(groups, key=len, default=[])
    return None, dense if len(dense) >= 3 else []


def remove_reliable_toc(markdown: str) -> TocRemovalResult:
    """Remove a reliable TOC while returning headings for hierarchy recovery."""

    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    toc_heading_index, toc_entries = _candidate_toc_entries(lines)
    toc_indexes = {index for index, _, _ in toc_entries}
    body_headings = [
        match.group("title").strip()
        for index, line in enumerate(lines)
        if index not in toc_indexes
        and index != toc_heading_index
        and (match := _HEADING.match(line))
        and _normalized_heading(match.group("title")) not in {"目录", "contents"}
    ]

    if len(toc_entries) < 2:
        return TocRemovalResult(
            markdown="\n".join(lines).strip(),
            detected=False,
            needs_review=False,
            headings=body_headings,
            warnings=[],
        )

    pages = [entry[2] for entry in toc_entries]
    page_monotonic = pages == sorted(pages) and len(set(pages)) >= 2
    body_keys = {_normalized_heading(value) for value in body_headings}
    matched = [
        title
        for _, title, _ in toc_entries
        if _normalized_heading(title) in body_keys
    ]
    minimum_matches = max(2, math.ceil(len(toc_entries) * 0.6))
    reliable_by_body_headings = len(matched) >= minimum_matches
    # A text-only PDF can have a clearly marked, early and dense TOC while
    # losing the body's heading markup during conversion.  Keeping such a TOC
    # in retrieval is worse than removing it, but the fallback is deliberately
    # narrow: it needs an explicit early marker, at least six ordered entries,
    # and no more than one surviving Markdown heading to corroborate.
    reliable_by_explicit_dense_toc = (
        toc_heading_index is not None
        and toc_heading_index < 20
        and len(toc_entries) >= 6
        and len(body_headings) <= 1
    )
    reliable = page_monotonic and (
        reliable_by_body_headings or reliable_by_explicit_dense_toc
    )
    if not reliable:
        return TocRemovalResult(
            markdown="\n".join(lines).strip(),
            detected=True,
            needs_review=True,
            headings=body_headings,
            warnings=["toc_review:疑似目录无法与正文标题可靠对应"],
        )

    remove_indexes = {index for index, _, _ in toc_entries}
    if toc_heading_index is not None:
        remove_indexes.add(toc_heading_index)
    cleaned = [
        line for index, line in enumerate(lines) if index not in remove_indexes
    ]
    return TocRemovalResult(
        markdown=re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip(),
        detected=True,
        needs_review=False,
        headings=matched,
        warnings=[],
    )


__all__ = ["TocRemovalResult", "remove_reliable_toc"]
