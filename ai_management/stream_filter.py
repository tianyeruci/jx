from __future__ import annotations

import re
from collections.abc import Mapping

URL_START = re.compile(r"https?://|www\.", re.IGNORECASE)
URL_END = re.compile(r"\s")
# ``\b`` is Unicode-aware in Python: a Chinese character next to an ASCII
# identifier is considered a word boundary only on neither side.  Public
# answers commonly place IDs directly after Chinese text, so use explicit
# ASCII adjacency guards instead of Unicode word boundaries.
INTERNAL_ID = re.compile(
    r"(?<![A-Za-z0-9_])(?P<kind>course|route)_[A-Za-z0-9-]{8,}(?![A-Za-z0-9_-])"
)


def redact_internal_ids(value: str, replacement: str = "（内部标识已隐藏）") -> str:
    """Remove internal course/route identifiers from non-streamed text."""

    return INTERNAL_ID.sub(replacement, str(value or ""))


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
            str(term): str(replacement)
            for term, replacement in replacements.items()
            if str(term)
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
    """Hide internal course/route identifiers from model-generated prose."""

    _TAIL = 32

    def __init__(self, replacement: str = "（内部标识已隐藏）") -> None:
        self.replacement = replacement
        self.pending = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self.pending += text
        output: list[str] = []
        while True:
            match = INTERNAL_ID.search(self.pending)
            if match is None:
                if len(self.pending) <= self._TAIL:
                    break
                output.append(self.pending[:-self._TAIL])
                self.pending = self.pending[-self._TAIL:]
                break
            output.append(self.pending[: match.start()])
            output.append(self.replacement)
            self.pending = self.pending[match.end() :]
        return "".join(output)

    def finish(self) -> str:
        result = INTERNAL_ID.sub(self.replacement, self.pending)
        self.pending = ""
        return result


class StreamingPublicRedactor:
    """Apply identity/source-name redaction and URL removal as one stream."""

    def __init__(self, replacements: Mapping[str, str]) -> None:
        self.terms = StreamingTermRedactor(replacements)
        self.internal_ids = StreamingInternalIdRedactor()
        self.urls = StreamingUrlRedactor()

    def feed(self, text: str) -> str:
        return self.urls.feed(self.internal_ids.feed(self.terms.feed(text)))

    def finish(self) -> str:
        result = self.urls.feed(self.internal_ids.feed(self.terms.finish()))
        result += self.urls.feed(self.internal_ids.finish())
        return result + self.urls.finish()


__all__ = [
    "StreamingPublicRedactor",
    "StreamingInternalIdRedactor",
    "redact_internal_ids",
    "StreamingTermRedactor",
    "StreamingUrlRedactor",
]
