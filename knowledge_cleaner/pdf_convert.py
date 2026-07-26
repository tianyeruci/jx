"""PDF-to-Markdown conversion and consultation privacy safeguards.

The public entry point is :func:`convert_pdf`.  It deliberately returns one
``PDFConversionResult`` for one PDF; page objects are diagnostics and are not
separate knowledge-base documents.

``pypdf`` is the always-available text-layer probe/fallback.  ``pdfplumber``
and PaddleOCR's PP-StructureV3 are imported lazily so batch jobs can route a
source to review instead of crashing when an optional backend is unavailable.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from pypdf import PdfReader


_COMPACT_WHITESPACE = re.compile(r"[ \t\f\v]+")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^\n)]*\)")
_REFERENCE_IMAGE = re.compile(r"(?m)^\s*!\[[^\]]*\]\s*\[[^\]]+\]\s*$")
_IMAGE_REFERENCE_DEF = re.compile(r"(?mi)^\s*\[[^\]]+\]:\s*\S+\s*$")
_HTML_IMAGE = re.compile(r"<\s*(?:img|figure)\b[^>]*>(?:.*?</\s*figure\s*>)?", re.I | re.S)
_HTML_TABLE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.I | re.S)

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

    def extract(self, path: Path) -> list[PDFPageResult]:
        """Extract all PDF pages in reading order without image blocks."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def table_to_markdown(rows: Sequence[Sequence[Any] | None]) -> tuple[str, bool]:
    """Convert extracted cells to Markdown and report structural reliability."""

    table = _trim_table(rows)
    reliable = (
        len(table) >= 2
        and len(table[0]) >= 2
        and all(len(row) == len(table[0]) for row in table)
        and all(sum(bool(cell) for cell in row) >= 2 for row in table)
    )
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
        pages.append(
            PDFPageResult(
                page_number=number,
                markdown=text,
                backend="pypdf",
                text_characters=_compact_length(text),
                warnings=warnings,
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
        if not result:
            result = value
        elif re.search(r"[\u3400-\u9fff]$", result) and re.match(r"[\u3400-\u9fff]", value):
            result += value
        else:
            result += " " + value
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


def _extract_pdfplumber_pages(path: Path) -> list[PDFPageResult]:
    try:
        pdfplumber = importlib.import_module("pdfplumber")
    except ImportError as exc:
        raise OptionalBackendUnavailable("pdfplumber 未安装，无法验证和结构化提取数字PDF表格") from exc

    results: list[PDFPageResult] = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            warnings: list[str] = []
            try:
                tables = list(page.find_tables())
            except Exception as exc:
                tables = []
                warnings.append(f"table_detection_error:{type(exc).__name__}")

            bboxes: list[tuple[float, float, float, float]] = []
            table_events: list[tuple[float, float, str]] = []
            reliable_count = 0
            for table in tables:
                bbox = tuple(float(value) for value in table.bbox)
                if any(
                    abs(bbox[0] - existing[0]) < 1
                    and abs(bbox[1] - existing[1]) < 1
                    and abs(bbox[2] - existing[2]) < 1
                    and abs(bbox[3] - existing[3]) < 1
                    for existing in bboxes
                ):
                    continue
                bboxes.append(bbox)
                try:
                    markdown, reliable = table_to_markdown(table.extract() or [])
                except Exception as exc:
                    markdown, reliable = "", False
                    warnings.append(f"table_extraction_error:{type(exc).__name__}")
                if markdown:
                    table_events.append((bbox[1], bbox[0], markdown))
                if reliable:
                    reliable_count += 1
                else:
                    warnings.append("table_review:表格结构无法可靠对齐，已保留线性单元格文本")

            try:
                words = list(page.extract_words(use_text_flow=True, keep_blank_chars=False) or [])
            except Exception as exc:
                words = []
                warnings.append(f"word_extraction_error:{type(exc).__name__}")
            outside_words = [
                word
                for word in words
                if not any(
                    _point_inside_bbox(
                        (float(word.get("x0", 0)) + float(word.get("x1", 0))) / 2,
                        (float(word.get("top", 0)) + float(word.get("bottom", 0))) / 2,
                        bbox,
                    )
                    for bbox in bboxes
                )
            ]
            events = _words_to_line_events(outside_words) + table_events
            markdown = "\n".join(event[2] for event in sorted(events, key=lambda event: (event[0], event[1])))
            markdown = _normalize_markdown(markdown)
            results.append(
                PDFPageResult(
                    page_number=number,
                    markdown=markdown,
                    backend="pdfplumber",
                    text_characters=_compact_length(markdown),
                    table_count=reliable_count,
                    warnings=warnings,
                )
            )
    return results


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

    def __init__(self, *, device: str | None = None) -> None:
        self.device = device

    def extract(self, path: Path) -> list[PDFPageResult]:
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
        output = pipeline.predict(
            input=str(path),
            use_seal_recognition=False,
            use_table_recognition=True,
            use_formula_recognition=False,
            use_chart_recognition=False,
            markdown_ignore_labels=self._IGNORED_LABELS,
        )

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
            pages.append(
                PDFPageResult(
                    page_number=index,
                    markdown=markdown,
                    backend=self.name,
                    text_characters=_compact_length(markdown),
                    table_count=table_count,
                    warnings=warnings,
                )
            )
        return pages


def _text_layer_missing(pages: Sequence[PDFPageResult]) -> bool:
    if not pages:
        return True
    sample = list(pages)
    if len(sample) > 12:
        middle = len(sample) // 2
        sample = sample[:4] + sample[middle - 2 : middle + 2] + sample[-4:]
    readable = sum(page.text_characters >= 40 for page in sample)
    return readable < max(1, len(sample) // 3)


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


def convert_pdf(
    path: str | Path,
    *,
    consultation: bool = False,
    enable_ocr: bool = True,
    ocr_backend: OCRBackend | None = None,
    extra_entities: Mapping[str, str] | None = None,
) -> PDFConversionResult:
    """Convert one PDF into one Markdown document plus review diagnostics.

    For consultation sources, ``consultation=True`` anonymizes the output and
    blocks ``ready`` status whenever residual PII remains.  If a scan-only PDF
    cannot run PP-StructureV3, the result contains no invented placeholder
    body and is explicitly marked ``review``.
    """

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    checksum = _sha256_file(source)
    document_id = ("case_" if consultation else "pdf_") + checksum[:16]
    review_reasons: list[str] = []

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

    scanned = _text_layer_missing(pypdf_pages)
    pages: list[PDFPageResult]
    backend_name: str
    if scanned:
        if not enable_ocr:
            pages = []
            backend_name = "ocr_disabled"
            review_reasons.append("PDF缺少可用文字层，OCR已禁用")
        else:
            backend = ocr_backend or PaddleOCRPPStructureV3Backend()
            backend_name = getattr(backend, "name", type(backend).__name__)
            try:
                pages = backend.extract(source)
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
    needs_review = bool(review_reasons)
    status = "review" if needs_review else "ready"
    metadata = {
        "doc_id": document_id,
        "source_type": "consultation" if consultation else "pdf",
        "privacy_level": "restricted_source_anonymized_output" if consultation else "public",
        "source_checksum": checksum,
        "page_count": len(reader.pages),
        "conversion_backend": backend_name,
        "review_status": status,
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
    )


def convert_consultation_pdf(
    path: str | Path,
    *,
    enable_ocr: bool = True,
    ocr_backend: OCRBackend | None = None,
    extra_entities: Mapping[str, str] | None = None,
) -> PDFConversionResult:
    """Privacy-safe convenience wrapper for consultation meeting summaries."""

    return convert_pdf(
        path,
        consultation=True,
        enable_ocr=enable_ocr,
        ocr_backend=ocr_backend,
        extra_entities=extra_entities,
    )


__all__ = [
    "OCRBackend",
    "OptionalBackendUnavailable",
    "PDFConversionResult",
    "PDFPageResult",
    "PIIFinding",
    "PaddleOCRPPStructureV3Backend",
    "RedactionSummary",
    "anonymize_consultation",
    "convert_consultation_pdf",
    "convert_pdf",
    "scan_residual_pii",
    "table_to_markdown",
]
