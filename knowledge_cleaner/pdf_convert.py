"""PDF-to-Markdown conversion and consultation privacy safeguards.

The public entry point is :func:`convert_pdf`.  It deliberately returns one
``PDFConversionResult`` for one PDF; page objects are diagnostics and are not
separate knowledge-base documents.

``pypdf`` is the always-available text-layer probe/fallback.  ``pdfplumber``
and OCR backends are imported lazily so batch jobs can route a source to
review instead of crashing when an optional backend is unavailable.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from pypdf import PdfReader

from . import pdf_markdown_refine as _layout_core

_COMPACT_WHITESPACE = re.compile(r"[ \t\f\v]+")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^\n)]*\)")
_REFERENCE_IMAGE = re.compile(r"(?m)^\s*!\[[^\]]*\]\s*\[[^\]]+\]\s*$")
_IMAGE_REFERENCE_DEF = re.compile(r"(?mi)^\s*\[[^\]]+\]:\s*\S+\s*$")
_HTML_IMAGE = re.compile(r"<\s*(?:img|figure)\b[^>]*>(?:.*?</\s*figure\s*>)?", re.I | re.S)
_HTML_TABLE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.I | re.S)

_NON_BLOCKING_WARNING_PREFIXES = (
    "table_review:",
    "PDF_BOUNDARY_LOW_TEXT_WARNING:",
    "PDF_TEXT_LAYER_IMAGE_PAGE_WARNING:",
)

_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_LANDLINE = re.compile(
    r"(?i)(?P<label>电话|座机|联系电话|tel)\s*[:：]?\s*"
    r"(?P<value>(?:0\d{2,3}[- ]?)?[2-9]\d{6,7}(?:[-转]\d{1,6})?)"
)
_EMAIL = re.compile(r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])")
_WECHAT = re.compile(
    r"(?i)(?P<label>微信(?:号|ID)?|wechat)\s*[:：]?\s*(?P<value>[A-Za-z][-_A-Za-z0-9]{5,19})"
)
_QQ = re.compile(r"(?i)(?P<label>QQ(?:号|ID)?)\s*[:：]?\s*(?P<value>[1-9]\d{4,11})")
_ID_CARD = re.compile(r"(?<![0-9Xx])\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9Xx])")
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_URL = re.compile(r"(?i)(?:https?://|www\.)[^\s<>]+")
_ADDRESS_FIELD = re.compile(r"(?m)^(?P<label>(?:联系|家庭|办公|公司)?地址)\s*[:：]\s*(?P<value>[^\n]{4,100})$")
_FIELD_PREFIX = r"\s*(?:#{1,6}\s*)?(?:[-*+]\s*)?(?:\*\*)?"
_FIELD_SUFFIX = r"(?:\*\*)?\s*[:：]\s*"
_PERSON_FIELD = re.compile(
    rf"(?m)^{_FIELD_PREFIX}(?P<label>客户姓名|姓名|联系人|参会人|参与人|咨询对象|顾问姓名|咨询师|主持人|记录人)"
    rf"{_FIELD_SUFFIX}(?P<value>[^\n]{{1,80}})$"
)
_COMPANY_FIELD = re.compile(
    rf"(?m)^{_FIELD_PREFIX}(?P<label>公司(?:名称)?|企业(?:名称)?|机构(?:名称)?|所在单位|工作单位)"
    rf"{_FIELD_SUFFIX}(?P<value>[^\n]{{2,100}})$"
)
_CONTEXT_COMPANY = re.compile(
    r"(?P<prefix>(?:就职|任职|来自|供职|创办|服务)于?|公司(?:名称)?(?:是|为))\s*[:：]?\s*"
    r"(?P<value>[\u4e00-\u9fffA-Za-z0-9（）()·&]{2,36}(?:有限责任公司|股份有限公司|有限公司|公司|工作室|机构|学校))"
)
_LEGAL_COMPANY_SCAN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9（）()·&]{2,40}(?:有限责任公司|股份有限公司|有限公司)"
)


class OptionalBackendUnavailable(RuntimeError):
    """Raised internally when a lazy optional PDF backend is unavailable."""


@dataclass(slots=True, frozen=True)
class RedactionSummary:
    category: str
    count: int


@dataclass(slots=True, frozen=True)
class PIIFinding:
    """A non-secret residual PII finding; evidence is masked on purpose."""

    category: str
    start: int
    end: int
    masked_evidence: str
    confidence: str = "high"


@dataclass(slots=True)
class PDFPageResult:
    page_number: int
    markdown: str
    backend: str
    text_characters: int
    table_count: int = 0
    warnings: list[str] = field(default_factory=list)
    table_issues: list[dict[str, Any]] = field(default_factory=list)
    # OCR diagnostics are optional for text-layer/PDFPlumber pages.  They are
    # kept on the page rather than emitted as a second document so callers can
    # make an auditable quality decision without exposing source text.
    confidence: float | None = None
    dpi: int | None = None
    ocr_attempts: int = 0
    key_fact_conflicts: list[str] = field(default_factory=list)
    # ``image_count`` is populated by the lightweight pypdf probe.  It is
    # intentionally diagnostic only and is not copied into Dify metadata.
    image_count: int = 0


@dataclass(slots=True)
class _TableCandidate:
    """A private table candidate used while constructing a page layout.

    pdfplumber exposes several table strategies.  Keeping the candidates
    private lets us compare them without expanding the public conversion
    contract or leaking geometric audit data to callers.
    """

    bbox: tuple[float, float, float, float]
    rows: list[list[str]]
    markdown: str
    reliable: bool
    score: float
    strategy: str
    page_width: float
    page_height: float

    @property
    def columns(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def normalized_boundaries(self) -> tuple[float, ...]:
        # Table extraction does not expose the exact column lines uniformly
        # across pdfplumber versions.  Equal-width boundaries are a safe
        # fallback and still allow conservative cross-page comparison.
        count = max(self.columns, 1)
        return tuple(round(index / count, 3) for index in range(count + 1))


@dataclass(slots=True)
class _PageLayout:
    page: PDFPageResult
    tables: list[_TableCandidate] = field(default_factory=list)


@dataclass(slots=True)
class PDFConversionResult:
    """One sanitized, integration-friendly result for one source PDF."""

    document_id: str
    markdown: str
    page_count: int
    source_checksum: str
    backend: str
    status: str
    needs_review: bool
    review_reasons: list[str] = field(default_factory=list)
    pages: list[PDFPageResult] = field(default_factory=list)
    redactions: list[RedactionSummary] = field(default_factory=list)
    residual_pii: list[PIIFinding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    ocr_used: bool = False
    ocr_retry_count: int = 0
    ocr_low_confidence_pages: list[int] = field(default_factory=list)
    ocr_key_fact_conflicts: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)

    def to_dict(self, *, include_page_markdown: bool = False) -> dict[str, Any]:
        """Return a JSON-serializable record safe for a pipeline manifest.

        Page text is omitted by default because it duplicates ``markdown``.
        The original source path and filename are never included.
        """

        record = asdict(self)
        if not include_page_markdown:
            for page in record["pages"]:
                page.pop("markdown", None)
        return record


class OCRBackend(Protocol):
    name: str

    def extract(self, path: Path, *, dpi: int = 200) -> list[PDFPageResult]:
        """Extract all PDF pages in reading order without image blocks."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_offline_ocr_models(
    model_root: str | Path,
    expected_sha256: Mapping[str, str],
) -> dict[str, str]:
    """Validate an offline PaddleOCR model manifest before model loading.

    ``expected_sha256`` is normally loaded from a deployment-controlled
    manifest.  Relative paths are required so a malformed manifest cannot
    make the worker hash arbitrary host files.  The returned map contains
    only relative names and hashes, never model content.
    """

    root = Path(model_root).resolve()
    if not isinstance(expected_sha256, Mapping) or not expected_sha256:
        raise OptionalBackendUnavailable("OCR离线模型清单为空，拒绝启动OCR")
    verified: dict[str, str] = {}
    for relative, expected in expected_sha256.items():
        rel = Path(str(relative))
        if rel.is_absolute() or ".." in rel.parts:
            raise OptionalBackendUnavailable("OCR离线模型清单包含非法路径")
        expected_hash = str(expected).lower().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise OptionalBackendUnavailable(f"OCR离线模型哈希格式无效:{rel.name}")
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            raise OptionalBackendUnavailable("OCR离线模型路径超出模型目录")
        if not target.is_file():
            raise OptionalBackendUnavailable(f"OCR离线模型缺失:{rel.as_posix()}")
        actual = _sha256_file(target)
        if actual != expected_hash:
            raise OptionalBackendUnavailable(f"OCR离线模型哈希不匹配:{rel.as_posix()}")
        verified[rel.as_posix()] = actual
    return verified


