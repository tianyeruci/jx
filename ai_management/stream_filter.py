from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

URL_START = re.compile(r"https?://|www\.", re.IGNORECASE)
URL_END = re.compile(r"\s")
# ``\b`` is Unicode-aware in Python: a Chinese character next to an ASCII
# identifier is considered a word boundary only on neither side.  Public
# answers commonly place IDs directly after Chinese text, so use explicit
# ASCII adjacency guards instead of Unicode word boundaries.
_INTERNAL_ID_TOKEN = r"(?:course|route|stage)[_-][A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*"
INTERNAL_ID = re.compile(
    rf"(?<![A-Za-z0-9_-]){_INTERNAL_ID_TOKEN}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_INTERNAL_FIELD_LABEL = r"(?:(?:路线|阶段|课程)\s*(?:ID|id)|(?:route|stage|course)[_-]id)"
_INTERNAL_PLACEHOLDER = r"(?:[（(【\[]\s*内部标识已隐藏\s*[）)】\]]|内部标识已隐藏)"
_INTERNAL_FIELD_VALUE = rf"(?:{_INTERNAL_ID_TOKEN}|{_INTERNAL_PLACEHOLDER})"
_INTERNAL_FIELD_BODY = (
    rf"{_INTERNAL_FIELD_LABEL}\s*[:：=]\s*"
    rf"[\"'“”]?{_INTERNAL_FIELD_VALUE}[\"'“”]?"
    rf"(?![A-Za-z0-9_-])"
)
_BRACKETED_INTERNAL_FIELDS = re.compile(
    rf"[（(【\[]\s*{_INTERNAL_FIELD_BODY}"
    rf"(?:\s*[,，;；、]\s*{_INTERNAL_FIELD_BODY})*\s*[）)】\]]",
    re.IGNORECASE,
)
_INTERNAL_FIELD = re.compile(_INTERNAL_FIELD_BODY, re.IGNORECASE)
_INTERNAL_PLACEHOLDER_TEXT = re.compile(rf"{_INTERNAL_PLACEHOLDER}")
_PUBLIC_ENTITY_FIELD = re.compile(
    rf"(?P<label>{_INTERNAL_FIELD_LABEL})\s*[:：=]\s*"
    rf"[\"'“”]?(?P<value>{_INTERNAL_FIELD_VALUE})[\"'“”]?"
    rf"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_PUBLIC_ENTITY_PAIR = re.compile(
    rf"(?P<head>(?P<kind>路线|阶段|课程)(?:是|为|：|:)\s*)"
    rf"(?P<opening>[「『【\"“])?"
    rf"(?P<old>[^。！？\n（）(){{}}]{{1,80}}?)"
    rf"(?P<closing>[」』】\"”])?\s*[（(]\s*"
    rf"(?P<label>{_INTERNAL_FIELD_LABEL})\s*[:：=]\s*"
    rf"(?P<value>{_INTERNAL_ID_TOKEN})\s*[）)]",
    re.IGNORECASE,
)
_PUBLIC_LIST_BLOCK = re.compile(
    rf"(?P<block>(?:^[ \t]*\d+[.)、]\s*{_INTERNAL_PLACEHOLDER_TEXT.pattern}"
    rf"[ \t]*(?:\r?\n|$)){{2,}})",
    re.MULTILINE,
)
_PUBLIC_MACHINE_FIELDS = {
    "business_goal": "业务目标",
    "focus_directions": "关注方向",
    "experience_stage": "学习基础",
    "learning_status": "学习进度",
    "learning_route": "学习路线",
    "course_cards": "课程推荐",
}
_UNAVAILABLE_LABELS = {
    "路线": "该路线当前暂不可用",
    "阶段": "该阶段当前暂不可用",
    "课程": "该课程当前暂不可用",
}
_PUBLIC_SEPARATOR = "，,、；;"
_PUBLIC_END_PUNCTUATION = "。.!！？?"


def _clean_removed_internal_punctuation(value: str) -> str:
    result = re.sub(
        rf"[{_PUBLIC_SEPARATOR}]\s*(?=[{_PUBLIC_SEPARATOR}])",
        "",
        value,
    )
    return re.sub(
        rf"[{_PUBLIC_SEPARATOR}]\s*(?=[{_PUBLIC_END_PUNCTUATION}])",
        "",
        result,
    )


def redact_internal_ids(value: str, replacement: str = "") -> str:
    """Remove internal course, route and stage references from public text."""

    result = str(value or "")
    result = _BRACKETED_INTERNAL_FIELDS.sub("", result)
    result = _INTERNAL_FIELD.sub("", result)
    result = _INTERNAL_PLACEHOLDER_TEXT.sub("", result)
    return _clean_removed_internal_punctuation(INTERNAL_ID.sub(replacement, result))


def _entity_kind(value: str) -> str:
    normalized = str(value or "").lower().replace("_", "-")
    if "路线" in normalized or normalized.startswith("route-"):
        return "路线"
    if "阶段" in normalized or normalized.startswith("stage-"):
        return "阶段"
    return "课程"


def _catalog_name(names: Mapping[str, str], identifier: str) -> str:
    if identifier in names:
        return str(names[identifier])
    lowered = identifier.casefold()
    for key, value in names.items():
        if str(key).casefold() == lowered:
            return str(value)
    return ""


def _fallback_name(
    fallback_names: Mapping[str, Sequence[str]],
    kind: str,
    index: int = 0,
) -> str:
    values = fallback_names.get({"课程": "course", "路线": "route", "阶段": "stage"}[kind], ())
    return str(values[index]) if index < len(values) else ""


def _replace_public_list_block(
    match: re.Match[str],
    fallback_names: Mapping[str, Sequence[str]],
) -> str:
    course_names = list(fallback_names.get("course", ()))
    index = 0

    def replace_item(item: re.Match[str]) -> str:
        nonlocal index
        name = course_names[index] if index < len(course_names) else _UNAVAILABLE_LABELS["课程"]
        index += 1
        return f"{item.group('prefix')}{name}{item.group('tail')}"

    return re.sub(
        rf"(?m)^(?P<prefix>[ \t]*\d+[.)、]\s*){_INTERNAL_PLACEHOLDER_TEXT.pattern}"
        rf"(?P<tail>[ \t]*(?:\r?\n|$))",
        replace_item,
        match.group("block"),
    )


def publicize_internal_references(
    value: str,
    catalog_names: Mapping[str, str] | None = None,
    fallback_names: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Render approved catalog names while keeping machine IDs out of prose.

    ``catalog_names`` is built from the authoritative course snapshot.  The
    optional fallbacks recover legacy Dify placeholder output when the model
    has already replaced an ID with ``内部标识已隐藏`` before the service sees
    the text.  This function is for public prose only; structured recommendation
    fields must keep their IDs for validation and signed-card generation.
    """

    names = {str(key): str(item) for key, item in (catalog_names or {}).items() if str(item)}
    fallbacks = fallback_names or {}
    result = str(value or "")

    def replace_pair(match: re.Match[str]) -> str:
        identifier = match.group("value")
        official = _catalog_name(names, identifier)
        if not official:
            return match.group(0)
        opening = match.group("opening") or ""
        closing = match.group("closing") or ("」" if opening else "")
        return f"{match.group('head')}{opening}{official}{closing}"

    result = _PUBLIC_ENTITY_PAIR.sub(replace_pair, result)

    def replace_field(match: re.Match[str]) -> str:
        label = match.group("label")
        kind = _entity_kind(label)
        raw_value = match.group("value")
        if _INTERNAL_PLACEHOLDER_TEXT.fullmatch(raw_value):
            official = _fallback_name(fallbacks, kind)
        else:
            official = _catalog_name(names, raw_value)
        return f"{kind}：{official or _UNAVAILABLE_LABELS[kind]}"

    result = _PUBLIC_ENTITY_FIELD.sub(replace_field, result)
    if fallbacks:
        result = _PUBLIC_LIST_BLOCK.sub(
            lambda match: _replace_public_list_block(match, fallbacks),
            result,
        )

    def replace_bare(match: re.Match[str]) -> str:
        identifier = match.group(0)
        return _catalog_name(names, identifier) or _UNAVAILABLE_LABELS[_entity_kind(identifier)]

    result = INTERNAL_ID.sub(replace_bare, result)
    result = _INTERNAL_PLACEHOLDER_TEXT.sub(
        lambda _match: _fallback_name(fallbacks, "课程") or "",
        result,
    )
    for machine_field, public_name in _PUBLIC_MACHINE_FIELDS.items():
        result = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(machine_field)}(?![A-Za-z0-9_])", public_name, result
        )
    result = _clean_removed_internal_punctuation(result)
    result = re.sub(r"(?m)^\s*\d+[.)、]\s*(?:\r?\n|$)", "", result)
    return result


def redact_internal_ids_in_value(value: Any) -> Any:
    """Apply public internal-reference filtering recursively without changing shape."""

    if isinstance(value, str):
        return redact_internal_ids(value)
    if isinstance(value, list):
        return [redact_internal_ids_in_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_internal_ids_in_value(item) for item in value)
    if isinstance(value, dict):
        return {str(key): redact_internal_ids_in_value(item) for key, item in value.items()}
    return value


def publicize_internal_references_in_value(
    value: Any,
    catalog_names: Mapping[str, str] | None = None,
    fallback_names: Mapping[str, Sequence[str]] | None = None,
) -> Any:
    """Map visible resource strings without changing structured field shape."""

    if isinstance(value, str):
        return publicize_internal_references(value, catalog_names, fallback_names)
    if isinstance(value, list):
        return [
            publicize_internal_references_in_value(item, catalog_names, fallback_names)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            publicize_internal_references_in_value(item, catalog_names, fallback_names)
            for item in value
        )
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {"course_id", "route_id", "stage_id"}:
                result[key_text] = redact_internal_ids_in_value(item)
            else:
                result[key_text] = publicize_internal_references_in_value(
                    item,
                    catalog_names,
                    fallback_names,
                )
        return result
    return value


class StreamingUrlRedactor:
    """Remove model-generated URLs without waiting for the full streamed answer."""

    def __init__(self, replacement: str = "（链接请使用课程卡片或引用）") -> None:
        self.replacement = replacement
        self.pending = ""
        self.suppressing = False
        self.replacement_emitted = False

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self.pending += text
        output: list[str] = []
        while self.pending:
            if self.suppressing:
                match = URL_END.search(self.pending)
                if match is None:
                    self.pending = ""
                    break
                if not self.replacement_emitted:
                    output.append(self.replacement)
                output.append(self.pending[match.start() : match.end()])
                self.pending = self.pending[match.end() :]
                self.suppressing = False
                self.replacement_emitted = False
                continue

            match = URL_START.search(self.pending)
            if match is not None:
                output.append(self.pending[: match.start()])
                self.pending = self.pending[match.end() :]
                self.suppressing = True
                continue

            # Keep enough tail to detect a URL prefix split across SSE chunks.
            keep = min(8, len(self.pending))
            if len(self.pending) <= keep:
                break
            output.append(self.pending[:-keep])
            self.pending = self.pending[-keep:]
        return "".join(output)

    def finish(self) -> str:
        if self.suppressing:
            result = "" if self.replacement_emitted else self.replacement
        else:
            result = self.pending
        self.pending = ""
        self.suppressing = False
        self.replacement_emitted = False
        return result


class StreamingTermRedactor:
    """Replace protected terms even when a match spans multiple SSE chunks."""

    def __init__(self, replacements: Mapping[str, str]) -> None:
        self.replacements = {
            str(term): str(replacement) for term, replacement in replacements.items() if str(term)
        }
        self.terms = sorted(
            self.replacements,
            key=lambda item: (-len(item), item),
        )
        self.maximum_length = max((len(item) for item in self.terms), default=1)
        self.pending = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self.pending += text
        output: list[str] = []
        while self.pending:
            matches = [
                (self.pending.find(term), -len(term), term)
                for term in self.terms
                if term in self.pending
            ]
            if matches:
                position, _, term = min(matches)
                output.append(self.pending[:position])
                output.append(self.replacements[term])
                self.pending = self.pending[position + len(term) :]
                continue
            keep = self.maximum_length - 1
            if len(self.pending) <= keep:
                break
            if keep:
                output.append(self.pending[:-keep])
                self.pending = self.pending[-keep:]
            else:
                output.append(self.pending)
                self.pending = ""
        return "".join(output)

    def finish(self) -> str:
        result = self.pending
        self.pending = ""
        return result


class StreamingInternalIdRedactor:
    """Remove internal course, route and stage references from model prose."""

    _TAIL = 128

    def __init__(self, replacement: str = "") -> None:
        self.replacement = replacement
        self.pending = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self.pending += text
        output: list[str] = []
        while True:
            matches = [
                pattern.search(self.pending)
                for pattern in (
                    _BRACKETED_INTERNAL_FIELDS,
                    _INTERNAL_FIELD,
                    _INTERNAL_PLACEHOLDER_TEXT,
                    INTERNAL_ID,
                )
            ]
            matches = [match for match in matches if match is not None]
            match = min(matches, key=lambda item: item.start()) if matches else None
            if match is None:
                if len(self.pending) <= self._TAIL:
                    break
                output.append(self.pending[: -self._TAIL])
                self.pending = self.pending[-self._TAIL :]
                break
            output.append(self.pending[: match.start()])
            output.append(self.replacement)
            self.pending = self.pending[match.end() :]
        return "".join(output)

    def finish(self) -> str:
        result = redact_internal_ids(self.pending, replacement=self.replacement)
        self.pending = ""
        return result


class StreamingPublicRedactor:
    """Apply identity/source-name redaction and URL removal as one stream."""

    def __init__(
        self,
        replacements: Mapping[str, str],
        *,
        catalog_names: Mapping[str, str] | None = None,
        fallback_names: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.terms = StreamingTermRedactor(replacements)
        self.catalog_names = dict(catalog_names or {})
        self.fallback_names = dict(fallback_names or {})
        self.catalog_pending = ""
        self.internal_ids = StreamingInternalIdRedactor()
        self.urls = StreamingUrlRedactor()

    def set_fallback_names(self, fallback_names: Mapping[str, Sequence[str]] | None) -> None:
        self.fallback_names = dict(fallback_names or {})

    def feed(self, text: str) -> str:
        if self.catalog_names:
            self.catalog_pending += self.terms.feed(text)
            return ""
        return self.urls.feed(self.internal_ids.feed(self.terms.feed(text)))

    def finish(self) -> str:
        if self.catalog_names:
            self.catalog_pending += self.terms.finish()
            raw = self.catalog_pending
            self.catalog_pending = ""
            public = publicize_internal_references(
                raw,
                self.catalog_names,
                self.fallback_names,
            )
            url_redactor = StreamingUrlRedactor()
            return url_redactor.feed(public) + url_redactor.finish()
        result = self.urls.feed(self.internal_ids.feed(self.terms.finish()))
        result += self.urls.feed(self.internal_ids.finish())
        return result + self.urls.finish()


class StreamingExactPrefixDeduper:
    """Remove one exact prefix from the beginning of a streamed answer.

    The prefix is compared after any leading whitespace in the answer.  Input
    is buffered only until the comparison is decided, so a prefix split across
    SSE chunks is handled without changing a non-matching answer.  Repeated
    text occurring after the beginning is intentionally left untouched.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = str(prefix or "")
        self.pending = ""
        self.decided = not bool(self.prefix)

    def feed(self, text: str) -> str:
        if not text:
            return ""
        if self.decided:
            return text

        self.pending += text
        candidate = self.pending.lstrip()
        if self.prefix.startswith(candidate) and len(candidate) < len(self.prefix):
            # The data received so far can still be the exact prefix.  Keep it
            # until a later SSE chunk disambiguates it.
            return ""
        if candidate.startswith(self.prefix):
            self.decided = True
            remainder_start = len(self.pending) - len(candidate) + len(self.prefix)
            remainder = self.pending[remainder_start:]
            self.pending = ""
            return remainder

        # A mismatch means this is not a duplicated prompt.  Preserve every
        # character, including leading whitespace, exactly as received.
        self.decided = True
        result = self.pending
        self.pending = ""
        return result

    def finish(self) -> str:
        result = self.pending
        self.pending = ""
        self.decided = True
        return result


__all__ = [
    "StreamingPublicRedactor",
    "StreamingExactPrefixDeduper",
    "StreamingInternalIdRedactor",
    "redact_internal_ids",
    "redact_internal_ids_in_value",
    "publicize_internal_references_in_value",
    "StreamingTermRedactor",
    "StreamingUrlRedactor",
    "publicize_internal_references",
]
