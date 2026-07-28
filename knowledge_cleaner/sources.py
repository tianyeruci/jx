"""Source-specific parsers for exported public-account and Markdown content.

The parsers in this module deliberately stop at structural cleaning.  They do
not decide whether an article is useful to the business-advisor knowledge base;
that decision belongs to the classifier/review stage.  This separation keeps
the source rules reusable when new exports have the same layout but different
subject matter.

Typical usage::

    items = parse_source(path)
    for item in items:
        record = item.to_dict()

All readers use strict decoding.  A malformed source therefore fails loudly
instead of silently losing characters and producing an incomplete index.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

SourceType = Literal["wechat_public_account", "markdown_post", "markdown_course"]


@dataclass(slots=True)
class ParsedSourceUnit:
    """One atomic document produced by a source adapter.

    Attributes are intentionally serialization-friendly so the object can be
    converted to a manifest record or mapped to ``models.SourceUnit`` without
    making this module depend on the current pipeline model.

    ``sequence`` is a source-local identifier (article number, post position,
    or lesson number), not a globally unique document id. ``warnings`` contains
    recoverable structural anomalies; decoding and missing required boundaries
    raise exceptions instead.
    """

    title: str
    body: str
    source_path: Path
    source_type: SourceType
    sequence: str
    collection: str = ""
    author: str = ""
    published_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready record with ``source_path`` converted to text."""

        record = asdict(self)
        record["source_path"] = str(self.source_path)
        return record


_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]\r\n]*\]\([^\)\r\n]*(?:\([^\)\r\n]*\)[^\)\r\n]*)*\)")
_HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_REFERENCE_IMAGE = re.compile(r"(?m)^\s*!\[[^\]]*\]\[[^\]]*\]\s*$")
_SEPARATOR = re.compile(r"^\s*(?:[-—_=*•·.．…]\s*){3,}$")

# These expressions are only applied to consecutive lines at the beginning or
# end of a unit.  The same words in the middle of an article remain untouched.
_BOUNDARY_CTA = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:[-·\s]*)(?:END|THE END|完|全文完)(?:[-·\s。.!！]*)$",
        r"^(?:点击|长按|扫描|扫码).{0,30}(?:关注|识别|加入|报名|查看|阅读)",
        r"^(?:关注|回复).{0,35}(?:公众号|读书|资料|领取|获取|加入)",
        r"^(?:点赞|点个赞|在看|转发|收藏).{0,25}$",
        r"^(?:阅读原文|原文链接|推荐阅读|往期推荐|往期回顾|猜你喜欢).{0,30}$",
        r"^您已关注.{0,80}(?:免费领取|门票|福利)",
        r"^(?:加我微信|添加微信|进群|加入群聊|报名链接|点击报名).{0,50}$",
        r"^如果你想.{0,120}(?:欢迎加入|点击.{0,20}阅读原文|查看加入方式)",
        r"^[↑⬆]{2,}.{0,40}(?:关注|名片)",
        r"^回复【?.{1,12}】?.{0,80}(?:微信|读书群|领取|获取|加入)",
        r"^点击关注下方账号.{0,100}(?:回复|领取|获取)",
        r"^(?:阿猫|鱼堂主).{0,100}(?:免费领|扫码加入|加.{0,10}小号)",
        r"^(?:作者[丨|｜].{0,30}[丨|｜].{0,30}|作者简介[：:].*)$",
    )
)
_BOUNDARY_NAV_START = re.compile(
    r"^(?:你还会喜欢|更多文章|必看文章|推荐阅读|往期推荐|往期回顾)[：:]?\s*$"
)