def _compact_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _normalize_markdown(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = _MARKDOWN_IMAGE.sub("", text)
    text = _REFERENCE_IMAGE.sub("", text)
    text = _IMAGE_REFERENCE_DEF.sub("", text)
    text = _HTML_IMAGE.sub("", text)
    text = _HTML_TABLE.sub(lambda match: _html_table_to_markdown(match.group(0)), text)
    lines = [_COMPACT_WHITESPACE.sub(" ", line).rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _escape_table_cell(value: Any) -> str:
    cell = "" if value is None else str(value)
    cell = re.sub(r"\s*\n\s*", "<br>", cell.strip())
    return cell.replace("|", r"\|")


def _trim_table(rows: Sequence[Sequence[Any] | None]) -> list[list[str]]:
    normalized = [[_escape_table_cell(cell) for cell in (row or [])] for row in rows]
    normalized = [row for row in normalized if any(cell for cell in row)]
    if not normalized:
        return []
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    keep_columns = [index for index in range(width) if any(row[index] for row in normalized)]
    return [[row[index] for index in keep_columns] for row in normalized]


def _table_reliability_reasons(rows: Sequence[Sequence[Any] | None]) -> list[str]:
    table = _trim_table(rows)
    reasons: list[str] = []
    if len(table) < 2:
        reasons.append("fewer_than_2_rows")
    width = max((len(row) for row in table), default=0)
    if width < 2:
        reasons.append("fewer_than_2_columns")
    sparse_rows = sum(
        1 for row in table if sum(bool(cell) for cell in row) < 2
    )
    if sparse_rows:
        reasons.append(f"sparse_rows={sparse_rows}")
    return reasons


def table_to_markdown(rows: Sequence[Sequence[Any] | None]) -> tuple[str, bool]:
    """Convert extracted cells to Markdown and report structural reliability."""

    table = _trim_table(rows)
    reliable = not _table_reliability_reasons(rows)
    if not table:
        return "", False
    if not reliable:
        # Preserve uncertain cell text without inventing a table structure.
        return "\n".join("；".join(cell for cell in row if cell) for row in table), False
    separator = ["---"] * len(table[0])
    lines = [table[0], separator, *table[1:]]
    return "\n".join("| " + " | ".join(row) + " |" for row in lines), True


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None:
            if self._row is not None:
                self._row.append(html.unescape("".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _html_table_to_markdown(source: str) -> str:
    parser = _TableHTMLParser()
    try:
        parser.feed(source)
    except Exception:
        return re.sub(r"<[^>]+>", " ", source)
    markdown, _ = table_to_markdown(parser.rows)
    return f"\n\n{markdown}\n\n" if markdown else ""


def _extract_pypdf_pages(reader: PdfReader) -> list[PDFPageResult]:
    pages: list[PDFPageResult] = []
    for number, page in enumerate(reader.pages, start=1):
        warnings: list[str] = []
        try:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except (TypeError, ValueError):
                text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            warnings.append(f"pypdf_page_error:{type(exc).__name__}")
        text = _normalize_markdown(text)
        try:
            image_count = len(list(getattr(page, "images", ()) or ()))
        except Exception:
            image_count = 0
        pages.append(
            PDFPageResult(
                page_number=number,
                markdown=text,
                backend="pypdf",
                text_characters=_compact_length(text),
                warnings=warnings,
                image_count=image_count,
            )
        )
    return pages


def _point_inside_bbox(x: float, y: float, bbox: Sequence[float]) -> bool:
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def _join_words(words: Sequence[Mapping[str, Any]]) -> str:
    values = [str(word.get("text") or "").strip() for word in words]
    values = [value for value in values if value]
    result = ""
    for value in values:
        result = _layout_core._join_tokens(result, value)
    return result


def _words_to_line_events(words: Sequence[Mapping[str, Any]], tolerance: float = 3.0) -> list[tuple[float, float, str]]:
    rows: list[list[Mapping[str, Any]]] = []
    row_tops: list[float] = []
    for word in sorted(words, key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0)))):
        top = float(word.get("top", 0))
        if not rows or abs(top - row_tops[-1]) > tolerance:
            rows.append([word])
            row_tops.append(top)
        else:
            rows[-1].append(word)
    events: list[tuple[float, float, str]] = []
    for top, row in zip(row_tops, rows):
        row = sorted(row, key=lambda item: float(item.get("x0", 0)))
        events.append((top, float(row[0].get("x0", 0)), _join_words(row)))
    return events


_TABLE_SETTINGS_CANDIDATES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("default", {}),
    ("snap5", {"snap_tolerance": 5, "join_tolerance": 5}),
    ("snap8", {"snap_tolerance": 8, "join_tolerance": 8}),
    (
        "text_lines",
        {"vertical_strategy": "text", "horizontal_strategy": "lines"},
    ),
    (
        "text_text",
        {"vertical_strategy": "text", "horizontal_strategy": "text"},
    ),
)


def _table_rows_for_candidate(rows: Sequence[Sequence[Any] | None]) -> list[list[str]]:
    """Normalize table rows once so scoring and Markdown share the same cells."""

    return _trim_table(rows)


def _score_table_candidate(
    rows: Sequence[Sequence[str]],
    bbox: Sequence[float],
    *,
    page_width: float,
    page_height: float,
    reliable: bool,
) -> float:
    if not rows:
        return -100.0
    width = max(len(row) for row in rows)
    nonempty = sum(bool(cell) for row in rows for cell in row)
    total = max(len(rows) * width, 1)
    density = nonempty / total
    stable = sum(len(row) == width for row in rows) / max(len(rows), 1)
    area = max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))
    page_area = max(page_width * page_height, 1.0)
    # Padding/phantom columns lower stability and density.  Reliability is a
    # useful signal but never enough to select a candidate by itself.
    return (
        (2.0 if reliable else 0.0)
        + min(len(rows), 12) * 0.12
        + min(width, 12) * 0.15
        + density * 2.0
        + stable * 2.0
        + min(area / page_area, 0.8)
    )


def _table_bbox_equal(left: Sequence[float], right: Sequence[float]) -> bool:
    return all(abs(float(a) - float(b)) <= 1.5 for a, b in zip(left, right))