def _is_boundary_noise(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _SEPARATOR.fullmatch(stripped):
        return True
    # A long paragraph that happens to mention "关注" is正文, not a CTA line.
    if len(stripped) > 240:
        return False
    return any(pattern.search(stripped) for pattern in _BOUNDARY_CTA)


def clean_structural_text(text: str) -> str:
    """Remove transport artifacts, images, and deterministic edge-only CTAs.

    Markdown images and zero-width characters are removed everywhere because
    they carry no searchable text in these exports. CTA removal is deliberately
    restricted to the outer edges of a unit to avoid deleting正文 that discusses
    marketing calls-to-action.
    """

    text = _ZERO_WIDTH.sub("", text)
    text = _HTML_IMAGE.sub("", text)
    text = _MARKDOWN_IMAGE.sub("", text)
    text = _REFERENCE_IMAGE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and _is_boundary_noise(lines[0]):
        lines.pop(0)
    while lines and _is_boundary_noise(lines[-1]):
        lines.pop()
    # Recommendation menus often contain ordinary-looking titles that are not
    # individually CTA lines.  Remove the whole menu only when its explicit
    # heading occurs close to the document tail.
    tail_start = max(0, len(lines) - 40)
    for index in range(tail_start, len(lines)):
        if _BOUNDARY_NAV_START.fullmatch(lines[index].strip()):
            del lines[index:]
            break
    while lines and _is_boundary_noise(lines[-1]):
        lines.pop()
    result = "\n".join(lines)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


_WECHAT_BOUNDARY = re.compile(
    r"(?m)^[ \t\ufeff]*【文章\s*(?P<number>\d+)】(?P<title>[^\r\n]*)\r?\n"
    r"(?P<published>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})[ \t]{2,}"
    r"(?P<account>[^\r\n]+?)\s*\r?$"
)
_ESCAPED_NEWLINE = re.compile(r"\\r\\n|\\n|\\r")
_REGION_PREFIX = re.compile(
    r"^(?:北京|上海|天津|重庆|香港|澳门|台湾|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|内蒙古|广西|西藏|宁夏|新疆|新加坡)(?=\S)"
)


def read_wechat_text(path: Path | str) -> str:
    """Read a public-account TXT export using strict GB18030 decoding."""

    source = Path(path)
    return source.read_bytes().decode("gb18030", errors="strict")


def _unescape_exported_lines(value: str) -> list[str]:
    value = _ESCAPED_NEWLINE.sub("\n", value)
    return [line.strip() for line in value.splitlines()]


def _third_line_body(section: str, published_at: str) -> str:
    """Recover正文 appended to the export's packed third-line page header."""

    section = section.lstrip("\r\n").replace("\r\n", "\n").replace("\r", "\n")
    lines = section.split("\n")
    if not lines:
        return ""
    packed = lines[0]
    timestamp_at = packed.find(published_at)
    if timestamp_at >= 0:
        packed = packed[timestamp_at + len(published_at) :]
        packed = _REGION_PREFIX.sub("", packed)
    recovered = [packed, *lines[1:]]
    return "\n".join(recovered).strip()


def _normalized_comparison(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _choose_wechat_body(embedded_body: str, physical_body: str) -> tuple[str, str, list[str]]:
    """Choose between the two repeated export representations conservatively."""

    embedded = clean_structural_text(embedded_body)
    physical = clean_structural_text(physical_body)
    if not embedded:
        return physical, "third_line_suffix", []
    if not physical:
        return embedded, "escaped_header", []

    left = _normalized_comparison(embedded)
    right = _normalized_comparison(physical)
    similarity = SequenceMatcher(None, left, right, autojunk=False).ratio()
    containment = bool(left and right and (left in right or right in left))
    if containment or similarity >= 0.9:
        if len(right) > len(left):
            return physical, "deduplicated_longer_after", []
        return embedded, "deduplicated_longer_before", []

    shorter_ratio = min(len(left), len(right)) / max(len(left), len(right))
    warnings: list[str] = []
    if min(len(left), len(right)) >= 200 and shorter_ratio >= 0.5:
        warnings.append("body_candidate_conflict")
    # In the established export profile, an escaped multi-line header is the
    # canonical body and a dissimilar physical suffix is a packed preview.
    return embedded, "escaped_header", warnings


def parse_wechat_public_account(path: Path | str) -> list[ParsedSourceUnit]:
    """Split one GB18030 public-account export into individual articles.

    A valid boundary requires both ``【文章N】`` and the immediately following
    ``YYYY-mm-dd HH:MM:SS  account`` line.  Recent exports may store the complete
    article as literal ``\\n`` sequences on the header line; those sequences are
    restored before cleaning.  In older exports,正文 starts after a duplicate web
    header on the third physical line; the suffix following the matching
    timestamp is recovered without relying on a particular account or title.

    Raises:
        UnicodeDecodeError: if the source is not valid GB18030.
        ValueError: if no structurally valid article boundary is found.
    """

    source = Path(path)
    text = read_wechat_text(source)
    boundaries = list(_WECHAT_BOUNDARY.finditer(text))
    if not boundaries:
        raise ValueError(f"No valid WeChat article boundaries found in {source}")

    units: list[ParsedSourceUnit] = []
    for index, match in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        exported_lines = _unescape_exported_lines(match.group("title"))
        nonempty_exported = [line for line in exported_lines if line]
        title = nonempty_exported[0] if nonempty_exported else f"文章{match.group('number')}"
        embedded_body = "\n".join(exported_lines[1:]).strip()
        physical_body = _third_line_body(text[match.end() : end], match.group("published"))

        body, body_origin, warnings = _choose_wechat_body(embedded_body, physical_body)
        if not body:
            warnings.append("content_missing")
        if index and int(match.group("number")) == int(boundaries[index - 1].group("number")):
            warnings.append("duplicate_sequence")

        units.append(
            ParsedSourceUnit(
                title=title,
                body=body,
                source_path=source,
                source_type="wechat_public_account",
                sequence=match.group("number"),
                collection=source.stem,
                author=match.group("account").strip(),
                published_at=match.group("published"),
                metadata={
                    "account": match.group("account").strip(),
                    "article_number": int(match.group("number")),
                    "parser_profile": "wechat_numbered_export",
                    "body_origin": body_origin,
                },
                warnings=warnings,
            )
        )
    return units


_ATX_HEADING = re.compile(r"^(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*?))?[ \t]*#?[ \t]*$")
_LESSON_HEADING = re.compile(
    r"^第\s*(?P<number>\d{1,3}|[零〇一二三四五六七八九十百两]+)\s*讲(?:\s*[：:、.．-]\s*|\s+)(?P<title>.*)$"
)
_PSEUDO_H1 = re.compile(r"^(?:[\d０-９]+[、.．/\\_-]*|[、.．/\\_-]+)$")
_MARKDOWN_ESCAPES = re.compile(r"\\([\\`*_{}\[\]()#+.!|~-])")


def read_markdown_text(path: Path | str) -> str:
    """Read UTF-8 Markdown strictly while accepting an optional UTF-8 BOM."""

    return Path(path).read_bytes().decode("utf-8-sig", errors="strict")


def _plain_heading(value: str) -> str:
    value = _ZERO_WIDTH.sub("", value).strip()
    value = _MARKDOWN_ESCAPES.sub(r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def _valid_h1(title: str) -> bool:
    title = _plain_heading(title)
    return bool(title) and not _PSEUDO_H1.fullmatch(title)


def _heading(line: str) -> tuple[int, str] | None:
    match = _ATX_HEADING.match(line)
    if not match:
        return None
    return len(match.group("marks")), _plain_heading(match.group("title") or "")


def _markdown_profile(lines: list[str]) -> Literal["h1_post_export", "hierarchical_course"]:
    lessons = 0
    for line in lines:
        heading = _heading(line)
        if heading and heading[0] == 2 and _LESSON_HEADING.match(heading[1]):
            lessons += 1
    return "hierarchical_course" if lessons >= 2 else "h1_post_export"


def _collection_title(lines: list[str], fallback: str) -> str:
    for line in lines:
        heading = _heading(line)
        if heading and heading[0] == 1 and _valid_h1(heading[1]):
            return heading[1]
    return fallback


def _split_h1_posts(source: Path, lines: list[str], collection: str) -> list[ParsedSourceUnit]:
    boundaries: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines):
        heading = _heading(line)
        if heading and heading[0] == 1 and _valid_h1(heading[1]):
            boundaries.append((line_number, heading[1]))
    if not boundaries:
        return []

    # The first valid H1 names the export. Subsequent H1s are posts. A file with
    # only one H1 is retained as a single-document fallback if it has正文.
    post_boundaries = boundaries[1:] if len(boundaries) > 1 else boundaries
    units: list[ParsedSourceUnit] = []
    for index, (start, title) in enumerate(post_boundaries):
        end = post_boundaries[index + 1][0] if index + 1 < len(post_boundaries) else len(lines)
        body = clean_structural_text("\n".join(lines[start + 1 : end]))
        if not body:
            continue
        units.append(
            ParsedSourceUnit(
                title=title,
                body=body,
                source_path=source,
                source_type="markdown_post",
                sequence=str(index + 1),
                collection=collection,
                metadata={
                    "parser_profile": "h1_post_export",
                    "source_line_start": start + 1,
                    "source_line_end": end,
                },
            )
        )
    return units


def _split_h2_lessons(source: Path, lines: list[str], collection: str) -> list[ParsedSourceUnit]:
    lessons: list[tuple[int, str, str, str]] = []
    section = ""
    for line_number, line in enumerate(lines):
        heading = _heading(line)
        if not heading:
            continue
        level, title = heading
        if level == 1 and title != collection and _valid_h1(title):
            section = title
        elif level == 2:
            lesson = _LESSON_HEADING.match(title)
            if lesson:
                lessons.append((line_number, title, lesson.group("number"), section))

    units: list[ParsedSourceUnit] = []
    for index, (start, title, number, section) in enumerate(lessons):
        end = lessons[index + 1][0] if index + 1 < len(lessons) else len(lines)
        body_lines = lines[start + 1 : end]
        # A following H1 is a section divider for the next lesson, not part of
        # the previous lesson's正文.
        for offset, line in enumerate(body_lines):
            heading = _heading(line)
            if heading and heading[0] == 1 and _valid_h1(heading[1]):
                body_lines = body_lines[:offset]
                break
        body = clean_structural_text("\n".join(body_lines))
        warnings = [] if body else ["content_missing"]
        units.append(
            ParsedSourceUnit(
                title=title,
                body=body,
                source_path=source,
                source_type="markdown_course",
                sequence=number,
                collection=collection,
                metadata={
                    "parser_profile": "hierarchical_course",
                    "section": section,
                    "lesson_number": number,
                    "source_line_start": start + 1,
                    "source_line_end": end,
                },
                warnings=warnings,
            )
        )
    return units


def parse_markdown_posts(path: Path | str) -> list[ParsedSourceUnit]:
    """Parse a Markdown export using its detected structural profile.

    Profiles:

    * ``h1_post_export``: the first valid H1 is the collection and later valid
      H1 headings are individual posts. Empty and numeric-only H1 artifacts are
      ignored as boundaries.
    * ``hierarchical_course``: H1 headings are sections and H2 headings matching
      ``第 N 讲`` are individual lessons. Subheadings remain in lesson正文.

    The profile is selected from heading structure (at least two lesson H2s),
    never from specific filenames or article content.
    """

    source = Path(path)
    text = read_markdown_text(source)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    collection = _collection_title(lines, source.stem)
    profile = _markdown_profile(lines)
    if profile == "hierarchical_course":
        return _split_h2_lessons(source, lines, collection)
    return _split_h1_posts(source, lines, collection)


def parse_source(path: Path | str) -> list[ParsedSourceUnit]:
    """Dispatch a supported source file to the appropriate strict parser."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".txt":
        return parse_wechat_public_account(source)
    if suffix in {".md", ".markdown"}:
        return parse_markdown_posts(source)
    raise ValueError(f"Unsupported source type: {source.suffix or '<no extension>'}")


__all__ = [
    "ParsedSourceUnit",
    "SourceType",
    "clean_structural_text",
    "parse_markdown_posts",
    "parse_source",
    "parse_wechat_public_account",
    "read_markdown_text",
    "read_wechat_text",
]