def _collect_table_candidates(
    page: Any,
    warnings: list[str],
) -> list[_TableCandidate]:
    page_width = float(getattr(page, "width", 1) or 1)
    page_height = float(getattr(page, "height", 1) or 1)
    candidates: list[_TableCandidate] = []
    for strategy, settings in _TABLE_SETTINGS_CANDIDATES:
        try:
            if settings:
                raw_tables = list(page.find_tables(table_settings=settings) or [])
            else:
                raw_tables = list(page.find_tables() or [])
        except TypeError:
            # Lightweight fakes and old pdfplumber versions may not accept
            # table_settings.  The default strategy remains useful.
            if settings:
                continue
            try:
                raw_tables = list(page.find_tables() or [])
            except Exception as exc:
                warnings.append(f"table_detection_error:{type(exc).__name__}")
                continue
        except Exception as exc:
            warnings.append(f"table_detection_error:{type(exc).__name__}")
            continue
        for table in raw_tables:
            try:
                bbox = tuple(float(value) for value in table.bbox)
                rows = _table_rows_for_candidate(table.extract() or [])
                markdown, reliable = table_to_markdown(rows)
            except Exception as exc:
                warnings.append(f"table_extraction_error:{type(exc).__name__}")
                continue
            if not markdown:
                continue
            # pdfplumber's text strategy can model an ordinary single-column
            # page as a table (one cell per line).  Masking such a candidate
            # would suppress heading recovery and disturb reading order.  A
            # short, genuinely single-column table is still retained for
            # linearization; long page-spanning candidates are discarded.
            if len(rows) > 8 and max((len(row) for row in rows), default=0) <= 1:
                continue
            if any(_table_bbox_equal(bbox, existing.bbox) for existing in candidates):
                # Keep the highest scoring variant for identical geometry.
                existing = next(item for item in candidates if _table_bbox_equal(bbox, item.bbox))
                score = _score_table_candidate(
                    rows,
                    bbox,
                    page_width=page_width,
                    page_height=page_height,
                    reliable=reliable,
                )
                if score > existing.score:
                    candidates.remove(existing)
                else:
                    continue
            candidates.append(
                _TableCandidate(
                    bbox=bbox,
                    rows=rows,
                    markdown=markdown,
                    reliable=reliable,
                    score=_score_table_candidate(
                        rows,
                        bbox,
                        page_width=page_width,
                        page_height=page_height,
                        reliable=reliable,
                    ),
                    strategy=strategy,
                    page_width=page_width,
                    page_height=page_height,
                )
            )
    # Do not keep overlapping candidates from different detections: choose
    # the higher-scoring geometry and retain independent adjacent tables.
    selected: list[_TableCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        overlap = False
        for existing in selected:
            x_overlap = max(0.0, min(candidate.bbox[2], existing.bbox[2]) - max(candidate.bbox[0], existing.bbox[0]))
            y_overlap = max(0.0, min(candidate.bbox[3], existing.bbox[3]) - max(candidate.bbox[1], existing.bbox[1]))
            area = max((candidate.bbox[2] - candidate.bbox[0]) * (candidate.bbox[3] - candidate.bbox[1]), 1.0)
            if x_overlap * y_overlap / area >= 0.55:
                overlap = True
                break
        if not overlap:
            selected.append(candidate)
    return sorted(selected, key=lambda item: (item.bbox[1], item.bbox[0]))


def _word_line_blocks(words: Sequence[Mapping[str, Any]], tolerance: float = 3.0) -> list[dict[str, Any]]:
    """Group words into ordered lines while retaining heading evidence."""

    rows: list[list[Mapping[str, Any]]] = []
    tops: list[float] = []
    for word in sorted(words, key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0)))):
        top = float(word.get("top", 0))
        if not rows or abs(top - tops[-1]) > tolerance:
            rows.append([word])
            tops.append(top)
        else:
            rows[-1].append(word)
    blocks: list[dict[str, Any]] = []
    for top, row in zip(tops, rows):
        row = sorted(row, key=lambda item: float(item.get("x0", 0)))
        text = _join_words(row).strip()
        if not text:
            continue
        # pdfplumber's ``extract_words`` does not expose ``size`` on all
        # versions.  Character height is a stable proxy for font size and is
        # sufficient for document-level heading statistics.
        sizes = [
            float(item.get("size") or item.get("height") or 0)
            for item in row
            if item.get("size") or item.get("height")
        ]
        names = [str(item.get("fontname") or "").lower() for item in row]
        x0 = min(float(item.get("x0", 0)) for item in row)
        x1 = max(float(item.get("x1", item.get("x0", 0))) for item in row)
        blocks.append(
            {
                "top": top,
                "x0": x0,
                "x1": x1,
                "text": text,
                "size": sum(sizes) / len(sizes) if sizes else 0.0,
                "bold": any("bold" in name or "black" in name or "demi" in name for name in names),
                "words": row,
            }
        )
    return blocks


def _heading_level(
    block: Mapping[str, Any],
    *,
    body_size: float,
    largest_size: float,
    page_width: float,
) -> int | None:
    text = str(block.get("text") or "").strip()
    if not text or len(text) > 80 or text.startswith(("|", "- ", "* ", "+ ")):
        return None
    if len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text)) < 2:
        return None
    size = float(block.get("size") or 0)
    numbered = bool(re.match(r"^(?:第\s*[0-9一二三四五六七八九十百]+[章节部分篇]|[0-9]{1,2}[.、)]|[一二三四五六七八九十]+[、.])", text))
    line_width = max(0.0, float(block.get("x1", 0)) - float(block.get("x0", 0)))
    centered = (
        line_width <= page_width * 0.62
        and abs(((float(block.get("x0", 0)) + float(block.get("x1", 0))) / 2) - page_width / 2)
        <= page_width * 0.05
    )
    emphatic = bool(block.get("bold")) and len(text) <= 55
    larger = size > body_size * 1.12 if body_size else False
    if not (numbered or centered or emphatic or larger):
        return None
    # A numbered body/list item is not a heading merely because it starts
    # with ``1、``.  Require an independent layout signal (larger type,
    # centering or bold) before promoting it.
    if numbered and not (centered or emphatic or larger):
        return None
    if not (numbered or emphatic or larger):
        return None
    if size and largest_size and size >= largest_size * 0.96:
        return 1
    if size and body_size and size >= body_size * 1.28:
        return 2
    if numbered and len(text) <= 60:
        return 2 if body_size and size >= body_size * 1.05 else 3
    if emphatic:
        return 2 if size >= body_size * 1.08 else 3
    return None


def _recover_headings(blocks: Sequence[Mapping[str, Any]], *, page_width: float) -> list[str]:
    if not blocks:
        return []
    sizes: list[float] = []
    for block in blocks:
        size = float(block.get("size") or 0)
        count = max(len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", str(block.get("text") or ""))), 1)
        if size:
            sizes.extend([size] * min(count, 120))
    if not sizes:
        return [str(block.get("text") or "") for block in blocks]
    counts = Counter(round(size, 1) for size in sizes)
    body_size = float(max(counts, key=counts.get))
    largest_size = max(counts)
    headings: list[str] = []
    h1_seen = False
    output: list[str] = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        level = _heading_level(
            block,
            body_size=body_size,
            largest_size=largest_size,
            page_width=page_width,
        )
        if level is not None:
            if level == 1 and h1_seen:
                level = 2
            if level == 1:
                h1_seen = True
            headings.append(text)
            output.append("#" * level + " " + text)
        else:
            output.append(text)
    return output


def _remove_repeated_heading_lines(lines: Sequence[str]) -> list[str]:
    counts = Counter(line.strip() for line in lines if line.strip())
    repeated = {line for line, count in counts.items() if count >= 3 and len(line) <= 100}
    return [line for line in lines if line.strip() not in repeated]


def _cross_page_table_compatible(previous: _TableCandidate, current: _TableCandidate) -> bool:
    if previous.columns < 2 or current.columns < 2:
        return False
    if abs(previous.bbox[2] - current.bbox[2]) > previous.page_width * 0.08:
        return False
    if abs(previous.bbox[0] - current.bbox[0]) > previous.page_width * 0.08:
        return False
    if previous.columns != current.columns:
        return False
    # Both tables must touch the page boundaries; this rejects adjacent
    # independent tables in the middle of two pages.
    if previous.bbox[3] < previous.page_height * 0.62:
        return False
    if current.bbox[1] > current.page_height * 0.38:
        return False
    return True


def _merge_cross_page_tables(layouts: Sequence[_PageLayout]) -> None:
    """Merge only geometrically compatible, adjacent table fragments."""

    for index in range(1, len(layouts)):
        previous_layout, current_layout = layouts[index - 1], layouts[index]
        if not previous_layout.tables or not current_layout.tables:
            continue
        previous = previous_layout.tables[-1]
        current = current_layout.tables[0]
        if not _cross_page_table_compatible(previous, current):
            continue
        previous_rows = [list(row) for row in previous.rows]
        current_rows = [list(row) for row in current.rows]
        if not current_rows:
            continue
        # A repeated header is removed only when all cells match; a partial
        # match remains as ordinary data to avoid losing text.
        if previous_rows and current_rows[0] == previous_rows[0]:
            current_rows = current_rows[1:]
        elif previous_rows and current_rows[0] and not current_rows[0][0]:
            tail = previous_rows[-1]
            head = current_rows.pop(0)
            if len(tail) == len(head) and all(not head[idx] or tail[idx] for idx in range(len(head))):
                previous_rows[-1] = [
                    (f"{tail[idx]} {head[idx]}".strip() if head[idx] else tail[idx])
                    for idx in range(len(tail))
                ]
        if current_rows:
            previous_rows.extend(current_rows)
        merged_markdown, reliable = table_to_markdown(previous_rows)
        if not merged_markdown:
            continue
        previous_page = previous_layout.page
        current_page = current_layout.page
        previous_page.markdown = previous_page.markdown.replace(previous.markdown, merged_markdown, 1)
        current_page.markdown = current_page.markdown.replace(current.markdown, "", 1)
        previous_page.table_count = max(previous_page.table_count, int(reliable))
        current_page.table_count = max(0, current_page.table_count - 1)
        if not reliable:
            table_index = current_layout.tables.index(current) + 1
            issue = {
                "table_index": table_index,
                "action": "demoted_plain_text",
                "rows": len(previous_rows),
                "columns": max((len(row) for row in previous_rows), default=0),
                "reason": "cross_page_structure_not_reliably_aligned",
            }
            current_page.table_issues.append(issue)
            current_page.warnings.append(
                "table_review:跨页表格结构未能唯一确认，已保守线性化:"
                f"table={table_index}"
            )


def _extract_pdfplumber_pages(path: Path) -> list[PDFPageResult]:
    try:
        pdfplumber = importlib.import_module("pdfplumber")
    except ImportError as exc:
        raise OptionalBackendUnavailable("pdfplumber 未安装，无法验证和结构化提取数字PDF表格") from exc

    # The diagnostic CLI and the formal text-PDF path must use the same
    # deterministic layout core.  Keep the page-result adapter here so the
    # production converter still exposes its existing audit contract.
    with pdfplumber.open(str(path)) as pdf:
        markdown, details = _layout_core._text_pdf_to_markdown(pdf)
        if not markdown.strip():
            return []
        warnings = [str(value) for value in details.get("warnings", [])]
        return [
            PDFPageResult(
                page_number=1,
                markdown=_normalize_markdown(markdown),
                backend="pdfplumber_shared_core",
                text_characters=_compact_length(markdown),
                table_count=int(details.get("table_count") or 0),
                warnings=warnings,
            )
        ]


def _looks_like_unparsed_table(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    tableish = 0
    for line in lines:
        columns = re.split(r"\s{2,}|\t+", line.strip())
        if len([column for column in columns if column]) >= 3:
            tableish += 1
            if tableish >= 2:
                return True
        else:
            tableish = 0
    return False


class PaddleOCRPPStructureV3Backend:
    """Lazy PaddleOCR 3.x OCR/layout backend.

    Formula, chart, seal, and image regions are intentionally disabled or
    ignored.  Text, headings, lists, and tables remain in reading order.
    """

    name = "paddleocr_ppstructurev3"

    _IGNORED_LABELS = [
        "number",
        "footnote",
        "header",
        "header_image",
        "footer",
        "footer_image",
        "aside_text",
        "image",
        "figure",
        "figure_title",
        "chart",
    ]

    def __init__(
        self,
        *,
        device: str | None = None,
        render_dpi: int = 200,
        retry_dpi: int = 300,
        model_root: str | Path | None = None,
        model_manifest: Mapping[str, str] | None = None,
    ) -> None:
        self.device = device
        self.render_dpi = render_dpi
        self.retry_dpi = retry_dpi
        self.model_root = Path(model_root) if model_root is not None else None
        self.model_manifest = dict(model_manifest or {})

    def extract(self, path: Path, *, dpi: int = 200) -> list[PDFPageResult]:
        if self.model_root is not None or self.model_manifest:
            if self.model_root is None:
                raise OptionalBackendUnavailable("OCR离线模型目录未配置")
            validate_offline_ocr_models(self.model_root, self.model_manifest)
        try:
            paddleocr = importlib.import_module("paddleocr")
            pipeline_class = getattr(paddleocr, "PPStructureV3")
        except (ImportError, AttributeError) as exc:
            raise OptionalBackendUnavailable(
                "PaddleOCR PPStructureV3 未安装；扫描PDF已转入review，未生成伪正文"
            ) from exc

        options: dict[str, Any] = {
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
            "use_seal_recognition": False,
            "use_table_recognition": True,
            "use_formula_recognition": False,
            "use_chart_recognition": False,
            "markdown_ignore_labels": self._IGNORED_LABELS,
        }
        if self.device:
            options["device"] = self.device
        pipeline = pipeline_class(**options)
        predict_options = {
            "input": str(path),
            "use_seal_recognition": False,
            "use_table_recognition": True,
            "use_formula_recognition": False,
            "use_chart_recognition": False,
            "markdown_ignore_labels": self._IGNORED_LABELS,
        }
        # PaddleOCR versions differ on whether PDF rasterization DPI is
        # accepted by ``predict``.  Prefer the explicit option and fall back
        # only for older versions; the requested DPI remains in diagnostics.
        try:
            output = pipeline.predict(**predict_options, pdf_dpi=dpi)
        except TypeError as exc:
            if "pdf_dpi" not in str(exc):
                raise
            output = pipeline.predict(**predict_options)

        pages: list[PDFPageResult] = []
        for index, result in enumerate(output, start=1):
            markdown_info = getattr(result, "markdown", {}) or {}
            if isinstance(markdown_info, Mapping):
                markdown = str(markdown_info.get("markdown_texts") or "")
            else:
                markdown = str(markdown_info)
            markdown = _normalize_markdown(markdown)
            table_count = markdown.count("| ---")
            warnings: list[str] = []
            if not markdown:
                warnings.append("ocr_page_empty")
            confidence = _extract_ocr_confidence(result)
            conflicts = _extract_key_fact_conflicts(result)
            if conflicts:
                warnings.extend(f"key_fact_conflict:{item}" for item in conflicts)
            pages.append(
                PDFPageResult(
                    page_number=index,
                    markdown=markdown,
                    backend=self.name,
                    text_characters=_compact_length(markdown),
                    table_count=table_count,
                    warnings=warnings,
                    confidence=confidence,
                    dpi=dpi,
                    ocr_attempts=1,
                    key_fact_conflicts=conflicts,
                )
            )
        return pages


def _result_payload(result: Any) -> Any:
    """Best-effort conversion of a Paddle result to inspectable data."""

    for name in ("json", "res", "result", "to_dict"):
        value = getattr(result, name, None)
        if value is None:
            continue
        try:
            value = value() if callable(value) else value
        except Exception:
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                continue
        if value is not None:
            return value
    return result


def _walk_values(value: Any, *, keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in keys:
                found.append(child)
            found.extend(_walk_values(child, keys=keys))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_walk_values(child, keys=keys))
    return found


def _extract_ocr_confidence(result: Any) -> float | None:
    payload = _result_payload(result)
    values = _walk_values(
        payload,
        keys={"confidence", "score", "rec_score", "text_score", "rec_scores"},
    )
    numbers: list[float] = []
    for value in values:
        if isinstance(value, (int, float)):
            numbers.append(float(value))
        elif isinstance(value, (list, tuple)):
            numbers.extend(float(item) for item in value if isinstance(item, (int, float)))
    if not numbers:
        return None
    normalized = [number / 100 if number > 1 else number for number in numbers]
    normalized = [max(0.0, min(1.0, number)) for number in normalized]
    return min(normalized)


def _extract_key_fact_conflicts(result: Any) -> list[str]:
    payload = _result_payload(result)
    values = _walk_values(
        payload,
        keys={"key_fact_conflicts", "key_fact_conflict", "fact_conflicts"},
    )
    conflicts: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            conflicts.append(value.strip()[:300])
        elif isinstance(value, (list, tuple)):
            conflicts.extend(str(item).strip()[:300] for item in value if str(item).strip())
    return list(dict.fromkeys(conflicts))


def _text_layer_missing(pages: Sequence[PDFPageResult]) -> bool:
    """Return whether any non-blank page lacks a usable text layer.

    This helper remains for source compatibility with callers that used the
    old sampling heuristic.  Conversion routing now relies on the file-level
    ``_classify_pdf_type`` result instead of sampling a handful of pages.
    """

    nonblank = [page for page in pages if page.markdown.strip() or page.image_count]
    if not nonblank:
        return True
    return any(page.text_characters < 40 for page in nonblank)


def _boundary_low_text_pages(pages: Sequence[PDFPageResult]) -> list[int]:
    """Return image-free low-text pages eligible for the boundary exception."""

    page_count = len(pages)
    if page_count < 2:
        return []
    return [
        page.page_number
        for page in pages
        if page.page_number in {1, page_count}
        and page.image_count == 0
        and 0 < page.text_characters < 40
    ]


def _classify_pdf_type(pages: Sequence[PDFPageResult]) -> str:
    """Classify a PDF with a text-layer-first policy.

    Native text is the primary source even when a page also contains an
    illustration or a rendered diagram.  An image-only page remains a hard
    mixed-file review gate so that the text backend cannot silently drop a
    page which would require OCR.  Short image-free cover/trailer pages keep
    the historical boundary exception; other low-text pages stay review-only.
    """

    if not pages:
        # Preserve the legacy empty-probe route; conversion still blocks later
        # if OCR cannot produce non-empty Markdown.
        return "scan"

    kinds: list[str] = []
    for page in pages:
        if page.text_characters >= 40:
            kinds.append("text")
        elif page.image_count > 0 and page.text_characters == 0:
            kinds.append("scan")
        elif page.image_count > 0:
            # A low-text page containing an image is ambiguous: it may be a
            # scan, a rendered cover, or a genuinely mixed page.  Keep the
            # strict review gate instead of treating it as text.
            kinds.append("uncertain")
        elif page.image_count == 0 and page.text_characters > 0:
            # A short, image-free text layer can be a cover/trailer.  A truly
            # blank page is kept distinct and remains review-only in a text
            # document (while an all-blank pypdf file keeps the OCR route).
            kinds.append("low")
        else:
            kinds.append("blank")

    if all(kind == "scan" for kind in kinds):
        return "scan"
    if any(kind == "scan" for kind in kinds):
        return "review"
    if all(kind == "text" for kind in kinds):
        return "text"

    low_pages = [
        page
        for page, kind in zip(pages, kinds)
        if kind in {"low", "uncertain"}
    ]
    blank_pages = [page for page, kind in zip(pages, kinds) if kind == "blank"]
    text_pages = [page for page, kind in zip(pages, kinds) if kind == "text"]
    if not text_pages:
        # Preserve the historical OCR route for an entirely blank pypdf probe:
        # a PDF with no text or image resources is indistinguishable from a
        # raster scan at this stage. Synthetic/fake backends remain uncertain.
        if (
            all(kind == "blank" for kind in kinds)
            and all(page.backend == "pypdf" and page.image_count == 0 for page in pages)
        ):
            return "scan"
        return "review"
    if blank_pages:
        return "review"

    page_count = len(pages)
    if all(
        page.page_number in {1, page_count} and page.image_count == 0
        for page in low_pages
    ):
        return "text"

    # A page with an embedded image and a short native text layer is common in
    # slide decks and illustrated manuals.  Do not send such a document to OCR
    # or block it merely because the image resource is present: when every
    # page has some native text and the file has enough text overall, the
    # native layer is the complete source of record.  Image-only pages were
    # handled above and still require review.
    image_low_pages = [
        page for page, kind in zip(pages, kinds)
        if kind == "uncertain" and page.image_count > 0 and page.text_characters > 0
    ]
    if image_low_pages and all(page.text_characters > 0 for page in pages):
        native_chars = sum(page.text_characters for page in pages)
        if native_chars >= 200 and any(page.text_characters >= 40 for page in pages):
            return "text"
    return "review"


def _remove_repeated_marginal_lines(pages: Sequence[PDFPageResult]) -> None:
    if len(pages) < 3:
        return
    counts: Counter[str] = Counter()
    for page in pages:
        lines = [line.strip() for line in page.markdown.splitlines() if line.strip()]
        candidates = set(lines[:2] + lines[-2:])
        counts.update(line for line in candidates if 2 <= len(line) <= 100 and not line.startswith("|"))
    threshold = max(3, (len(pages) + 2) // 3)
    repeated = {line for line, count in counts.items() if count >= threshold}
    if not repeated:
        return
    for page in pages:
        page.markdown = "\n".join(
            line for line in page.markdown.splitlines() if line.strip() not in repeated
        ).strip()
        page.text_characters = _compact_length(page.markdown)


def _page_document(pages: Sequence[PDFPageResult]) -> str:
    parts: list[str] = []
    for page in pages:
        if not page.markdown:
            continue
        # Comment markers aid audits but do not become visible knowledge text.
        parts.append(f"<!-- page:{page.page_number} -->\n\n{page.markdown}")
    return "\n\n".join(parts).strip()


def _filename_entity_candidates(path: Path) -> tuple[str, str]:
    stem = re.sub(r"[_｜|\-]*\d{4}[-_]?\d{2}[-_]?\d{2}.*$", "", path.stem).strip()
    parts = re.split(r"\s*(?:｜|\||I|_|-|—)\s*", stem, maxsplit=1)
    candidate = parts[0].strip(" _-｜|") if len(parts) > 1 else ""
    person = candidate if re.fullmatch(r"[\u4e00-\u9fff·]{2,12}", candidate) else ""
    organization = ""
    if len(parts) > 1:
        organization = re.sub(
            r"(?:私董|合伙人|战略|商业|会议|项目)*(?:咨询|诊断)(?:总结|纪要|报告)?[_｜|\-]*$",
            "",
            parts[1].strip(),
        ).strip(" _-｜|")
        if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9（）()·&]{2,30}", organization):
            organization = ""
    return person, organization


def _entity_tokens(value: str) -> list[str]:
    value = re.split(r"[；;。]", value, maxsplit=1)[0]
    tokens = re.split(r"[、,，/和与]\s*|\s{2,}", value)
    ignored = {"客户", "用户", "案例客户", "顾问", "老师", "主持人", "记录人", "匿名参与者"}
    result: list[str] = []
    for token in tokens:
        token = re.sub(r"\s*[（(](?:顾问|咨询师|主持人|记录人|客户|用户|老师)[）)]\s*$", "", token.strip())
        if 1 < len(token) <= 40 and token not in ignored:
            result.append(token)
    return result


def anonymize_consultation(
    text: str,
    *,
    source_path: Path | None = None,
    extra_entities: Mapping[str, str] | None = None,
) -> tuple[str, list[RedactionSummary]]:
    """Remove consultation identities while retaining commercial metrics.

    Entity candidates come from explicit document fields, generic company
    context, the structured filename prefix, and optional caller-provided
    entities.  No sample person or brand name is built into the rules.
    """

    result = text
    replacements: dict[str, str] = {}
    category_counts: Counter[str] = Counter()

    if source_path:
        person, organization = _filename_entity_candidates(source_path)
        if person:
            replacements[person] = "案例客户"
        if organization:
            replacements[organization] = "顾问机构"

    for match in _PERSON_FIELD.finditer(result):
        label = match.group("label")
        role = "匿名顾问" if label in {"顾问姓名", "咨询师", "主持人", "记录人"} else "匿名参与者"
        for token in _entity_tokens(match.group("value")):
            replacements[token] = role

    companies: set[str] = set()
    for match in _COMPANY_FIELD.finditer(result):
        companies.update(_entity_tokens(match.group("value")))
    companies.update(match.group("value") for match in _CONTEXT_COMPANY.finditer(result))
    for company in companies:
        replacements[company] = "某机构"

    for entity, replacement in (extra_entities or {}).items():
        entity = str(entity).strip()
        if len(entity) >= 2:
            replacements[entity] = str(replacement).strip() or "[实体已脱敏]"

    for entity in sorted(replacements, key=len, reverse=True):
        replacement = replacements[entity]
        count = result.count(entity)
        if count:
            result = result.replace(entity, replacement)
            category_counts["实体名称"] += count

    def replace_pattern(pattern: re.Pattern[str], replacement: str, category: str) -> None:
        nonlocal result
        result, count = pattern.subn(replacement, result)
        category_counts[category] += count

    replace_pattern(_PHONE, "[手机号已脱敏]", "联系方式")
    replace_pattern(_EMAIL, "[邮箱已脱敏]", "联系方式")
    result, count = _LANDLINE.subn(lambda match: f"{match.group('label')}：[已脱敏]", result)
    category_counts["联系方式"] += count
    result, count = _WECHAT.subn(lambda match: f"{match.group('label')}：[已脱敏]", result)
    category_counts["联系方式"] += count
    result, count = _QQ.subn(lambda match: f"{match.group('label')}：[已脱敏]", result)
    category_counts["联系方式"] += count
    replace_pattern(_ID_CARD, "[证件号已脱敏]", "证件号")
    replace_pattern(_BANK_CARD, "[银行卡号已脱敏]", "银行卡号")
    replace_pattern(_URL, "[链接已移除]", "链接")
    result, count = _ADDRESS_FIELD.subn(
        lambda match: f"{match.group('label')}：[地址已脱敏]", result
    )
    category_counts["地址"] += count

    # Normalize field values even when an unusual delimiter prevented tokenization.
    result = _PERSON_FIELD.sub(lambda match: f"{match.group('label')}：匿名参与者", result)
    result = _COMPANY_FIELD.sub(lambda match: f"{match.group('label')}：某机构", result)
    result = _CONTEXT_COMPANY.sub(lambda match: f"{match.group('prefix')}某机构", result)
    summaries = [
        RedactionSummary(category=category, count=count)
        for category, count in sorted(category_counts.items())
        if count
    ]
    return result, summaries


def _masked_evidence(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * min(8, len(value) - 4) + value[-2:]


def scan_residual_pii(text: str) -> list[PIIFinding]:
    """Find residual high-signal PII without returning raw evidence."""

    patterns: list[tuple[str, re.Pattern[str], str]] = [
        ("phone", _PHONE, "high"),
        ("landline", _LANDLINE, "high"),
        ("email", _EMAIL, "high"),
        ("wechat", _WECHAT, "high"),
        ("qq", _QQ, "high"),
        ("id_card", _ID_CARD, "high"),
        ("bank_card", _BANK_CARD, "high"),
        ("address_field", _ADDRESS_FIELD, "high"),
        ("person_field", _PERSON_FIELD, "high"),
        ("company_field", _COMPANY_FIELD, "high"),
        ("legal_company", _LEGAL_COMPANY_SCAN, "medium"),
    ]
    findings: list[PIIFinding] = []
    seen: set[tuple[int, int, str]] = set()
    for category, pattern, confidence in patterns:
        for match in pattern.finditer(text):
            value = match.group(0)
            if "已脱敏" in value or "匿名参与者" in value or "某机构" in value:
                continue
            key = (match.start(), match.end(), category)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                PIIFinding(
                    category=category,
                    start=match.start(),
                    end=match.end(),
                    masked_evidence=_masked_evidence(value),
                    confidence=confidence,
                )
            )
    return sorted(findings, key=lambda item: (item.start, item.end, item.category))


def _backend_extract(backend: OCRBackend, path: Path, *, dpi: int) -> list[PDFPageResult]:
    """Call old third-party/fake backends while preferring DPI-aware APIs."""

    try:
        return list(backend.extract(path, dpi=dpi))
    except TypeError as exc:
        # Existing integrations implemented ``extract(path)`` before quality
        # diagnostics were introduced.  Keep them source-compatible; only
        # swallow an argument mismatch, never a backend processing failure.
        if "dpi" not in str(exc):
            raise
        return list(backend.extract(path))  # type: ignore[call-arg]


def _ocr_quality(
    pages: Sequence[PDFPageResult],
    *,
    min_confidence: float,
    auto_confidence: float,
    max_low_conf_ratio: float,
) -> tuple[list[int], list[str]]:
    low_pages: list[int] = []
    missing_confidence_pages: list[int] = []
    conflicts: list[str] = []
    for page in pages:
        if page.confidence is None:
            missing_confidence_pages.append(page.page_number)
            low_pages.append(page.page_number)
        elif page.confidence < min_confidence:
            low_pages.append(page.page_number)
        elif page.confidence < auto_confidence:
            # Scores between the hard floor and the automatic acceptance
            # threshold are retry candidates and cannot be auto-published.
            low_pages.append(page.page_number)
        if not page.markdown.strip() or "ocr_page_empty" in page.warnings:
            low_pages.append(page.page_number)
        conflicts.extend(page.key_fact_conflicts)
        conflicts.extend(
            warning.split(":", 1)[1]
            for warning in page.warnings
            if warning.startswith("key_fact_conflict:") and ":" in warning
        )
    low_pages = sorted(set(low_pages))
    conflicts = list(dict.fromkeys(conflicts))
    if pages and len(low_pages) / len(pages) > max_low_conf_ratio:
        reasons = ["OCR低置信或未达自动门禁页面比例超过阈值"]
    else:
        reasons = []
    if missing_confidence_pages:
        reasons.append(
            "OCR缺少置信度证据:"
            + ",".join(map(str, sorted(set(missing_confidence_pages))))
        )
    if conflicts:
        reasons.append(f"OCR关键事实冲突:{len(conflicts)}处")
    return low_pages, reasons


def _prefer_ocr_pages(
    first: Sequence[PDFPageResult],
    retry: Sequence[PDFPageResult],
) -> list[PDFPageResult]:
    """Choose the retry only when it improves quality, page by page."""

    if not first:
        return list(retry)
    if not retry or len(retry) != len(first):
        return list(first)

    def rank(page: PDFPageResult) -> tuple[int, float, int, int]:
        conflict_penalty = len(page.key_fact_conflicts) + sum(
            warning.startswith("key_fact_conflict:") for warning in page.warnings
        )
        empty_penalty = int(not page.markdown.strip())
        confidence = page.confidence if page.confidence is not None else 0.0
        return (empty_penalty, -confidence, conflict_penalty, -page.text_characters)

    selected: list[PDFPageResult] = []
    for original, candidate in zip(first, retry):
        page = candidate if rank(candidate) < rank(original) else original
        page.ocr_attempts = max(original.ocr_attempts, 1) + int(page is candidate)
        if page is candidate and page.dpi is None:
            page.dpi = 300
        selected.append(page)
    return selected


def _load_ocr_manifest(value: str | Path | Mapping[str, str] | None) -> Mapping[str, str]:
    """Load a deployment-controlled OCR manifest without exposing its content."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    manifest_path = Path(value)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        # Let the backend's normal startup gate turn this into a review result
        # instead of allowing a malformed deployment manifest to crash the
        # cleaning task before its transaction can be marked safely.
        return {}
    files = payload.get("files") if isinstance(payload, Mapping) else None
    if not isinstance(files, Mapping):
        return {}
    return {str(key): str(value) for key, value in files.items()}


def convert_pdf(
    path: str | Path,
    *,
    consultation: bool = False,
    enable_ocr: bool = True,
    ocr_backend_name: str = "rapidocr_onnxruntime",
    ocr_backend: OCRBackend | None = None,
    extra_entities: Mapping[str, str] | None = None,
    ocr_max_pages: int = 50,
    ocr_render_dpi: int = 200,
    ocr_retry_dpi: int = 300,
    ocr_min_confidence: float = 0.75,
    ocr_auto_confidence: float = 0.90,
    ocr_max_low_conf_ratio: float = 0.10,
    ocr_cpu_threads: int = 2,
    ocr_model_root: str | Path | None = Path("var/ocr-models/rapidocr"),
    ocr_model_manifest: str | Path | Mapping[str, str] | None = Path(
        "var/ocr-models/rapidocr/manifest.json"
    ),
) -> PDFConversionResult:
    """Convert one PDF into one Markdown document plus review diagnostics.

    For consultation sources, ``consultation=True`` anonymizes the output and
    blocks ``ready`` status whenever residual PII remains.  If a scan-only PDF
    cannot run the configured OCR backend, the result contains no invented
    placeholder body and is explicitly marked ``review``.  Scanned PDFs are
    limited by ``ocr_max_pages``; text-layer PDFs are not, and mixed or
    uncertain page types are rejected for manual review rather than processed
    by two backends.
    """

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if not 1 <= ocr_max_pages <= 50:
        raise ValueError("ocr_max_pages 必须在 1-50 之间")
    if not 72 <= ocr_render_dpi <= 600 or not 72 <= ocr_retry_dpi <= 600:
        raise ValueError("OCR DPI 必须在 72-600 之间")
    if ocr_retry_dpi < ocr_render_dpi:
        raise ValueError("ocr_retry_dpi 不能低于 ocr_render_dpi")
    if (
        not 0 <= ocr_min_confidence <= 1
        or not 0 <= ocr_auto_confidence <= 1
        or not 0 <= ocr_max_low_conf_ratio <= 1
    ):
        raise ValueError("OCR 质量阈值必须在 0-1 之间")
    if ocr_min_confidence > ocr_auto_confidence:
        raise ValueError("ocr_min_confidence 不能高于 ocr_auto_confidence")
    checksum = _sha256_file(source)
    document_id = ("case_" if consultation else "pdf_") + checksum[:16]
    review_reasons: list[str] = []
    quality_flags: list[str] = []
    ocr_used = False
    ocr_retry_count = 0
    ocr_low_confidence_pages: list[int] = []
    ocr_key_fact_conflicts: list[str] = []

    try:
        reader = PdfReader(str(source))
        pypdf_pages = _extract_pypdf_pages(reader)
    except Exception as exc:
        return PDFConversionResult(
            document_id=document_id,
            markdown="",
            page_count=0,
            source_checksum=checksum,
            backend="pypdf",
            status="review",
            needs_review=True,
            review_reasons=[f"PDF读取失败:{type(exc).__name__}"],
            metadata={
                "doc_id": document_id,
                "source_type": "consultation" if consultation else "pdf",
                "privacy_level": "restricted_source_anonymized_output" if consultation else "public",
            },
        )

    pdf_type = _classify_pdf_type(pypdf_pages)
    if consultation and any(page.image_count > 0 for page in pypdf_pages):
        # Native text is safe to prefer for ordinary knowledge sources, but a
        # consultation image may contain a name, phone number, or other PII
        # that the text layer cannot reveal.  Keep the anonymized Markdown
        # extraction for review, while requiring a privacy check before it can
        # be published.
        quality_flags.append("pdf_consultation_image_privacy_review")
        review_reasons.append(
            "PDF_CONSULTATION_IMAGE_PRIVACY_REVIEW:咨询PDF含图片，需人工确认图片中无隐私信息"
        )
    boundary_low_text_pages = (
        _boundary_low_text_pages(pypdf_pages) if pdf_type == "text" else []
    )
    if boundary_low_text_pages:
        quality_flags.append("pdf_boundary_low_text")
        review_reasons.append(
            "PDF_BOUNDARY_LOW_TEXT_WARNING:已保留无图片的首页/末页低文字页:"
            + ",".join(map(str, boundary_low_text_pages))
        )
    image_low_text_pages = (
        [
            page.page_number
            for page in pypdf_pages
            if page.image_count > 0 and 0 < page.text_characters < 40
        ]
        if pdf_type == "text"
        else []
    )
    if image_low_text_pages:
        quality_flags.append("pdf_text_layer_image_page_warning")
        page_preview = ",".join(map(str, image_low_text_pages[:20]))
        if len(image_low_text_pages) > 20:
            page_preview += f"…共{len(image_low_text_pages)}页"
        review_reasons.append(
            "PDF_TEXT_LAYER_IMAGE_PAGE_WARNING:文字层优先，含图低文字页仅作质量提示:"
            + page_preview
        )
    if pdf_type == "review":
        pages = []
        backend_name = "pdf_type_review"
        review_reasons.append(
            "PDF_TYPE_REVIEW_REQUIRED:文件包含混合、低文字或无法判定页面，禁止混合处理"
        )
        scanned = False
    else:
        scanned = pdf_type == "scan"
    pages: list[PDFPageResult]
    backend_name: str
    if pdf_type == "review":
        pass
    elif scanned:
        if not enable_ocr:
            pages = []
            backend_name = "ocr_disabled"
            review_reasons.append("PDF缺少可用文字层，OCR已禁用")
        elif len(reader.pages) > ocr_max_pages:
            pages = []
            backend_name = "ocr_page_limit"
            quality_flags.append("ocr_page_limit")
            review_reasons.append(
                "OCR_PAGE_LIMIT_REVIEW:"
                f"扫描型PDF页数超过配置上限:{len(reader.pages)}>{ocr_max_pages}"
            )
        else:
            backend = ocr_backend
            if backend is None:
                if ocr_backend_name in {"rapidocr", "rapidocr_onnxruntime"}:
                    from .rapidocr_backend import RapidOCRBackend

                    backend = RapidOCRBackend(
                        render_dpi=ocr_render_dpi,
                        retry_dpi=ocr_retry_dpi,
                        model_root=ocr_model_root,
                        model_manifest=_load_ocr_manifest(ocr_model_manifest),
                        cpu_threads=ocr_cpu_threads,
                    )
                elif ocr_backend_name in {"paddle", "paddleocr_ppstructurev3"}:
                    # Kept only as a controlled local rollback for old test
                    # fixtures. New deployments never select this backend.
                    backend = PaddleOCRPPStructureV3Backend(
                        render_dpi=ocr_render_dpi,
                        retry_dpi=ocr_retry_dpi,
                    )
                else:
                    raise OptionalBackendUnavailable(
                        f"不支持的OCR后端:{ocr_backend_name}"
                    )
            backend_name = getattr(backend, "name", type(backend).__name__)
            try:
                ocr_used = True
                pages = _backend_extract(backend, source, dpi=ocr_render_dpi)
                for page in pages:
                    page.dpi = page.dpi or ocr_render_dpi
                    page.ocr_attempts = max(page.ocr_attempts, 1)
                low_pages, quality_reasons = _ocr_quality(
                    pages,
                    min_confidence=ocr_min_confidence,
                    auto_confidence=ocr_auto_confidence,
                    max_low_conf_ratio=ocr_max_low_conf_ratio,
                )
                if low_pages or quality_reasons:
                    retry_pages = _backend_extract(backend, source, dpi=ocr_retry_dpi)
                    for page in retry_pages:
                        page.dpi = page.dpi or ocr_retry_dpi
                        page.ocr_attempts = max(page.ocr_attempts, 1)
                    pages = _prefer_ocr_pages(pages, retry_pages)
                    ocr_retry_count = 1
                ocr_low_confidence_pages, quality_reasons = _ocr_quality(
                    pages,
                    min_confidence=ocr_min_confidence,
                    auto_confidence=ocr_auto_confidence,
                    max_low_conf_ratio=ocr_max_low_conf_ratio,
                )
                ocr_key_fact_conflicts = list(
                    dict.fromkeys(
                        conflict
                        for page in pages
                        for conflict in page.key_fact_conflicts
                    )
                )
                if ocr_low_confidence_pages:
                    quality_flags.append("ocr_low_confidence")
                    review_reasons.append(
                        "OCR_LOW_CONFIDENCE_REVIEW:仍存在低置信或空白页面:"
                        + ",".join(map(str, ocr_low_confidence_pages))
                    )
                if ocr_key_fact_conflicts:
                    quality_flags.append("ocr_key_fact_conflict")
                    review_reasons.append(
                        "OCR_KEY_FACT_CONFLICT:"
                        + ",".join(ocr_key_fact_conflicts[:10])
                    )
                if quality_reasons:
                    review_reasons.extend(quality_reasons)
                    if any(reason.startswith("OCR缺少置信度证据") for reason in quality_reasons):
                        quality_flags.append("ocr_confidence_missing")
            except OptionalBackendUnavailable as exc:
                pages = []
                review_reasons.append(str(exc))
            except Exception as exc:
                pages = []
                review_reasons.append(f"OCR执行失败:{type(exc).__name__}")
    else:
        try:
            pages = _extract_pdfplumber_pages(source)
            backend_name = "pdfplumber"
        except OptionalBackendUnavailable:
            pages = pypdf_pages
            backend_name = "pypdf"
            if any(_looks_like_unparsed_table(page.markdown) for page in pages):
                review_reasons.append("检测到疑似表格，但pdfplumber未安装，表格结构需要复核")
        except Exception as exc:
            pages = pypdf_pages
            backend_name = "pypdf"
            review_reasons.append(f"pdfplumber提取失败，已使用pypdf保底:{type(exc).__name__}")

    _remove_repeated_marginal_lines(pages)
    for page in pages:
        review_reasons.extend(warning for warning in page.warnings if warning.startswith("table_review:"))
    markdown = _page_document(pages)
    redactions: list[RedactionSummary] = []
    residual: list[PIIFinding] = []
    if consultation and markdown:
        markdown, redactions = anonymize_consultation(
            markdown,
            source_path=source,
            extra_entities=extra_entities,
        )
        residual = scan_residual_pii(markdown)
        if residual:
            review_reasons.append(f"脱敏后仍检测到{len(residual)}处潜在PII，禁止直接发布")

    if not markdown:
        review_reasons.append("未生成可入库正文")
    review_reasons = list(dict.fromkeys(review_reasons))
    needs_review = bool(
        [
            reason
            for reason in review_reasons
            if not reason.startswith(_NON_BLOCKING_WARNING_PREFIXES)
        ]
    )
    status = "review" if needs_review else "ready"
    metadata = {
        "doc_id": document_id,
        "source_type": "consultation" if consultation else "pdf",
        "privacy_level": "restricted_source_anonymized_output" if consultation else "public",
        "source_checksum": checksum,
        "page_count": len(reader.pages),
        "conversion_backend": backend_name,
        "review_status": status,
        "ocr_used": ocr_used,
        "ocr_retry_count": ocr_retry_count,
        "ocr_backend": backend_name if ocr_used else None,
        "ocr_render_dpi": ocr_render_dpi if ocr_used else None,
        "ocr_retry_dpi": ocr_retry_dpi if ocr_retry_count else None,
        "ocr_min_confidence": ocr_min_confidence if ocr_used else None,
        "ocr_auto_confidence": ocr_auto_confidence if ocr_used else None,
        "ocr_low_confidence_pages": ocr_low_confidence_pages,
        "ocr_key_fact_conflicts": ocr_key_fact_conflicts,
        "quality_flags": quality_flags,
    }
    return PDFConversionResult(
        document_id=document_id,
        markdown=markdown,
        page_count=len(reader.pages),
        source_checksum=checksum,
        backend=backend_name,
        status=status,
        needs_review=needs_review,
        review_reasons=review_reasons,
        pages=pages,
        redactions=redactions,
        residual_pii=residual,
        metadata=metadata,
        ocr_used=ocr_used,
        ocr_retry_count=ocr_retry_count,
        ocr_low_confidence_pages=ocr_low_confidence_pages,
        ocr_key_fact_conflicts=ocr_key_fact_conflicts,
        quality_flags=quality_flags,
    )


def convert_consultation_pdf(
    path: str | Path,
    *,
    enable_ocr: bool = True,
    ocr_backend_name: str = "rapidocr_onnxruntime",
    ocr_backend: OCRBackend | None = None,
    extra_entities: Mapping[str, str] | None = None,
    ocr_max_pages: int = 50,
    ocr_render_dpi: int = 200,
    ocr_retry_dpi: int = 300,
    ocr_min_confidence: float = 0.75,
    ocr_auto_confidence: float = 0.90,
    ocr_max_low_conf_ratio: float = 0.10,
    ocr_cpu_threads: int = 2,
    ocr_model_root: str | Path | None = Path("var/ocr-models/rapidocr"),
    ocr_model_manifest: str | Path | Mapping[str, str] | None = Path(
        "var/ocr-models/rapidocr/manifest.json"
    ),
) -> PDFConversionResult:
    """Privacy-safe convenience wrapper for consultation meeting summaries."""

    return convert_pdf(
        path,
        consultation=True,
        enable_ocr=enable_ocr,
        ocr_backend_name=ocr_backend_name,
        ocr_backend=ocr_backend,
        extra_entities=extra_entities,
        ocr_max_pages=ocr_max_pages,
        ocr_render_dpi=ocr_render_dpi,
        ocr_retry_dpi=ocr_retry_dpi,
        ocr_min_confidence=ocr_min_confidence,
        ocr_auto_confidence=ocr_auto_confidence,
        ocr_max_low_conf_ratio=ocr_max_low_conf_ratio,
        ocr_cpu_threads=ocr_cpu_threads,
        ocr_model_root=ocr_model_root,
        ocr_model_manifest=ocr_model_manifest,
    )


__all__ = [
    "OCRBackend",
    "OptionalBackendUnavailable",
    "PDFConversionResult",
    "PDFPageResult",
    "PIIFinding",
    "PaddleOCRPPStructureV3Backend",
    "RedactionSummary",
    "_extract_key_fact_conflicts",
    "_extract_ocr_confidence",
    "_classify_pdf_type",
    "anonymize_consultation",
    "convert_consultation_pdf",
    "convert_pdf",
    "scan_residual_pii",
    "table_to_markdown",
    "validate_offline_ocr_models",
]
